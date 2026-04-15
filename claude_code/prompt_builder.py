"""Build pasteable prompts that tell Claude about available tools and conversation history."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .tools import TOOL_DEFINITIONS

# ------------------------------------------------------------------ #
#  System prompt that teaches Claude the tool-call protocol           #
# ------------------------------------------------------------------ #

SYSTEM_PROMPT = """\
You are Claude Code, an expert software engineer operating inside an agentic loop.
You have access to the following tools to interact with the user's file system:

## Available Tools

### read_file
Read the contents of a file.
Parameters: path (string, required)

### write_file
Write content to a file. Creates parent directories if needed. Overwrites if exists.
Parameters: path (string, required), content (string, required)

### execute_shell_command
Execute a shell command and get stdout/stderr/exit code.
Parameters: command (string, required), timeout (integer, optional, default 120)

## How to call tools

When you need to use a tool, output a JSON block fenced with ```tool_call markers:

```tool_call
{
  "tool": "<tool_name>",
  "params": { ... }
}
```

You may make MULTIPLE tool calls in a single response — use one ```tool_call block per call.

After each tool call round, I will reply with the results inside ```tool_result blocks.
Then you continue working until the task is complete.

When you are DONE and have no more tool calls to make, write your final summary as plain text with NO tool_call blocks.

## Rules
- Read files before modifying them.
- Write COMPLETE file contents (no placeholders or ellipsis).
- Be precise — only change what was requested.
- After finishing, summarize what you did.
"""


def build_initial_prompt(
    user_prompt: str,
    file_paths: list[str] | None = None,
) -> str:
    """Build the first message to paste into Claude.

    Includes the system prompt, pre-loaded file contents, and the user's request.
    """
    sections: list[str] = [SYSTEM_PROMPT.strip(), ""]

    # Pre-load files
    if file_paths:
        sections.append("## Project Files\n")
        for fp in file_paths:
            try:
                content = Path(fp).resolve().read_text(encoding="utf-8")
                sections.append(f"### `{fp}`\n```\n{content}\n```\n")
            except Exception as exc:
                sections.append(f"### `{fp}`\n[Could not read: {exc}]\n")

    sections.append(f"## Task\n\n{user_prompt}")
    return "\n".join(sections)


def build_tool_results_prompt(results: list[dict[str, Any]]) -> str:
    """Format executed tool results into a follow-up message to paste into Claude.

    Each result dict has keys: tool_name, tool_input, result.
    """
    parts: list[str] = ["Here are the tool results:\n"]

    for r in results:
        parts.append(f"### {r['tool_name']}({_summarize_input(r['tool_name'], r['tool_input'])})")
        parts.append(f"```tool_result\n{r['result']}\n```\n")

    parts.append("Continue working on the task. If you need more tool calls, use ```tool_call blocks. If done, give your final summary.")
    return "\n".join(parts)


def _summarize_input(tool_name: str, inputs: dict[str, Any]) -> str:
    """One-line summary of tool input for display."""
    if tool_name == "read_file":
        return inputs.get("path", "")
    if tool_name == "write_file":
        return inputs.get("path", "")
    if tool_name == "execute_shell_command":
        return inputs.get("command", "")[:80]
    return str(inputs)[:80]
