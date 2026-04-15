# Claude Code Wrapper

A clipboard-bridge that turns your **Claude Pro web account** into a local API with agentic file-system and shell tools. **No API key needed.**

## How it works

```
Your code ──POST──▸ Local server ──formats prompt──▸ Clipboard
                                                        │
                                                        ▼
                                                   claude.ai
                                                   (you paste)
                                                        │
                                                        ▼
Your code ◂──result── Local server ◂──you paste── Claude's reply
                         │
                    executes tools
                    (read/write/shell)
                         │
                    formats results ──▸ Clipboard ──▸ claude.ai ...
```

1. You send a request to `http://localhost:5050/v1/messages`
2. The server builds a prompt with tool definitions and copies it to your clipboard
3. You paste it into claude.ai and copy Claude's response
4. You POST the response back to `/v1/sessions/<id>/respond`
5. The server parses tool calls, executes them locally, and gives you the next prompt
6. Repeat until Claude responds with no tool calls (task complete)

## Quick start

```bash
pip install -e .

# --- Option A: Local API server ---
claude-code serve

# In another terminal, start a session:
curl -X POST http://localhost:5050/v1/messages \
  -H "Content-Type: application/json" \
  -d '{"prompt": "Add error handling to main.py", "file_paths": ["main.py"]}'

# Paste the returned "prompt" into claude.ai, copy the response, then:
curl -X POST http://localhost:5050/v1/sessions/<SESSION_ID>/respond \
  -H "Content-Type: application/json" \
  -d '{"response": "<paste Claude response here>"}'

# --- Option B: Interactive CLI ---
claude-code run -i
claude-code run "Fix the tests" -f tests/test_app.py src/app.py
```

## Library usage

```python
from claude_code import ClaudeCodeWrapper

wrapper = ClaudeCodeWrapper(allowed_roots=["./my-project"])

# Provide your own response callback (e.g. integrate with a UI)
def get_response():
    return input("Paste Claude's response: ")

result = wrapper.run(
    prompt="Refactor the database module",
    file_paths=["src/db.py"],
    get_response=get_response,
)

print(result["text"])           # Final answer
print(result["tool_results"])   # Every tool call + result
print(result["turns"])          # Number of round trips
```

## API endpoints

| Method | Path | Description |
|---|---|---|
| `POST` | `/v1/messages` | Start a new session. Body: `{"prompt": "...", "file_paths": [...]}` |
| `POST` | `/v1/sessions/<id>/respond` | Send Claude's response. Body: `{"response": "..."}` |
| `GET` | `/v1/sessions/<id>` | Check session status and history |
| `GET` | `/health` | Server health check |

## CLI commands

| Command | Description |
|---|---|
| `claude-code run [prompt]` | Interactive clipboard-bridge mode |
| `claude-code run -f file1 file2` | Pre-load files |
| `claude-code serve` | Start the local HTTP API server |
| `claude-code serve -p 8080` | Custom port |

## Architecture

```
claude_code/
├── __init__.py          # Package exports
├── wrapper.py           # ClaudeCodeWrapper — multi-turn clipboard loop
├── tools.py             # Tool handlers (read_file, write_file, shell) + sandbox
├── parser.py            # Extracts ```tool_call blocks from Claude's text
├── prompt_builder.py    # Formats prompts with tool protocol for pasting
├── server.py            # Flask API server with session management
└── cli.py               # CLI entry point (run / serve)
```

## Tool-call protocol

Claude is instructed to emit tool calls as fenced JSON blocks:

~~~
```tool_call
{"tool": "read_file", "params": {"path": "src/main.py"}}
```
~~~

The wrapper parses these, executes them locally, and sends results back as `tool_result` blocks in the next prompt. This continues until Claude responds with plain text only.

## Security

- File operations are sandboxed to `--allowed-roots` (defaults to cwd)
- Destructive shell commands are blocked by a static pattern list
- The server only binds to `127.0.0.1` — not exposed to the network
- **Treat this like giving someone shell access** — review your allowed roots
