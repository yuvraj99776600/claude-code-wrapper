"""Claude Code - A Python API wrapper that provides an agentic coding experience using Anthropic's Messages API."""

__version__ = "0.1.0"

from .wrapper import ClaudeCodeWrapper
from .tools import TOOL_DEFINITIONS

__all__ = ["ClaudeCodeWrapper", "TOOL_DEFINITIONS"]
