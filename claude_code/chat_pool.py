"""Chat pool — maps API keys to isolated Claude browser chat sessions.

Each API key gets its own browser tab with a separate Claude conversation.
Requests are serialized per-key so one key can't overlap itself.
"""

from __future__ import annotations

import asyncio
import logging
import random
import secrets
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from playwright.async_api import Page

from .browser import ClaudeBrowser

log = logging.getLogger(__name__)


@dataclass
class ChatSlot:
    """One API key ↔ one Claude chat tab."""
    api_key: str
    page: Page | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    busy: bool = False
    message_count: int = 0
    # Rate limiting: timestamps of recent messages (rolling 1-hour window)
    _send_times: deque = field(default_factory=lambda: deque(maxlen=200))
    # Track when last message was sent for inter-message delay
    _last_send: float = 0.0


class ChatPool:
    """Manage a pool of Claude chat sessions, one per API key.

    Parameters
    ----------
    num_slots : int
        How many API keys / concurrent chat sessions to create.
    headless : bool
        Run Chromium headless (False for first-time login).
    browser_profile : str | None
        Path to persistent browser profile directory.
    rate_limit : int
        Maximum messages per slot per hour (0 = unlimited).
    min_delay : float
        Minimum seconds to wait between messages on the same slot.
    max_delay : float
        Maximum seconds to wait between messages on the same slot.
    proxy : str | None
        Proxy URL for browser traffic (e.g. http://user:pass@host:port).
    timezone : str | None
        Timezone to spoof (e.g. America/New_York).
    locale : str | None
        Locale to spoof (e.g. en-US).
    """

    def __init__(
        self,
        num_slots: int = 3,
        headless: bool = True,
        browser_profile: str | None = None,
        rate_limit: int = 20,
        min_delay: float = 2.0,
        max_delay: float = 8.0,
        proxy: str | None = None,
        timezone: str | None = None,
        locale: str | None = None,
    ) -> None:
        self.num_slots = num_slots
        self.headless = headless
        self.rate_limit = rate_limit
        self.min_delay = min_delay
        self.max_delay = max_delay

        self._browser = ClaudeBrowser(
            headless=headless,
            profile_dir=browser_profile,
            proxy=proxy,
            timezone=timezone,
            locale=locale,
        )
        self._slots: dict[str, ChatSlot] = {}
        self._started = False

    # ---------------------------------------------------------------- #
    #  Lifecycle                                                       #
    # ---------------------------------------------------------------- #

    async def start(self) -> list[str]:
        """Launch the browser and create chat slots.

        Returns the list of generated API keys.
        """
        await self._browser.start()
        keys: list[str] = []

        for i in range(self.num_slots):
            api_key = f"cc-{secrets.token_hex(16)}"
            page = await self._browser.new_chat_page()
            slot = ChatSlot(api_key=api_key, page=page)
            self._slots[api_key] = slot
            keys.append(api_key)
            log.info("Slot %d ready — key: %s...", i + 1, api_key[:12])

        self._started = True
        return keys

    async def stop(self) -> None:
        """Close all tabs and the browser."""
        for slot in self._slots.values():
            if slot.page and not slot.page.is_closed():
                await slot.page.close()
        await self._browser.stop()
        self._slots.clear()
        self._started = False

    # ---------------------------------------------------------------- #
    #  Key management                                                  #
    # ---------------------------------------------------------------- #

    def validate_key(self, api_key: str) -> bool:
        return api_key in self._slots

    def get_slot(self, api_key: str) -> ChatSlot | None:
        return self._slots.get(api_key)

    def list_keys(self) -> list[dict[str, Any]]:
        now = time.monotonic()
        cutoff = now - 3600
        return [
            {
                "api_key": slot.api_key,
                "busy": slot.busy,
                "messages_sent": slot.message_count,
                "messages_last_hour": sum(
                    1 for t in slot._send_times if t > cutoff
                ),
                "rate_limit": self.rate_limit,
            }
            for slot in self._slots.values()
        ]

    # ---------------------------------------------------------------- #
    #  Send a message through a slot                                   #
    # ---------------------------------------------------------------- #

    async def send_message(self, api_key: str, message: str) -> str:
        """Send a message on the slot's Claude chat and return the response.

        Serialized: if the slot is busy, this waits for the lock.
        Applies rate limiting and random inter-message delays.
        """
        slot = self._slots.get(api_key)
        if not slot:
            raise KeyError(f"Unknown API key: {api_key[:12]}...")
        if not slot.page or slot.page.is_closed():
            # Re-open the tab
            slot.page = await self._browser.new_chat_page()

        async with slot.lock:
            # --- Rate limiting: check rolling 1-hour window ---
            if self.rate_limit > 0:
                now = time.monotonic()
                cutoff = now - 3600  # 1 hour ago
                # Remove timestamps older than 1 hour
                while slot._send_times and slot._send_times[0] < cutoff:
                    slot._send_times.popleft()
                if len(slot._send_times) >= self.rate_limit:
                    wait_until = slot._send_times[0] + 3600
                    wait_secs = wait_until - now
                    log.warning(
                        "Slot %s... rate limited. %d msgs in last hour (limit: %d). "
                        "Waiting %.0fs.",
                        api_key[:12], len(slot._send_times),
                        self.rate_limit, wait_secs,
                    )
                    await asyncio.sleep(wait_secs)

            # --- Random inter-message delay ---
            if slot._last_send > 0:
                elapsed = time.monotonic() - slot._last_send
                desired_delay = random.uniform(self.min_delay, self.max_delay)
                if elapsed < desired_delay:
                    wait = desired_delay - elapsed
                    log.debug(
                        "Slot %s... waiting %.1fs before next message.",
                        api_key[:12], wait,
                    )
                    await asyncio.sleep(wait)

            slot.busy = True
            try:
                response = await self._browser.send_message(slot.page, message)
                slot.message_count += 1
                slot._send_times.append(time.monotonic())
                slot._last_send = time.monotonic()
                return response
            finally:
                slot.busy = False

    async def new_chat(self, api_key: str) -> None:
        """Reset a slot to a fresh Claude conversation."""
        slot = self._slots.get(api_key)
        if not slot:
            raise KeyError(f"Unknown API key: {api_key[:12]}...")

        async with slot.lock:
            if slot.page and not slot.page.is_closed():
                await slot.page.close()
            slot.page = await self._browser.new_chat_page()
            slot.message_count = 0
            log.info("Slot %s... reset to new chat.", api_key[:12])
