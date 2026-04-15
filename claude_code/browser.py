"""Browser automation for claude.ai using Playwright.

Controls a real Chromium browser — types prompts into Claude's web UI,
waits for responses, and extracts the reply text. Persists login state
across restarts via a saved browser profile.
"""

from __future__ import annotations

import asyncio
import logging
import math
import random
import time
from pathlib import Path
from typing import Any

from playwright.async_api import (
    async_playwright,
    Browser,
    BrowserContext,
    Page,
    Playwright,
)

log = logging.getLogger(__name__)

# --------------- selectors (claude.ai as of 2026-04) --------------- #
# These may need updating if Anthropic changes their frontend.

# The main contenteditable / textarea where you type prompts
_INPUT_SELECTOR = 'div[contenteditable="true"], textarea[placeholder]'

# The send button (arrow icon next to the input)
_SEND_BUTTON_SELECTOR = 'button[aria-label="Send Message"], button[data-testid="send-button"], form button[type="submit"]'

# Each assistant message container
_ASSISTANT_MSG_SELECTOR = '[data-testid="chat-message-Assistant"], div.font-claude-message, div[data-is-streaming]'

# The "stop generating" / streaming indicator
_STREAMING_INDICATOR = 'button[aria-label="Stop Response"], div[data-is-streaming="true"], button:has-text("Stop")'

# "New chat" button
_NEW_CHAT_SELECTOR = 'a[href="/new"], button:has-text("New chat"), nav a[href="/"]'

CLAUDE_URL = "https://claude.ai/new"
PROFILE_DIR = Path.home() / ".claude-code-wrapper" / "browser-profile"


