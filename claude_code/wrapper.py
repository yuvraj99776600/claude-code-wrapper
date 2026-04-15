"""Clipboard-bridge wrapper — manages the multi-turn agentic loop via copy/paste.

No Anthropic API key required.  Uses your Claude Pro web account instead.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any, Callable

from .prompt_builder import build_initial_prompt, build_tool_results_prompt
from .parser import parse_response, ParsedResponse
from .tools import dispatch_tool


MAX_TURNS = 40


class ClaudeCodeWrapper:
    """Drive a multi-turn, tool-using conversation by copy/pasting to claude.ai.

    Parameters
    ----------
    allowed_roots : list[str] | None
        Directories the tools are allowed to access.  Defaults to cwd.
    working_dir : str | None
        Working directory for shell commands.
    max_turns : int
        Safety cap on agentic round-trips.
    auto_copy : bool
        If True, automatically copy prompts to the system clipboard.
    """

    def __init__(
        self,
        allowed_roots: list[str] | None = None,
        working_dir: str | None = None,
        max_turns: int = MAX_TURNS,
        auto_copy: bool = True,
    ) -> None:
        self.allowed_roots = [
            str(Path(r).resolve()) for r in (allowed_roots or [os.getcwd()])
        ]
        self.working_dir = working_dir or self.allowed_roots[0]
        self.max_turns = max_turns
        self.auto_copy = auto_copy

    # ------------------------------------------------------------------ #
    #  Public API                                                        #
    # ------------------------------------------------------------------ #

    def run(
        self,
        prompt: str,
        file_paths: list[str] | None = None,
        *,
        get_response: Callable[[], str] | None = None,
        on_tool_call: Callable[[str, dict, str], None] | None = None,
        on_prompt_ready: Callable[[str], None] | None = None,
    ) -> dict[str, Any]:
        """Run the full agentic loop.

        Parameters
        ----------
        prompt : str
            Natural-language task.
        file_paths : list[str] | None
            Files to inject into the first message.
        get_response : callable
            A function that returns Claude's raw response text.
            Defaults to clipboard-based input (waits for paste).
        on_tool_call : callable | None
            ``(tool_name, tool_input, result) -> None`` progress callback.
        on_prompt_ready : callable | None
            ``(formatted_prompt) -> None`` called when a prompt is ready
            to be pasted.  Receives the full text.

        Returns
        -------
        dict  ``{"text", "tool_results", "turns"}``
        """
        if get_response is None:
            get_response = self._default_get_response

        # --- Turn 0: initial prompt ---
        outgoing = build_initial_prompt(prompt, file_paths)
        all_tool_results: list[dict[str, Any]] = []
        final_text = ""

        for turn in range(self.max_turns):
            # Present the prompt to the user
            self._deliver_prompt(outgoing, on_prompt_ready)

            # Get Claude's response (pasted back by the user)
            raw_response = get_response()
            parsed = parse_response(raw_response)

            # Always capture text
            if parsed.text:
                final_text = parsed.text

            # If no tool calls, we're done
            if not parsed.has_tool_calls:
                break

            # Execute each tool call locally
            turn_results: list[dict[str, Any]] = []
            for tc in parsed.tool_calls:
                result_str = dispatch_tool(
                    tool_name=tc.tool,
                    tool_input=tc.params,
                    allowed_roots=self.allowed_roots,
                    working_dir=self.working_dir,
                )
                entry = {
                    "tool_name": tc.tool,
                    "tool_input": tc.params,
                    "result": result_str,
                }
                turn_results.append(entry)
                all_tool_results.append(entry)

                if on_tool_call:
                    on_tool_call(tc.tool, tc.params, result_str)

            # Build follow-up prompt with results
            outgoing = build_tool_results_prompt(turn_results)

        return {
            "text": final_text,
            "tool_results": all_tool_results,
            "turns": turn + 1 if 'turn' in dir() else 0,
        }

    # ------------------------------------------------------------------ #
    #  Clipboard helpers                                                 #
    # ------------------------------------------------------------------ #

    def _deliver_prompt(
        self,
        text: str,
        on_prompt_ready: Callable[[str], None] | None,
    ) -> None:
        """Copy *text* to clipboard (if auto_copy) and notify callback."""
        if self.auto_copy:
            try:
                import pyperclip
                pyperclip.copy(text)
            except ImportError:
                pass  # pyperclip not installed; user copies manually
        if on_prompt_ready:
            on_prompt_ready(text)

    @staticmethod
    def _default_get_response() -> str:
        """Wait for the user to paste Claude's response via stdin."""
        print("\n" + "=" * 60)
        print("  Paste Claude's response below, then press Enter twice:")
        print("=" * 60)
        lines: list[str] = []
        empty_count = 0
        while True:
            try:
                line = input()
            except EOFError:
                break
            if line == "":
                empty_count += 1
                if empty_count >= 2:
                    break
            else:
                # Reset counter if we saw a non-empty line (single blank
                # lines in code blocks are fine)
                if empty_count == 1:
                    lines.append("")
                empty_count = 0
                lines.append(line)
        return "\n".join(lines)
