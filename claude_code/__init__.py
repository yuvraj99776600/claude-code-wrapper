"""Claude Code Wrapper — OpenAI-compatible API backed by the `claude` CLI."""

__version__ = "0.4.0"

from .chat_pool import ChatPool
from .claude_cli import ClaudeCliError, ClaudeSession
from .server import create_app

__all__ = ["ChatPool", "ClaudeCliError", "ClaudeSession", "create_app", "__version__"]
