"""CLI entry point for Claude Code wrapper."""

import argparse
import os
import sys
import json

from .wrapper import ClaudeCodeWrapper


def _print_tool_call(name: str, inputs: dict, result: str) -> None:
    """Pretty-print a tool invocation."""
    print(f"\n{'─' * 60}")
    print(f"  🔧 {name}")
    if name == "read_file":
        print(f"     path: {inputs.get('path')}")
    elif name == "write_file":
        print(f"     path: {inputs.get('path')}")
        print(f"     size: {len(inputs.get('content', ''))} chars")
    elif name == "execute_shell_command":
        print(f"     cmd:  {inputs.get('command')}")
    # Truncate long results for display
    preview = result[:500] + ("…" if len(result) > 500 else "")
    print(f"     result: {preview}")
    print(f"{'─' * 60}")


def _print_text(text: str) -> None:
    """Print assistant text as it arrives."""
    print(text)


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="claude-code",
        description="Run an agentic coding session with Claude.",
    )
    parser.add_argument(
        "prompt",
        nargs="?",
        help="Natural-language instruction for Claude.",
    )
    parser.add_argument(
        "-f", "--files",
        nargs="*",
        default=[],
        help="File paths to pre-load into the conversation context.",
    )
    parser.add_argument(
        "-m", "--model",
        default="claude-sonnet-4-20250514",
        help="Anthropic model to use (default: claude-sonnet-4-20250514).",
    )
    parser.add_argument(
        "--max-turns",
        type=int,
        default=40,
        help="Maximum tool-use round trips (default: 40).",
    )
    parser.add_argument(
        "--allowed-roots",
        nargs="*",
        default=None,
        help="Directories the tools may access (default: cwd).",
    )
    parser.add_argument(
        "--working-dir",
        default=None,
        help="Working directory for shell commands.",
    )
    parser.add_argument(
        "-i", "--interactive",
        action="store_true",
        help="Enter interactive (multi-session) mode after the first prompt.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Output the full result as JSON instead of pretty-printing.",
    )
    args = parser.parse_args()

    if not args.prompt and not args.interactive:
        parser.print_help()
        sys.exit(1)

    wrapper = ClaudeCodeWrapper(
        model=args.model,
        allowed_roots=args.allowed_roots,
        working_dir=args.working_dir,
        max_turns=args.max_turns,
    )

    def _run_prompt(prompt: str, files: list[str]) -> None:
        if args.json_output:
            result = wrapper.run(prompt, files)
            # messages contain Anthropic SDK objects; serialize text/tool_results only
            print(json.dumps({
                "text": result["text"],
                "tool_results": result["tool_results"],
                "turns": result["turns"],
            }, indent=2))
        else:
            print(f"\n{'━' * 60}")
            print(f"  Prompt: {prompt[:120]}{'…' if len(prompt) > 120 else ''}")
            print(f"{'━' * 60}\n")
            result = wrapper.run(
                prompt,
                files,
                on_tool_call=_print_tool_call,
                on_text=_print_text,
            )
            print(f"\n{'━' * 60}")
            print(f"  Done — {result['turns']} turn(s), {len(result['tool_results'])} tool call(s)")
            print(f"{'━' * 60}\n")

    # Run initial prompt if given
    if args.prompt:
        _run_prompt(args.prompt, args.files)

    # Interactive loop
    if args.interactive:
        print("\nInteractive mode. Type 'exit' or 'quit' to stop.\n")
        while True:
            try:
                user_input = input(">>> ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\nExiting.")
                break
            if user_input.lower() in ("exit", "quit", "q"):
                break
            if not user_input:
                continue
            _run_prompt(user_input, [])


if __name__ == "__main__":
    main()
