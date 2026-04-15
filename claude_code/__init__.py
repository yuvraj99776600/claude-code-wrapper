"""Claude Code - Clipboard-bridge wrapper that turns your Claude Pro web account into a local API."""

__version__ = "0.2.0"

from .wrapper import ClaudeCodeWrapper
from .parser import parse_response, ParsedResponse, ToolCall
from .tools import TOOL_DEFINITIONS, dispatch_tool

__all__ = [
    "ClaudeCodeWrapper",
    "parse_response",
    "ParsedResponse",
    "ToolCall",
    "TOOL_DEFINITIONS",
    "dispatch_tool",
]
