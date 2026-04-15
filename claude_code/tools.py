"""Tool definitions and execution handlers for the Claude Code wrapper."""

import os
import subprocess
import json
from pathlib import Path
from typing import Any


# --- Tool JSON Schema Definitions for the Messages API ---

TOOL_DEFINITIONS = [
    {
        "name": "read_file",
        "description": (
            "Read the contents of a file at the given path. "
            "Returns the file content as a string. Use this to inspect existing files."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Absolute or relative path to the file to read.",
                },
            },
            "required": ["path"],
        },
    },
    {
        "name": "write_file",
        "description": (
            "Write content to a file at the given path. "
            "Creates the file and any parent directories if they don't exist. "
            "Overwrites the file if it already exists."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Absolute or relative path to the file to write.",
                },
                "content": {
                    "type": "string",
                    "description": "The full content to write to the file.",
                },
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "execute_shell_command",
        "description": (
            "Execute a shell command and return its stdout, stderr, and exit code. "
            "Use this for running tests, installing packages, git operations, etc. "
            "Commands run in the working directory of the session."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "The shell command to execute.",
                },
                "timeout": {
                    "type": "integer",
                    "description": "Maximum seconds to wait for the command to complete. Defaults to 120.",
                },
            },
            "required": ["command"],
        },
    },
]


# --- Allowed-path validation ---

class SandboxViolation(Exception):
    """Raised when a tool call attempts to escape the allowed directory."""


def _resolve_and_validate(path_str: str, allowed_roots: list[str]) -> str:
    """Resolve a path and verify it falls under one of the allowed roots.

    Returns the resolved absolute path string.
    Raises SandboxViolation if the path is outside every allowed root.
    """
    resolved = Path(path_str).resolve()
    for root in allowed_roots:
        root_resolved = Path(root).resolve()
        try:
            resolved.relative_to(root_resolved)
            return str(resolved)
        except ValueError:
            continue
    raise SandboxViolation(
        f"Access denied: '{path_str}' resolves to '{resolved}' which is outside allowed roots {allowed_roots}"
    )


# --- Individual tool handlers ---

def handle_read_file(inputs: dict[str, Any], allowed_roots: list[str]) -> str:
    """Execute the read_file tool and return the result string."""
    path = _resolve_and_validate(inputs["path"], allowed_roots)
    try:
        content = Path(path).read_text(encoding="utf-8")
        return content
    except FileNotFoundError:
        return f"Error: File not found: {path}"
    except PermissionError:
        return f"Error: Permission denied: {path}"
    except Exception as exc:
        return f"Error reading file: {exc}"


def handle_write_file(inputs: dict[str, Any], allowed_roots: list[str]) -> str:
    """Execute the write_file tool and return a confirmation string."""
    path = _resolve_and_validate(inputs["path"], allowed_roots)
    content = inputs["content"]
    try:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return f"Successfully wrote {len(content)} bytes to {path}"
    except PermissionError:
        return f"Error: Permission denied: {path}"
    except Exception as exc:
        return f"Error writing file: {exc}"


_BLOCKED_PATTERNS = [
    "rm -rf /",
    "mkfs",
    "dd if=",
    ":(){:|:&};:",
    "> /dev/sda",
]


def handle_execute_shell_command(
    inputs: dict[str, Any],
    allowed_roots: list[str],
    working_dir: str | None = None,
) -> str:
    """Execute the shell command tool and return structured output."""
    command = inputs["command"]
    timeout = inputs.get("timeout", 120)

    # Basic destructive-command guard
    for pattern in _BLOCKED_PATTERNS:
        if pattern in command:
            return f"Error: Blocked potentially destructive command containing '{pattern}'"

    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=working_dir or (allowed_roots[0] if allowed_roots else None),
        )

        parts: list[str] = []
        if result.stdout:
            parts.append(f"STDOUT:\n{result.stdout}")
        if result.stderr:
            parts.append(f"STDERR:\n{result.stderr}")
        parts.append(f"EXIT CODE: {result.returncode}")
        return "\n".join(parts)

    except subprocess.TimeoutExpired:
        return f"Error: Command timed out after {timeout} seconds"
    except Exception as exc:
        return f"Error executing command: {exc}"


# --- Dispatcher ---

TOOL_HANDLERS = {
    "read_file": handle_read_file,
    "write_file": handle_write_file,
    "execute_shell_command": handle_execute_shell_command,
}


def dispatch_tool(
    tool_name: str,
    tool_input: dict[str, Any],
    allowed_roots: list[str],
    working_dir: str | None = None,
) -> str:
    """Route a tool call to its handler and return the result string."""
    handler = TOOL_HANDLERS.get(tool_name)
    if handler is None:
        return f"Error: Unknown tool '{tool_name}'"

    if tool_name == "execute_shell_command":
        return handle_execute_shell_command(tool_input, allowed_roots, working_dir)
    return handler(tool_input, allowed_roots)
