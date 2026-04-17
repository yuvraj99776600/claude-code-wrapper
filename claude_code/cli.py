"""Command-line entry point for claude-code-wrapper."""

from __future__ import annotations

import argparse
import logging
import sys

import uvicorn

from .server import create_app


def cmd_serve(args: argparse.Namespace) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    app = create_app(
        num_slots=args.slots,
        cwd=args.cwd,
        model=args.model,
        permission_mode=args.permission_mode,
        claude_bin=args.claude_bin,
    )
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="claude-code", description="Claude Code CLI wrapper")
    sub = parser.add_subparsers(dest="command", required=True)

    serve = sub.add_parser("serve", help="Run the OpenAI-compatible HTTP server")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("-p", "--port", type=int, default=5050)
    serve.add_argument("-s", "--slots", type=int, default=3, help="Number of Claude sessions")
    serve.add_argument("--cwd", default=None, help="Working directory for Claude CLI")
    serve.add_argument("--model", default=None, help="Claude model override")
    serve.add_argument(
        "--permission-mode",
        default="bypassPermissions",
        choices=["default", "acceptEdits", "bypassPermissions", "plan"],
    )
    serve.add_argument("--claude-bin", default="claude", help="Path to the claude executable")
    serve.set_defaults(func=cmd_serve)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
