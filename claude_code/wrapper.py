"""Wrapper — manages the multi-turn agentic tool loop.

Supports two backends:
  1. **clipboard** — formats prompts for manual copy/paste (local dev)
  2. **browser**  — drives claude.ai via Playwright (VPS / production)
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any, Callable

from .prompt_builder import build_initial_prompt, build_tool_results_prompt
from .parser import parse_response, ParsedResponse
from .tools import dispatch_tool


MAX_TURNS = 40


class ClaudeCodeWrapper:
    """Drive a multi-turn, tool-using conversation with Claude.

    Parameters
    ----------
    allowed_roots : list[str] | None
        Directories the tools are allowed to access.  Defaults to cwd.
    working_dir : str | None
        Working directory for shell commands.
    max_turns : int
        Safety cap on agentic round-trips.
    auto_copy : bool
        If True, automatically copy prompts to the system clipboard (clipboard mode).
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
    #  Public API — clipboard mode (interactive / local)                 #
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
        """Run the agentic loop in clipboard mode (human pastes responses).

        Returns dict ``{"text", "tool_results", "turns"}``.
        """
        if get_response is None:
            get_response = self._default_get_response

        outgoing = build_initial_prompt(prompt, file_paths)
        all_tool_results: list[dict[str, Any]] = []
        final_text = ""

        for turn in range(self.max_turns):
            self._deliver_prompt(outgoing, on_prompt_ready)
            raw_response = get_response()
            parsed = parse_response(raw_response)

            if parsed.text:
                final_text = parsed.text
            if not parsed.has_tool_calls:
                break

            turn_results = self._execute_tool_calls(parsed, on_tool_call)
            all_tool_results.extend(turn_results)
            outgoing = build_tool_results_prompt(turn_results)

        return {
            "text": final_text,
            "tool_results": all_tool_results,
            "turns": turn + 1 if 'turn' in dir() else 0,
        }

    # ------------------------------------------------------------------ #
    #  Public API — browser mode (fully automated on VPS)                #
    # ------------------------------------------------------------------ #

    async def run_with_browser(
        self,
        prompt: str,
        file_paths: list[str] | None = None,
        *,
        send_fn: Callable[[str], Any],
        on_tool_call: Callable[[str, dict, str], None] | None = None,
        max_turns: int | None = None,
    ) -> dict[str, Any]:
        """Run the agentic loop using a browser send function.

        Parameters
        ----------
        send_fn : async callable(str) -> str
            Sends a message to Claude via the browser and returns the response.
        """
        outgoing = build_initial_prompt(prompt, file_paths)
        all_tool_results: list[dict[str, Any]] = []
        final_text = ""
        limit = max_turns or self.max_turns

        for turn in range(limit):
            raw_response = await send_fn(outgoing)
            parsed = parse_response(raw_response)

            if parsed.text:
                final_text = parsed.text
            if not parsed.has_tool_calls:
                break

            turn_results = self._execute_tool_calls(parsed, on_tool_call)
            all_tool_results.extend(turn_results)
            outgoing = build_tool_results_prompt(turn_results)

        return {
            "text": final_text,
            "tool_results": all_tool_results,
            "turns": turn + 1 if 'turn' in dir() else 0,
        }

    # ------------------------------------------------------------------ #
    #  Shared internals                                                  #
    # ------------------------------------------------------------------ #

    def _execute_tool_calls(
        self,
        parsed: ParsedResponse,
        on_tool_call: Callable[[str, dict, str], None] | None = None,
    ) -> list[dict[str, Any]]:
        """Execute all tool calls from a parsed response."""
        results: list[dict[str, Any]] = []
        for tc in parsed.tool_calls:
            result_str = dispatch_tool(
                tool_name=tc.tool,
                tool_input=tc.params,
                allowed_roots=self.allowed_roots,
                working_dir=self.working_dir,
            )
            results.append({
                "tool_name": tc.tool,
                "tool_input": tc.params,
                "result": result_str,
            })
            if on_tool_call:
                on_tool_call(tc.tool, tc.params, result_str)
        return results

    # ------------------------------------------------------------------ #
    #  Clipboard helpers                                                 #
    # ------------------------------------------------------------------ #

    def _deliver_prompt(
        self,
        text: str,
        on_prompt_ready: Callable[[str], None] | None,
    ) -> None:
        if self.auto_copy:
            try:
                import pyperclip
                pyperclip.copy(text)
            except ImportError:
                pass
        if on_prompt_ready:
            on_prompt_ready(text)

    @staticmethod
    def _default_get_response() -> str:
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
                if empty_count == 1:
                    lines.append("")
                empty_count = 0
                lines.append(line)
        return "\n".join(lines)
