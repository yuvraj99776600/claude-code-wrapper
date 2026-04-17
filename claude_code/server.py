"""OpenAI-compatible FastAPI server backed by Claude Code CLI sessions."""

from __future__ import annotations

import json
import logging
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from .chat_pool import ChatPool
from .claude_cli import ClaudeCliError

log = logging.getLogger(__name__)

TOOL_EMOJI = {
    "Write": "🔧",
    "Edit": "✏️",
    "Read": "📖",
    "Bash": "💻",
    "Grep": "🔍",
    "Glob": "📁",
    "WebFetch": "🌐",
    "WebSearch": "🔎",
    "Task": "🤖",
}


def _extract_prompt(messages: list[dict]) -> str:
    if not messages:
        raise HTTPException(status_code=400, detail="messages is required")

    system_parts: list[str] = []
    for m in messages:
        if m.get("role") == "system":
            c = m.get("content", "")
            if isinstance(c, list):
                t = "".join(p.get("text", "") for p in c if isinstance(p, dict) and p.get("type") == "text")
            else:
                t = str(c)
            if t.strip():
                system_parts.append(t)

    last_user = ""
    for m in reversed(messages):
        if m.get("role") == "user":
            c = m.get("content", "")
            if isinstance(c, list):
                last_user = "".join(p.get("text", "") for p in c if isinstance(p, dict) and p.get("type") == "text")
            else:
                last_user = str(c)
            break

    if system_parts and last_user:
        return "\n\n".join(system_parts) + "\n\n" + last_user
    return last_user or "\n\n".join(system_parts)


def _openai_chunk(
    completion_id: str,
    model: str,
    delta: dict,
    finish_reason: str | None = None,
) -> str:
    payload = {
        "id": completion_id,
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
    }
    return f"data: {json.dumps(payload)}\n\n"


def _tool_use_banner(block: dict) -> str:
    name = block.get("name", "tool")
    emoji = TOOL_EMOJI.get(name, "🛠️")
    inp = block.get("input", {}) or {}
    detail = ""
    if name in ("Write", "Edit", "Read"):
        detail = inp.get("file_path", "")
    elif name == "Bash":
        detail = inp.get("command", "")
    elif name in ("Grep", "Glob"):
        detail = inp.get("pattern", "") or inp.get("query", "")
    elif name in ("WebFetch", "WebSearch"):
        detail = inp.get("url", "") or inp.get("query", "")
    if detail:
        if len(detail) > 120:
            detail = detail[:117] + "..."
        return f"\n{emoji} **{name}** `{detail}`\n"
    return f"\n{emoji} **{name}**\n"


def _translate_event(event: dict) -> list[str]:
    etype = event.get("type")
    out: list[str] = []
    if etype == "assistant":
        msg = event.get("message", {}) or {}
        for block in msg.get("content", []) or []:
            btype = block.get("type")
            if btype == "text":
                t = block.get("text", "")
                if t:
                    out.append(t)
            elif btype == "tool_use":
                out.append(_tool_use_banner(block))
    return out


def create_app(
    num_slots: int = 3,
    cwd: str | Path | None = None,
    model: str | None = None,
    permission_mode: str = "bypassPermissions",
    claude_bin: str = "claude",
) -> FastAPI:
    pool = ChatPool(
        num_slots=num_slots,
        cwd=cwd,
        model=model,
        permission_mode=permission_mode,
        claude_bin=claude_bin,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        keys = pool.start()
        banner = "\n".join(f"  {i+1}. {k}" for i, k in enumerate(keys))
        print(
            "\n" + "=" * 60 + "\n"
            f"Claude Code wrapper ready — {len(keys)} slot(s)\n"
            + "=" * 60 + "\n"
            f"API keys:\n{banner}\n"
            + "=" * 60 + "\n",
            flush=True,
        )
        yield

    app = FastAPI(title="claude-code-wrapper", version="0.4.0", lifespan=lifespan)

    def _auth(request: Request) -> str:
        header = request.headers.get("authorization", "")
        if not header.lower().startswith("bearer "):
            raise HTTPException(status_code=401, detail="missing bearer token")
        key = header.split(" ", 1)[1].strip()
        if not pool.validate_key(key):
            raise HTTPException(status_code=401, detail="invalid api key")
        return key

    @app.get("/health")
    async def health() -> dict:
        return {"status": "ok", "slots": num_slots}

    @app.get("/v1/models")
    async def list_models() -> dict:
        return {
            "object": "list",
            "data": [
                {
                    "id": "claude-code",
                    "object": "model",
                    "created": int(time.time()),
                    "owned_by": "anthropic",
                }
            ],
        }

    @app.get("/v1/keys")
    async def list_keys() -> dict:
        return {"slots": pool.list_keys()}

    @app.post("/v1/chat/new")
    async def new_chat(request: Request) -> dict:
        key = _auth(request)
        pool.reset_session(key)
        return {"status": "ok", "session_id": pool.get_session(key).session_id}

    @app.post("/v1/chat/completions")
    async def chat_completions(request: Request) -> Any:
        key = _auth(request)
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(status_code=400, detail="invalid json body")

        messages = body.get("messages") or []
        stream = bool(body.get("stream", False))
        req_model = body.get("model") or "claude-code"
        prompt = _extract_prompt(messages)
        session = pool.get_session(key)
        completion_id = "chatcmpl-" + uuid.uuid4().hex[:24]

        if stream:
            async def gen() -> AsyncIterator[str]:
                yield _openai_chunk(completion_id, req_model, {"role": "assistant", "content": ""})
                try:
                    async for event in session.stream(prompt):
                        for piece in _translate_event(event):
                            yield _openai_chunk(completion_id, req_model, {"content": piece})
                    pool.mark_sent(key)
                except ClaudeCliError as e:
                    err = f"\n\n[claude-cli error: {e}]"
                    yield _openai_chunk(completion_id, req_model, {"content": err})
                yield _openai_chunk(completion_id, req_model, {}, finish_reason="stop")
                yield "data: [DONE]\n\n"

            return StreamingResponse(gen(), media_type="text/event-stream")

        parts: list[str] = []
        try:
            async for event in session.stream(prompt):
                parts.extend(_translate_event(event))
            pool.mark_sent(key)
        except ClaudeCliError as e:
            raise HTTPException(status_code=502, detail=f"claude cli error: {e}")

        content = "".join(parts)
        return JSONResponse(
            {
                "id": completion_id,
                "object": "chat.completion",
                "created": int(time.time()),
                "model": req_model,
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": content},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            }
        )

    return app
