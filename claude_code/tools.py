"""Tool definitions and local execution handlers.

No external API dependency — tools run entirely on the local machine.
"""

import subprocess
from pathlib import Path
from typing import Any


# --- Tool descriptions (used when building the prompt for Claude) ---

TOOL_DEFINITIONS = {
    "read_file": {
        "name": "read_file",
        "description": (
            "Read the contents of a file at the given path. "
            "Returns the file content as a string."
        ),
        "parameters": {
            "path": "Absolute or relative path to the file to read.",
        },
        "required": ["path"],
    },
    "write_file": {
        "name": "write_file",
        "description": (
            "Write content to a file at the given path. "
            "Creates the file and any parent directories if they don't exist. "
            "Overwrites the file if it already exists."
        ),
        "parameters": {
            "path": "Absolute or relative path to the file to write.",
            "content": "The full content to write to the file.",
        },
        "required": ["path", "content"],
    },
    "execute_shell_command": {
        "name": "execute_shell_command",
        "description": (
            "Execute a shell command and return its stdout, stderr, and exit code. "
            "Use this for running tests, installing packages, git operations, etc."
        ),
        "parameters": {
            "command": "The shell command to execute.",
            "timeout": "(Optional) Maximum seconds to wait. Defaults to 120.",
        },
        "required": ["command"],
    },
}


# --- Sandbox ---

class SandboxViolation(Exception):
    """Raised when a tool call attempts to escape the allowed directory."""


def _resolve_and_validate(path_str: str, allowed_roots: list[str]) -> str:
    resolved = Path(path_str).resolve()
    for root in allowed_roots:
        root_resolved = Path(root).resolve()
        try:
            resolved.relative_to(root_resolved)
            return str(resolved)
        except ValueError:
            continue
    raise SandboxViolation(
        f"Access denied: '{path_str}' resolves to '{resolved}' "
        f"which is outside allowed roots {allowed_roots}"
    )


# --- Handlers ---

def handle_read_file(inputs: dict[str, Any], allowed_roots: list[str], **_: Any) -> str:
    path = _resolve_and_validate(inputs["path"], allowed_roots)
    try:
        return Path(path).read_text(encoding="utf-8")
    except FileNotFoundError:
        return f"Error: File not found: {path}"
    except PermissionError:
        return f"Error: Permission denied: {path}"
    except Exception as exc:
        return f"Error reading file: {exc}"


def handle_write_file(inputs: dict[str, Any], allowed_roots: list[str], **_: Any) -> str:
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


_BLOCKED_PATTERNS = ["rm -rf /", "mkfs", "dd if=", ":(){:|:&};:", "> /dev/sda"]


def handle_execute_shell_command(
    inputs: dict[str, Any],
    allowed_roots: list[str],
    working_dir: str | None = None,
    **_: Any,
) -> str:
    command = inputs["command"]
    timeout = inputs.get("timeout", 120)
    if isinstance(timeout, str):
        timeout = int(timeout)

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
        return f"Error: Command timed out after {timeout}s"
    except Exception as exc:
        return f"Error executing command: {exc}"


# --- Dispatcher ---

_HANDLERS = {
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
    handler = _HANDLERS.get(tool_name)
    if handler is None:
        return f"Error: Unknown tool '{tool_name}'"
    return handler(tool_input, allowed_roots, working_dir=working_dir)
