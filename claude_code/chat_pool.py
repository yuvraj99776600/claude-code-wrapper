"""Pool of Claude Code sessions — each API key maps to one persistent session."""

from __future__ import annotations

import logging
import secrets
import uuid
from dataclasses import dataclass
from pathlib import Path

from .claude_cli import ClaudeSession

log = logging.getLogger(__name__)


def _new_api_key() -> str:
    return "cc-" + secrets.token_hex(16)


@dataclass
class Slot:
    api_key: str
    session: ClaudeSession
    messages_sent: int = 0


class ChatPool:
    """Maps API keys to Claude CLI sessions."""

    def __init__(
        self,
        num_slots: int = 3,
        cwd: str | Path | None = None,
        model: str | None = None,
        permission_mode: str = "bypassPermissions",
        claude_bin: str = "claude",
    ) -> None:
        if num_slots < 1:
            raise ValueError("num_slots must be >= 1")
        self.num_slots = num_slots
        self.cwd = cwd
        self.model = model
        self.permission_mode = permission_mode
        self.claude_bin = claude_bin
        self._slots: dict[str, Slot] = {}

    def start(self) -> list[str]:
        for _ in range(self.num_slots):
            key = _new_api_key()
            session = ClaudeSession(
                session_id=str(uuid.uuid4()),
                cwd=self.cwd,
                model=self.model,
                permission_mode=self.permission_mode,
                claude_bin=self.claude_bin,
            )
            self._slots[key] = Slot(api_key=key, session=session)
        log.info("chat pool ready: %d slot(s)", len(self._slots))
        return list(self._slots.keys())

    def validate_key(self, api_key: str) -> bool:
        return api_key in self._slots

    def get_session(self, api_key: str) -> ClaudeSession:
        return self._slots[api_key].session

    def mark_sent(self, api_key: str) -> None:
        self._slots[api_key].messages_sent += 1

    def reset_session(self, api_key: str) -> None:
        slot = self._slots[api_key]
        slot.session = ClaudeSession(
            session_id=str(uuid.uuid4()),
            cwd=self.cwd,
            model=self.model,
            permission_mode=self.permission_mode,
            claude_bin=self.claude_bin,
        )
        slot.messages_sent = 0

    def list_keys(self) -> list[dict]:
        return [
            {
                "api_key": s.api_key,
                "session_id": s.session.session_id,
                "messages_sent": s.messages_sent,
            }
            for s in self._slots.values()
        ]
