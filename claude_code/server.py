"""FastAPI server that exposes Claude via browser automation.

Each API key maps to its own browser tab / Claude chat session.
The server handles the full agentic tool loop internally:
  prompt → Claude → parse tool calls → execute → send results → repeat → return final answer.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Header, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from contextlib import asynccontextmanager

from .chat_pool import ChatPool
from .prompt_builder import build_initial_prompt, build_tool_results_prompt
from .parser import parse_response
from .tools import dispatch_tool

log = logging.getLogger(__name__)

# ------------------------------------------------------------------ #
#  Config (set via env vars or CLI flags at startup)                   #
# ------------------------------------------------------------------ #

_pool: ChatPool | None = None

_ALLOWED_ROOTS: list[str] = []
_WORKING_DIR: str = ""
_MAX_TURNS: int = 40


# ------------------------------------------------------------------ #
#  Request / response models                                          #
# ------------------------------------------------------------------ #

class MessageRequest(BaseModel):
    """Incoming request body — mimics the Anthropic Messages API shape."""
    prompt: str | None = None
    content: str | None = None
    messages: list[dict[str, Any]] | None = None
    file_paths: list[str] = Field(default_factory=list)
    max_turns: int | None = None

    def get_prompt(self) -> str:
        if self.prompt:
            return self.prompt
        if self.content:
            return self.content
        if self.messages:
            for msg in self.messages:
                if msg.get("role") == "user":
                    c = msg.get("content", "")
                    if isinstance(c, list):
                        return " ".join(
                            b.get("text", "") for b in c if b.get("type") == "text"
                        )
                    return str(c)
        return ""


class ToolResultEntry(BaseModel):
    tool_name: str
    tool_input: dict[str, Any]
    result: str


class MessageResponse(BaseModel):
    text: str
    tool_results: list[ToolResultEntry]
    turns: int


# ------------------------------------------------------------------ #
#  App factory                                                        #
# ------------------------------------------------------------------ #

def create_app(
    num_slots: int = 3,
    headless: bool = True,
    browser_profile: str | None = None,
    allowed_roots: list[str] | None = None,
    working_dir: str | None = None,
    max_turns: int = 40,
    rate_limit: int = 20,
    min_delay: float = 2.0,
    max_delay: float = 8.0,
) -> FastAPI:
    global _pool, _ALLOWED_ROOTS, _WORKING_DIR, _MAX_TURNS

    _ALLOWED_ROOTS = [str(Path(r).resolve()) for r in (allowed_roots or [os.getcwd()])]
    _WORKING_DIR = working_dir or _ALLOWED_ROOTS[0]
    _MAX_TURNS = max_turns

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        global _pool
        _pool = ChatPool(
            num_slots=num_slots,
            headless=headless,
            browser_profile=browser_profile,
            rate_limit=rate_limit,
            min_delay=min_delay,
            max_delay=max_delay,
        )
        keys = await _pool.start()

        print("\n" + "=" * 60)
        print("  Claude Code API Server")
        print("=" * 60)
        print(f"\n  {num_slots} chat slot(s) ready. Your API keys:\n")
        for i, key in enumerate(keys, 1):
            print(f"    Slot {i}: {key}")
        print(f"\n  Rate limit: {rate_limit} msgs/hour per slot")
        print(f"  Delay between messages: {min_delay}-{max_delay}s")
        print(f"\n  Usage:")
        print(f'    curl -X POST http://localhost:PORT/v1/messages \\')
        print(f'      -H "Authorization: Bearer <KEY>" \\')
        print(f'      -H "Content-Type: application/json" \\')
        print(f'      -d \'{{"prompt": "your task here", "file_paths": []}}\'')
        print("=" * 60 + "\n")

        yield

        await _pool.stop()

    app = FastAPI(
        title="Claude Code API",
        description="Browser-automated Claude API — no API key from Anthropic needed.",
        lifespan=lifespan,
    )

    # ---- Auth helper ---- #

    def _get_api_key(authorization: str | None) -> str:
        if not authorization:
            raise HTTPException(401, "Missing Authorization header")
        parts = authorization.split()
        if len(parts) == 2 and parts[0].lower() == "bearer":
            return parts[1]
        if len(parts) == 1:
            return parts[0]
        raise HTTPException(401, "Invalid Authorization format. Use 'Bearer <key>'")

    # ---- Routes ---- #

    @app.post("/v1/messages", response_model=MessageResponse)
    async def messages(
        body: MessageRequest,
        authorization: str | None = Header(default=None),
    ):
        """Send a prompt, run the full agentic tool loop, return the result."""
        api_key = _get_api_key(authorization)
        if not _pool or not _pool.validate_key(api_key):
            raise HTTPException(403, "Invalid API key")

        prompt = body.get_prompt()
        if not prompt:
            raise HTTPException(400, "No prompt provided")

        turns_limit = body.max_turns or _MAX_TURNS
        file_paths = body.file_paths

        # Build the initial prompt with system instructions + file context
        outgoing = build_initial_prompt(prompt, file_paths)
        all_tool_results: list[dict[str, Any]] = []
        final_text = ""

        for turn in range(turns_limit):
            # Send to Claude via the browser
            try:
                raw_response = await _pool.send_message(api_key, outgoing)
            except Exception as exc:
                log.error("Browser error on turn %d: %s", turn, exc)
                raise HTTPException(502, f"Browser error: {exc}")

            parsed = parse_response(raw_response)

            if parsed.text:
                final_text = parsed.text

            if not parsed.has_tool_calls:
                break

            # Execute tool calls locally
            turn_results: list[dict[str, Any]] = []
            for tc in parsed.tool_calls:
                result_str = dispatch_tool(
                    tool_name=tc.tool,
                    tool_input=tc.params,
                    allowed_roots=_ALLOWED_ROOTS,
                    working_dir=_WORKING_DIR,
                )
                entry = {
                    "tool_name": tc.tool,
                    "tool_input": tc.params,
                    "result": result_str,
                }
                turn_results.append(entry)
                all_tool_results.append(entry)

            # Build follow-up prompt with results and send again
            outgoing = build_tool_results_prompt(turn_results)

        return MessageResponse(
            text=final_text,
            tool_results=[ToolResultEntry(**r) for r in all_tool_results],
            turns=turn + 1,
        )

    @app.post("/v1/chat/new")
    async def new_chat(authorization: str | None = Header(default=None)):
        """Reset the slot to a fresh Claude conversation."""
        api_key = _get_api_key(authorization)
        if not _pool or not _pool.validate_key(api_key):
            raise HTTPException(403, "Invalid API key")
        await _pool.new_chat(api_key)
        return {"status": "ok", "message": "New chat started"}

    @app.get("/v1/keys")
    async def list_keys():
        """List all API keys and their status (for admin use)."""
        if not _pool:
            return {"keys": []}
        return {"keys": _pool.list_keys()}

    @app.get("/health")
    async def health():
        return {
            "status": "ok",
            "slots": _pool.list_keys() if _pool else [],
        }

    return app
