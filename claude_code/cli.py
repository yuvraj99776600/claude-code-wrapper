"""CLI entry point — interactive clipboard-bridge mode and local API server."""

import argparse
import os
import sys

from .wrapper import ClaudeCodeWrapper


# ---------- Pretty callbacks ---------- #

def _on_tool_call(name: str, inputs: dict, result: str) -> None:
    print(f"\n{'─' * 60}")
    label = {
        "read_file": f"  READ   {inputs.get('path', '')}",
        "write_file": f"  WRITE  {inputs.get('path', '')}",
        "execute_shell_command": f"  SHELL  {inputs.get('command', '')[:70]}",
    }.get(name, f"  TOOL   {name}")
    print(label)
    preview = result[:400] + ("…" if len(result) > 400 else "")
    print(f"  result: {preview}")
    print(f"{'─' * 60}")


def _on_prompt_ready(prompt: str) -> None:
    """Show the user that a prompt is ready (clipboard or manual)."""
    try:
        import pyperclip
        pyperclip.copy(prompt)
        print("\n>>> Prompt COPIED to clipboard. Paste it into claude.ai.")
    except ImportError:
        # Fallback: print the prompt so the user can copy manually
        print("\n" + "=" * 60)
        print("  Copy the prompt below and paste it into claude.ai:")
        print("=" * 60)
        print(prompt)
        print("=" * 60)


# ---------- Commands ---------- #

def cmd_run(args: argparse.Namespace) -> None:
    """One-shot or interactive clipboard-bridge mode."""
    wrapper = ClaudeCodeWrapper(
        allowed_roots=args.allowed_roots,
        working_dir=args.working_dir,
        max_turns=args.max_turns,
        auto_copy=True,
    )

    def _do(prompt: str, files: list[str]) -> None:
        print(f"\n{'━' * 60}")
        print(f"  Task: {prompt[:120]}{'…' if len(prompt) > 120 else ''}")
        print(f"{'━' * 60}")
        result = wrapper.run(
            prompt,
            files,
            on_tool_call=_on_tool_call,
            on_prompt_ready=_on_prompt_ready,
        )
        print(f"\n{'━' * 60}")
        print(f"  Done — {result['turns']} turn(s), {len(result['tool_results'])} tool call(s)")
        print(f"{'━' * 60}\n")
        if result["text"]:
            print(result["text"])

    if args.prompt:
        _do(args.prompt, args.files or [])

    if args.interactive or not args.prompt:
        print("\nInteractive mode — type your task, 'exit' to quit.\n")
        while True:
            try:
                user_input = input("you> ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\nBye.")
                break
            if user_input.lower() in ("exit", "quit", "q"):
                break
            if not user_input:
                continue
            _do(user_input, [])


def cmd_serve(args: argparse.Namespace) -> None:
    """Start the local API server."""
    from .server import create_app

    app = create_app(
        allowed_roots=args.allowed_roots,
        working_dir=args.working_dir,
        max_turns=args.max_turns,
    )
    print(f"Starting Claude Code bridge server on http://127.0.0.1:{args.port}")
    print("Endpoints:")
    print(f"  POST /v1/messages              — start a session")
    print(f"  POST /v1/sessions/<id>/respond — paste Claude's reply")
    print(f"  GET  /v1/sessions/<id>         — check session status")
    print(f"  GET  /health                   — health check\n")
    app.run(host="127.0.0.1", port=args.port, debug=args.debug)


# ---------- Argument parser ---------- #

def main() -> None:
    parser = argparse.ArgumentParser(
        prog="claude-code",
        description="Agentic coding via your Claude Pro web account — no API key needed.",
    )
    sub = parser.add_subparsers(dest="command")

    # --- `claude-code run` (default) ---
    p_run = sub.add_parser("run", help="Interactive clipboard-bridge mode.")
    p_run.add_argument("prompt", nargs="?", help="Task for Claude.")
    p_run.add_argument("-f", "--files", nargs="*", default=[], help="Files to pre-load.")
    p_run.add_argument("--max-turns", type=int, default=40)
    p_run.add_argument("--allowed-roots", nargs="*", default=None)
    p_run.add_argument("--working-dir", default=None)
    p_run.add_argument("-i", "--interactive", action="store_true")

    # --- `claude-code serve` ---
    p_serve = sub.add_parser("serve", help="Start local API server.")
    p_serve.add_argument("-p", "--port", type=int, default=5050)
    p_serve.add_argument("--max-turns", type=int, default=40)
    p_serve.add_argument("--allowed-roots", nargs="*", default=None)
    p_serve.add_argument("--working-dir", default=None)
    p_serve.add_argument("--debug", action="store_true")

    args = parser.parse_args()

    # Default to interactive run if no subcommand
    if args.command is None or args.command == "run":
        if args.command is None:
            # Re-parse as 'run' with remaining args
            args = p_run.parse_args(sys.argv[1:])
        cmd_run(args)
    elif args.command == "serve":
        cmd_serve(args)


if __name__ == "__main__":
    main()
