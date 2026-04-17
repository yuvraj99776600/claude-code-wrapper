"""Async wrapper around the official `claude` CLI (claude-code npm package)."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import AsyncIterator

log = logging.getLogger(__name__)


class ClaudeCliError(RuntimeError):
    """Raised when the claude CLI exits non-zero or emits malformed output."""


class ClaudeSession:
    """One persistent Claude Code conversation, resumed across calls via --resume."""

    def __init__(
        self,
        session_id: str,
        cwd: str | Path | None = None,
        model: str | None = None,
        permission_mode: str = "bypassPermissions",
        claude_bin: str = "claude",
    ) -> None:
        self.session_id = session_id
        self.cwd = str(cwd) if cwd else None
        self.model = model
        self.permission_mode = permission_mode
        self.claude_bin = claude_bin
        self._first_send = True
        self._lock = asyncio.Lock()

    def _build_args(self, prompt: str) -> list[str]:
        args = [
            self.claude_bin,
            "-p",
            prompt,
            "--output-format",
            "stream-json",
            "--verbose",
            "--permission-mode",
            self.permission_mode,
        ]
        if self._first_send:
            args.extend(["--session-id", self.session_id])
        else:
            args.extend(["--resume", self.session_id])
        if self.model:
            args.extend(["--model", self.model])
        return args

    async def stream(self, prompt: str) -> AsyncIterator[dict]:
        """Run claude once with prompt, yielding parsed stream-json events."""
        async with self._lock:
            args = self._build_args(prompt)
            log.debug("claude cli args: %s", args)

            try:
                proc = await asyncio.create_subprocess_exec(
                    *args,
                    cwd=self.cwd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env=os.environ.copy(),
                )
            except FileNotFoundError as e:
                raise ClaudeCliError(
                    f"claude binary not found: {self.claude_bin!r}. "
                    "Install with: npm install -g @anthropic-ai/claude-code"
                ) from e

            stderr_buf: list[bytes] = []

            async def _drain_stderr() -> None:
                assert proc.stderr is not None
                while True:
                    chunk = await proc.stderr.read(4096)
                    if not chunk:
                        break
                    stderr_buf.append(chunk)

            stderr_task = asyncio.create_task(_drain_stderr())

            assert proc.stdout is not None
            try:
                async for raw in proc.stdout:
                    line = raw.decode("utf-8", errors="replace").strip()
                    if not line:
                        continue
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        log.warning("non-json line from claude: %s", line[:200])
                        continue
                    yield event
            finally:
                rc = await proc.wait()
                await stderr_task
                if rc != 0:
                    err = b"".join(stderr_buf).decode("utf-8", errors="replace")
                    raise ClaudeCliError(
                        f"claude exited with code {rc}: {err.strip()[:500]}"
                    )

            self._first_send = False

    async def send(self, prompt: str) -> str:
        """Run prompt and return the final result text."""
        final = ""
        async for event in self.stream(prompt):
            if event.get("type") == "result":
                final = event.get("result", "") or final
        return final
