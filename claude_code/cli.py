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
    """Start the local API server with browser automation."""
    import uvicorn
    from .server import create_app

    app = create_app(
        num_slots=args.slots,
        headless=not args.visible,
        browser_profile=args.browser_profile,
        allowed_roots=args.allowed_roots,
        working_dir=args.working_dir,
        max_turns=args.max_turns,
        rate_limit=args.rate_limit,
        min_delay=args.min_delay,
        max_delay=args.max_delay,
        proxy=args.proxy,
        timezone=args.timezone,
        locale=args.locale,
    )
    print(f"Starting Claude Code API server on http://{args.host}:{args.port}")
    print(f"  Slots:      {args.slots}")
    print(f"  Headless:   {not args.visible}")
    print(f"  Rate limit: {args.rate_limit} msgs/hour/slot")
    print(f"  Delay:      {args.min_delay}-{args.max_delay}s between messages")
    if args.proxy:
        masked = args.proxy.split("@")[-1] if "@" in args.proxy else args.proxy
        print(f"  Proxy:      {masked}")
    if args.timezone:
        print(f"  Timezone:   {args.timezone}")
    print(f"  Profile:    {args.browser_profile or '~/.claude-code-wrapper/browser-profile'}\n")
    uvicorn.run(app, host=args.host, port=args.port)


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
    p_serve = sub.add_parser("serve", help="Start local API server with browser automation.")
    p_serve.add_argument("-p", "--port", type=int, default=5050)
    p_serve.add_argument("--host", default="127.0.0.1", help="Bind address (default: 127.0.0.1, use 0.0.0.0 for remote access).")
    p_serve.add_argument("-s", "--slots", type=int, default=3, help="Number of API keys / concurrent chats (default: 3).")
    p_serve.add_argument("--visible", action="store_true", help="Show the browser window (use for first-time login).")
    p_serve.add_argument("--browser-profile", default=None, help="Path to persistent browser profile directory.")
    p_serve.add_argument("--max-turns", type=int, default=40)
    p_serve.add_argument("--rate-limit", type=int, default=20, help="Max messages per slot per hour (0=unlimited, default: 20).")
    p_serve.add_argument("--min-delay", type=float, default=2.0, help="Min seconds between messages on same slot (default: 2.0).")
    p_serve.add_argument("--max-delay", type=float, default=8.0, help="Max seconds between messages on same slot (default: 8.0).")
    p_serve.add_argument("--proxy", default=None, help="Proxy URL (e.g. http://user:pass@host:port).")
    p_serve.add_argument("--timezone", default=None, help="Timezone to spoof (e.g. America/New_York).")
    p_serve.add_argument("--locale", default=None, help="Locale to spoof (e.g. en-US).")
    p_serve.add_argument("--allowed-roots", nargs="*", default=None)
    p_serve.add_argument("--working-dir", default=None)

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
