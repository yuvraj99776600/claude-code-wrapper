"""Claude Code - Browser-automated wrapper that turns your Claude Pro account into a local API."""

__version__ = "0.3.0"

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