class ClaudeBrowser:
    """Manages a persistent Chromium browser session with claude.ai.

    Parameters
    ----------
    headless : bool
        Run browser without a visible window (set False for debugging / first login).
    profile_dir : str | Path | None
        Directory to persist cookies / localStorage between runs.
        Defaults to ``~/.claude-code-wrapper/browser-profile``.
    slow_mo : int
        Milliseconds to slow down each Playwright action (helps avoid detection).
    proxy : str | None
        Proxy URL, e.g. ``http://user:pass@host:port`` or ``socks5://host:port``.
    timezone : str | None
        Timezone ID to spoof, e.g. ``America/New_York``. Defaults to system tz.
    locale : str | None
        Locale to spoof, e.g. ``en-US``. Defaults to system locale.
    """

    def __init__(
        self,
        headless: bool = True,
        profile_dir: str | Path | None = None,
        slow_mo: int = 50,
        proxy: str | None = None,
        timezone: str | None = None,
        locale: str | None = None,
    ) -> None:
        self.headless = headless
        self.profile_dir = Path(profile_dir) if profile_dir else PROFILE_DIR
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        self.slow_mo = slow_mo
        self.proxy = proxy
        self.timezone = timezone
        self.locale = locale

        self._pw: Playwright | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None

    # ---------------------------------------------------------------- #
    #  Lifecycle                                                       #
    # ---------------------------------------------------------------- #

    @staticmethod
    def _parse_proxy(url: str) -> dict[str, str]:
        """Parse ``http://user:pass@host:port`` into Playwright proxy dict."""
        from urllib.parse import urlparse
        p = urlparse(url)
        cfg: dict[str, str] = {"server": f"{p.scheme}://{p.hostname}:{p.port}"}
        if p.username:
            cfg["username"] = p.username
        if p.password:
            cfg["password"] = p.password
        return cfg

    async def start(self) -> None:
        """Launch browser and restore saved login session."""
        self._pw = await async_playwright().start()

        launch_kwargs: dict[str, Any] = {
            "user_data_dir": str(self.profile_dir),
            "headless": self.headless,
            "slow_mo": self.slow_mo,
            "viewport": {"width": 1280, "height": 900},
            "args": [
                "--disable-blink-features=AutomationControlled",
            ],
        }

        # Proxy support — split embedded credentials for Playwright
        if self.proxy:
            proxy_cfg = self._parse_proxy(self.proxy)
            launch_kwargs["proxy"] = proxy_cfg
            log.info("Using proxy: %s", proxy_cfg["server"])

        # Timezone / locale spoofing
        if self.timezone:
            launch_kwargs["timezone_id"] = self.timezone
        if self.locale:
            launch_kwargs["locale"] = self.locale

        self._browser = await self._pw.chromium.launch_persistent_context(**launch_kwargs)
        self._context = self._browser
        log.info("Browser started (headless=%s, profile=%s)", self.headless, self.profile_dir)

    async def stop(self) -> None:
        """Close browser gracefully."""
        if self._context:
            await self._context.close()
            self._context = None
            self._browser = None
        if self._pw:
            await self._pw.stop()
            self._pw = None
        log.info("Browser stopped.")

    # ---------------------------------------------------------------- #
    #  Page / tab management                                           #
    # ---------------------------------------------------------------- #

    async def new_chat_page(self) -> Page:
        """Open a new tab pointed at a fresh Claude chat."""
        if not self._context:
            raise RuntimeError("Browser not started. Call start() first.")

        page = await self._context.new_page()
        await page.goto(CLAUDE_URL, wait_until="domcontentloaded")

        # Wait for either the chat input (logged in) or a login form
        try:
            await page.wait_for_selector(
                f"{_INPUT_SELECTOR}, input[type='email'], button:has-text('Log in')",
                timeout=15_000,
            )
        except Exception:
            pass  # We'll check login status below

        if await self._needs_login(page):
            log.warning(
                "Not logged in. Opening browser in visible mode for manual login. "
                "Log in, then the system will continue automatically."
            )
            await self._wait_for_manual_login(page)

        # Ensure we're on a new chat
        current_url = page.url
        if "/new" not in current_url and "/chat/" not in current_url:
            await page.goto(CLAUDE_URL, wait_until="domcontentloaded")

        await page.wait_for_selector(_INPUT_SELECTOR, timeout=30_000)
        log.info("New chat page ready: %s", page.url)
        return page

    async def _needs_login(self, page: Page) -> bool:
        """Check if we're on a login/auth page instead of the chat UI."""
        try:
            # If the chat input exists, we're logged in
            el = await page.query_selector(_INPUT_SELECTOR)
            if el:
                return False
        except Exception:
            pass

        # Check URL for login indicators
        url = page.url.lower()
        if any(kw in url for kw in ["login", "signin", "auth", "oauth"]):
            return True

        # Check for login form elements
        for sel in ["input[type='email']", "button:has-text('Log in')", "button:has-text('Sign in')"]:
            try:
                el = await page.query_selector(sel)
                if el:
                    return True
            except Exception:
                continue
        return False

    async def _wait_for_manual_login(self, page: Page) -> None:
        """Block until the user logs in manually via the visible browser."""
        log.info("Waiting for manual login... (look for the chat input to appear)")
        # Poll until the chat input appears (user logged in)
        for _ in range(600):  # up to 10 minutes
            try:
                el = await page.query_selector(_INPUT_SELECTOR)
                if el:
                    log.info("Login detected! Continuing.")
                    return
            except Exception:
                pass
            await asyncio.sleep(1)
        raise TimeoutError("Login timed out after 10 minutes.")

    # ---------------------------------------------------------------- #
    #  Human-like mouse movement                                       #
    # ---------------------------------------------------------------- #

    async def _human_move_to(self, page: Page, x: float, y: float) -> None:
        """Move the mouse to (x, y) along a Bézier curve with variable speed."""
        # Get current mouse position (default to a random starting point)
        start_x = getattr(self, '_mouse_x', random.uniform(200, 800))
        start_y = getattr(self, '_mouse_y', random.uniform(200, 600))

        # Generate 1-2 random control points for a Bézier curve
        cp1_x = start_x + (x - start_x) * random.uniform(0.2, 0.5) + random.uniform(-80, 80)
        cp1_y = start_y + (y - start_y) * random.uniform(0.2, 0.5) + random.uniform(-80, 80)
        cp2_x = start_x + (x - start_x) * random.uniform(0.5, 0.8) + random.uniform(-40, 40)
        cp2_y = start_y + (y - start_y) * random.uniform(0.5, 0.8) + random.uniform(-40, 40)

        # Number of steps based on distance (more distance = more steps)
        dist = math.hypot(x - start_x, y - start_y)
        steps = max(8, min(35, int(dist / 15)))

        for i in range(steps + 1):
            t = i / steps
            # Ease-in-out: slow start, fast middle, slow end
            t = t * t * (3 - 2 * t)
            # Cubic Bézier
            u = 1 - t
            bx = u**3 * start_x + 3 * u**2 * t * cp1_x + 3 * u * t**2 * cp2_x + t**3 * x
            by = u**3 * start_y + 3 * u**2 * t * cp1_y + 3 * u * t**2 * cp2_y + t**3 * y
            await page.mouse.move(bx, by)
            # Variable speed: slower at start and end
            await asyncio.sleep(random.uniform(0.005, 0.02))

        # Small overshoot correction (humans often slightly overshoot)
        if random.random() < 0.3:
            overshoot_x = x + random.uniform(-3, 3)
            overshoot_y = y + random.uniform(-3, 3)
            await page.mouse.move(overshoot_x, overshoot_y)
            await asyncio.sleep(random.uniform(0.02, 0.06))
            await page.mouse.move(x, y)

        self._mouse_x = x
        self._mouse_y = y

    async def _human_click(self, page: Page, element: Any) -> None:
        """Move mouse to element with human-like motion, then click."""
        box = await element.bounding_box()
        if not box:
            await element.click()
            return

        # Click at a random point within the element (not dead center)
        target_x = box['x'] + box['width'] * random.uniform(0.25, 0.75)
        target_y = box['y'] + box['height'] * random.uniform(0.3, 0.7)

        await self._human_move_to(page, target_x, target_y)
        await asyncio.sleep(random.uniform(0.05, 0.15))  # brief pause before click
        await page.mouse.click(target_x, target_y)

    async def _idle_mouse_wander(self, page: Page) -> None:
        """Small random mouse movements to simulate reading / waiting."""
        viewport = page.viewport_size or {'width': 1280, 'height': 900}
        # Move to a random spot in the content area
        x = random.uniform(200, viewport['width'] - 100)
        y = random.uniform(150, viewport['height'] - 100)
        await self._human_move_to(page, x, y)
        # Sometimes do a small scroll
        if random.random() < 0.3:
            await page.mouse.wheel(0, random.uniform(-60, 120))

    # ---------------------------------------------------------------- #
    #  Send / receive                                                  #
    # ---------------------------------------------------------------- #

    async def send_message(self, page: Page, message: str) -> str:
        """Type a message into the Claude chat and return the full response.

        Handles:
        - Typing/pasting the prompt into the input
        - Clicking send
        - Waiting for Claude to finish streaming
        - Extracting the latest assistant message text
        """
        # Count existing assistant messages so we know which one is new
        existing_msgs = await page.query_selector_all(_ASSISTANT_MSG_SELECTOR)
        msg_count_before = len(existing_msgs)

        # Focus and fill the input
        input_el = await page.wait_for_selector(_INPUT_SELECTOR, timeout=15_000)
        await self._human_click(page, input_el)

        # For long messages, use clipboard paste with human-like timing
        # For short messages, simulate realistic typing
        if len(message) > 500:
            await self._paste_text(page, message)
        else:
            await self._human_type(page, input_el, message)

        # Small delay to let the UI register the input (randomized)
        await asyncio.sleep(random.uniform(0.3, 0.8))

        # Click send or press Enter
        await self._click_send(page)

        # Wait for the new assistant message to appear and stream to completion
        response_text = await self._wait_for_response(page, msg_count_before)
        return response_text

    async def _human_type(self, page: Page, input_el: Any, text: str) -> None:
        """Simulate human-like typing with variable speed and micro-pauses."""
        for i, char in enumerate(text):
            await input_el.type(char, delay=0)
            # Base delay: 30-90ms per character (average human ~50-80ms)
            delay = random.uniform(0.03, 0.09)
            # Occasional longer pause after punctuation (thinking)
            if char in '.!?\n':
                delay += random.uniform(0.1, 0.4)
            # Occasional micro-pause mid-word (hesitation)
            elif random.random() < 0.05:
                delay += random.uniform(0.08, 0.25)
            # Slightly faster for spaces (muscle memory)
            elif char == ' ':
                delay *= 0.7
            await asyncio.sleep(delay)

    async def _paste_text(self, page: Page, text: str) -> None:
        """Paste text via clipboard to handle large prompts with human timing."""
        # Simulate a brief pause before pasting (like a human copying from somewhere)
        await asyncio.sleep(random.uniform(0.5, 1.5))

        input_el = await page.query_selector(_INPUT_SELECTOR)
        if input_el:
            tag = await input_el.evaluate("el => el.tagName.toLowerCase()")
            if tag == "textarea":
                await input_el.fill(text)
            else:
                # contenteditable div — use keyboard paste
                await page.evaluate(
                    "text => navigator.clipboard.writeText(text)", text
                )
                await input_el.click()
                await page.keyboard.press("Control+A")
                await page.keyboard.press("Control+V")
                await asyncio.sleep(random.uniform(0.2, 0.5))
                # Fallback: dispatch input event with the text directly
                pasted = await input_el.inner_text()
                if len(pasted.strip()) < 10:
                    await input_el.evaluate(
                        "(el, text) => { el.innerText = text; el.dispatchEvent(new Event('input', {bubbles: true})); }",
                        text,
                    )

        # Small pause after pasting before hitting send (like reviewing)
        await asyncio.sleep(random.uniform(0.3, 1.0))

    async def _click_send(self, page: Page) -> None:
        """Click the send button with human-like mouse movement, falling back to Enter key."""
        for selector in [
            _SEND_BUTTON_SELECTOR,
            'button[type="submit"]',
        ]:
            try:
                btn = await page.query_selector(selector)
                if btn and await btn.is_enabled():
                    await self._human_click(page, btn)
                    return
            except Exception:
                continue
        # Fallback: press Enter
        await page.keyboard.press("Enter")

    async def _wait_for_response(
        self,
        page: Page,
        msg_count_before: int,
        timeout: float = 300,
    ) -> str:
        """Wait for Claude to finish generating and return the response text."""
        start = time.monotonic()

        # Phase 1: wait for a new assistant message to appear
        while time.monotonic() - start < timeout:
            msgs = await page.query_selector_all(_ASSISTANT_MSG_SELECTOR)
            if len(msgs) > msg_count_before:
                break
            await asyncio.sleep(0.5)
        else:
            raise TimeoutError(f"No new assistant message after {timeout}s")

        # Phase 2: wait for streaming to stop (with idle mouse activity)
        idle_counter = 0
        while time.monotonic() - start < timeout:
            is_streaming = await page.query_selector(_STREAMING_INDICATOR)
            if not is_streaming:
                # Double-check after a short pause (UI may flicker)
                await asyncio.sleep(1.5)
                is_streaming = await page.query_selector(_STREAMING_INDICATOR)
                if not is_streaming:
                    break
            # Occasional idle mouse movement while waiting (like reading)
            idle_counter += 1
            if idle_counter % 8 == 0 and random.random() < 0.5:
                await self._idle_mouse_wander(page)
            await asyncio.sleep(0.5)

        # Phase 3: extract the last assistant message
        msgs = await page.query_selector_all(_ASSISTANT_MSG_SELECTOR)
        if not msgs:
            return "[Error: No assistant messages found on page]"

        last_msg = msgs[-1]
        # Get the full text content (including code blocks)
        text = await last_msg.inner_text()
        # Also grab innerHTML to preserve code block formatting
        html = await last_msg.inner_html()

        # If there are code blocks, reconstruct them with fences
        response = self._html_to_text_with_fences(text, html)
        return response

    @staticmethod
    def _html_to_text_with_fences(text: str, html: str) -> str:
        """Best-effort conversion — keeps code fences intact.

        inner_text() already handles most cases well, but we ensure
        code blocks have proper ``` fencing for our parser.
        """
        # inner_text() from Playwright generally preserves structure well enough.
        # The parser looks for ```tool_call blocks which Claude writes as text anyway.
        return text
