"""Core wrapper that drives the multi-turn agentic conversation with Claude."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import anthropic

from .tools import TOOL_DEFINITIONS, dispatch_tool


# ---------- defaults ----------
DEFAULT_MODEL = "claude-sonnet-4-20250514"
MAX_TURNS = 40  # safety limit on agentic loops
MAX_TOKENS = 16_384


class ClaudeCodeWrapper:
    """Drive a multi-turn, tool-using conversation with Claude.

    Parameters
    ----------
    api_key : str | None
        Anthropic API key.  Falls back to ``ANTHROPIC_API_KEY`` env var.
    model : str
        Model identifier to use for every Messages API call.
    allowed_roots : list[str] | None
        Directories the tools are allowed to read/write.  Defaults to cwd.
    working_dir : str | None
        Working directory for shell commands.  Defaults to the first
        allowed root.
    max_turns : int
        Maximum number of assistant→tool round trips before stopping.
    max_tokens : int
        ``max_tokens`` passed to each Messages API call.
    system_prompt : str | None
        Optional system prompt override.
    """

    def __init__(
        self,
        api_key: str | None = None,
        model: str = DEFAULT_MODEL,
        allowed_roots: list[str] | None = None,
        working_dir: str | None = None,
        max_turns: int = MAX_TURNS,
        max_tokens: int = MAX_TOKENS,
        system_prompt: str | None = None,
    ) -> None:
        self.client = anthropic.Anthropic(api_key=api_key or os.environ.get("ANTHROPIC_API_KEY"))
        self.model = model
        self.allowed_roots = [str(Path(r).resolve()) for r in (allowed_roots or [os.getcwd()])]
        self.working_dir = working_dir or self.allowed_roots[0]
        self.max_turns = max_turns
        self.max_tokens = max_tokens
        self.system_prompt = system_prompt or self._default_system_prompt()

    # ------------------------------------------------------------------ #
    #  Public API                                                        #
    # ------------------------------------------------------------------ #

    def run(
        self,
        prompt: str,
        file_paths: list[str] | None = None,
        *,
        on_tool_call: Any | None = None,
        on_text: Any | None = None,
    ) -> dict[str, Any]:
        """Execute a full agentic session.

        Parameters
        ----------
        prompt : str
            Natural-language instruction.
        file_paths : list[str] | None
            Files to pre-load into the conversation context.
        on_tool_call : callable | None
            ``(tool_name, tool_input, tool_result) -> None`` callback.
        on_text : callable | None
            ``(text_chunk) -> None`` callback for streamed assistant text.

        Returns
        -------
        dict with keys ``text``, ``tool_results``, ``messages``, ``turns``.
        """
        messages = self._build_initial_messages(prompt, file_paths or [])
        tool_results: list[dict[str, Any]] = []
        final_text_parts: list[str] = []

        for turn in range(self.max_turns):
            response = self._call_api(messages)

            # Collect text blocks
            text_blocks = [b.text for b in response.content if b.type == "text"]
            if text_blocks:
                combined = "\n".join(text_blocks)
                final_text_parts.append(combined)
                if on_text:
                    on_text(combined)

            # If no tool use, conversation is done
            if response.stop_reason != "tool_use":
                break

            # Process every tool_use block in the response
            tool_use_blocks = [b for b in response.content if b.type == "tool_use"]
            tool_result_contents: list[dict[str, Any]] = []

            for tool_block in tool_use_blocks:
                result_str = dispatch_tool(
                    tool_name=tool_block.name,
                    tool_input=tool_block.input,
                    allowed_roots=self.allowed_roots,
                    working_dir=self.working_dir,
                )
                tool_entry = {
                    "tool_name": tool_block.name,
                    "tool_input": tool_block.input,
                    "result": result_str,
                }
                tool_results.append(tool_entry)

                if on_tool_call:
                    on_tool_call(tool_block.name, tool_block.input, result_str)

                tool_result_contents.append({
                    "type": "tool_result",
                    "tool_use_id": tool_block.id,
                    "content": result_str,
                })

            # Append the assistant turn and all tool results
            messages.append({"role": "assistant", "content": response.content})
            messages.append({"role": "user", "content": tool_result_contents})

        return {
            "text": "\n".join(final_text_parts),
            "tool_results": tool_results,
            "messages": messages,
            "turns": turn + 1 if 'turn' in dir() else 0,
        }

    # ------------------------------------------------------------------ #
    #  Internals                                                         #
    # ------------------------------------------------------------------ #

    def _call_api(self, messages: list[dict]) -> Any:
        """Make a single Messages API call with tools."""
        return self.client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=self.system_prompt,
            tools=TOOL_DEFINITIONS,
            messages=messages,
        )

    def _build_initial_messages(
        self, prompt: str, file_paths: list[str]
    ) -> list[dict[str, Any]]:
        """Build the first user message, optionally pre-loading files."""
        parts: list[str] = []

        if file_paths:
            parts.append("Here are the files in the project:\n")
            for fp in file_paths:
                resolved = str(Path(fp).resolve())
                try:
                    content = Path(resolved).read_text(encoding="utf-8")
                    parts.append(f"--- {fp} ---\n{content}\n")
                except Exception as exc:
                    parts.append(f"--- {fp} ---\n[Could not read: {exc}]\n")

        parts.append(f"\n{prompt}")

        return [{"role": "user", "content": "\n".join(parts)}]

    @staticmethod
    def _default_system_prompt() -> str:
        return (
            "You are Claude Code, an expert software engineer. "
            "You have access to tools that let you read files, write files, "
            "and execute shell commands on the user's machine.\n\n"
            "Guidelines:\n"
            "- Read files before modifying them to understand existing code.\n"
            "- Write complete file contents when using write_file (no placeholders).\n"
            "- Use execute_shell_command for running tests, installing deps, git, etc.\n"
            "- Be precise and minimal — only change what the user asked for.\n"
            "- If you need to explore the project, use shell commands like "
            "'find . -type f' or 'ls -la'.\n"
            "- After completing the task, summarize what you did.\n"
        )
