"""Parse Claude's web-UI responses to extract tool calls and plain text."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolCall:
    tool: str
    params: dict[str, Any]


@dataclass
class ParsedResponse:
    """Result of parsing a single Claude response."""
    text: str  # Non-tool-call text (final answer / commentary)
    tool_calls: list[ToolCall] = field(default_factory=list)

    @property
    def has_tool_calls(self) -> bool:
        return len(self.tool_calls) > 0


# Matches ```tool_call ... ``` blocks (with optional language tag on the fence)
_TOOL_CALL_RE = re.compile(
    r"```tool_call\s*\n(.*?)```",
    re.DOTALL,
)


def parse_response(raw: str) -> ParsedResponse:
    """Extract tool_call blocks and remaining text from Claude's response.

    Claude is instructed to wrap tool invocations like:

        ```tool_call
        {"tool": "read_file", "params": {"path": "src/main.py"}}
        ```

    Everything outside those fences is treated as plain-text commentary.
    """
    tool_calls: list[ToolCall] = []
    errors: list[str] = []

    for match in _TOOL_CALL_RE.finditer(raw):
        block = match.group(1).strip()
        try:
            data = json.loads(block)
        except json.JSONDecodeError as exc:
            # Try to be lenient — strip trailing commas, etc.
            cleaned = _lenient_clean(block)
            try:
                data = json.loads(cleaned)
            except json.JSONDecodeError:
                errors.append(f"Could not parse tool_call block: {exc}\n---\n{block}")
                continue

        tool_name = data.get("tool") or data.get("name") or data.get("tool_name")
        params = data.get("params") or data.get("parameters") or data.get("input") or {}

        if not tool_name:
            errors.append(f"tool_call block missing 'tool' key: {block}")
            continue

        tool_calls.append(ToolCall(tool=tool_name, params=params))

    # Strip tool_call fences to get the plain text
    plain = _TOOL_CALL_RE.sub("", raw).strip()
    if errors:
        plain += "\n\n[Parser warnings]\n" + "\n".join(errors)

    return ParsedResponse(text=plain, tool_calls=tool_calls)


def _lenient_clean(s: str) -> str:
    """Best-effort cleanup of slightly malformed JSON."""
    # Remove trailing commas before } or ]
    s = re.sub(r",\s*([}\]])", r"\1", s)
    # Remove single-line // comments
    s = re.sub(r"//.*$", "", s, flags=re.MULTILINE)
    return s
