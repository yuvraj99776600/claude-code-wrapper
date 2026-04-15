# Claude Code Wrapper

A Python API wrapper around Anthropic's Messages API that gives Claude **read_file**, **write_file**, and **execute_shell_command** tools — turning it into an agentic coding assistant that can autonomously explore, modify, and run code on your machine.

## Features

- **Multi-turn tool loop** — Claude calls tools, receives results, and continues until the task is done (up to a configurable turn limit).
- **File-path pre-loading** — Pass a list of files and they are injected into the first message so Claude has immediate context.
- **Sandbox validation** — All file reads/writes are checked against an allowed-roots list to prevent escaping the project directory.
- **Destructive-command guard** — A blocklist prevents accidentally running `rm -rf /` and friends.
- **CLI & library** — Use it from the command line or import `ClaudeCodeWrapper` in your own Python code.

## Quick start

```bash
# Install
pip install -e .

# Set your API key
export ANTHROPIC_API_KEY="sk-ant-..."

# One-shot
claude-code "Add type hints to utils.py" -f src/utils.py

# Interactive
claude-code -i
```

## Library usage

```python
from claude_code import ClaudeCodeWrapper

wrapper = ClaudeCodeWrapper(
    allowed_roots=["./my-project"],
    model="claude-sonnet-4-20250514",
)

result = wrapper.run(
    prompt="Refactor the database module to use async/await",
    file_paths=["src/db.py", "src/models.py"],
)

print(result["text"])           # Final assistant response
print(result["tool_results"])   # List of every tool call + result
print(result["turns"])          # Number of API round trips
```

## CLI flags

| Flag | Description |
|---|---|
| `prompt` | Natural-language instruction (positional) |
| `-f / --files` | Files to pre-load into context |
| `-m / --model` | Model identifier (default `claude-sonnet-4-20250514`) |
| `--max-turns` | Max tool round trips (default 40) |
| `--allowed-roots` | Directories tools may access (default cwd) |
| `--working-dir` | Shell command working directory |
| `-i / --interactive` | Stay in a prompt loop after the first task |
| `--json` | Emit structured JSON output |

## Architecture

```
claude_code/
├── __init__.py      # Package exports
├── wrapper.py       # ClaudeCodeWrapper — drives the multi-turn loop
├── tools.py         # Tool schemas, sandbox validation, handlers
└── cli.py           # Command-line interface
```

### Conversation flow

1. The user's prompt (+ pre-loaded files) is sent as the first `user` message.
2. The Messages API returns either **text** (done) or **tool_use** blocks.
3. Each tool_use block is dispatched to its handler; results are sent back as `tool_result` blocks.
4. Steps 2-3 repeat until Claude responds with only text or the turn limit is hit.

## Security notes

- File operations are sandboxed to `--allowed-roots` (defaults to cwd).
- A static blocklist prevents the most dangerous shell commands.
- **This is not a security boundary** — treat it the same way you'd treat giving someone SSH access. Review the `--allowed-roots` you set.
