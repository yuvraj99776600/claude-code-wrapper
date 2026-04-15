# Claude Code Wrapper

Turn your **Claude Pro web account** into a fully automated local API. No Anthropic API key needed — uses Playwright to control a real browser on your Windows VPS, with agentic file-system and shell tools.

**3 API keys = 3 concurrent Claude chat sessions**, each in its own browser tab.

## How it works

```
Client code ──POST /v1/messages──▸ FastAPI server
                                       │
                    ┌──────────────────┼──────────────────┐
                    │ API Key 1        │ API Key 2        │ API Key 3
                    │ (Browser Tab 1)  │ (Browser Tab 2)  │ (Browser Tab 3)
                    └──────────────────┼──────────────────┘
                                       │
                            Playwright types prompt
                            into claude.ai chat
                                       │
                            Waits for Claude to respond
                                       │
                            Parses response for tool_call blocks
                                       │
                           ┌─── Has tool calls? ───┐
                           │ YES                    │ NO
                    Execute locally           Return final text
                   (read/write/shell)         to the client
                           │
                    Format results → send back to Claude → loop
```

## Quick start

```bash
# 1. Install
pip install -e .
playwright install chromium

# 2. First run — visible browser so you can log in
claude-code serve --visible --slots 3

# 3. Log into claude.ai in the browser that opens.
#    The server waits, then prints your 3 API keys.

# 4. After login is saved, run headless from now on:
claude-code serve --slots 3

# 5. Use it like any API:
curl -X POST http://localhost:5050/v1/messages \
  -H "Authorization: Bearer cc-your-api-key-here" \
  -H "Content-Type: application/json" \
  -d '{"prompt": "Add error handling to main.py", "file_paths": ["main.py"]}'
```

The server handles the **entire agentic loop** internally — sends prompt to Claude, parses tool calls, executes them locally, sends results back, repeats until done, returns the final answer.

## API endpoints

| Method | Path | Description |
|---|---|---|
| `POST` | `/v1/messages` | Send a task. Runs the full tool loop and returns the result. |
| `POST` | `/v1/chat/new` | Reset your slot to a fresh Claude conversation. |
| `GET` | `/v1/keys` | List all API keys and their busy/idle status. |
| `GET` | `/health` | Server health check. |

### POST /v1/messages

**Headers:** `Authorization: Bearer <your-api-key>`

**Body:**
```json
{
  "prompt": "Refactor the database module to use connection pooling",
  "file_paths": ["src/db.py", "src/models.py"],
  "max_turns": 20
}
```

**Response:**
```json
{
  "text": "I've refactored the database module...",
  "tool_results": [
    {"tool_name": "read_file", "tool_input": {"path": "src/db.py"}, "result": "..."},
    {"tool_name": "write_file", "tool_input": {"path": "src/db.py", "content": "..."}, "result": "..."}
  ],
  "turns": 3
}
```

## CLI commands

```bash
# Start the API server (production — headless)
claude-code serve --slots 3 --port 5050

# Start with visible browser (first-time login)
claude-code serve --visible --slots 1

# Interactive clipboard mode (no browser, for local dev)
claude-code run -i
claude-code run "Fix the tests" -f tests/test_app.py
```

### serve flags

| Flag | Description |
|---|---|
| `-s / --slots` | Number of API keys / concurrent chats (default: 3) |
| `-p / --port` | Server port (default: 5050) |
| `--visible` | Show the browser window (required for first-time login) |
| `--browser-profile` | Path to persistent browser profile dir |
| `--allowed-roots` | Directories tools may access (default: cwd) |
| `--working-dir` | Shell command working directory |
| `--max-turns` | Max tool round trips per request (default: 40) |

## Architecture

```
claude_code/
├── __init__.py          # Package exports
├── browser.py           # Playwright automation — controls claude.ai
├── chat_pool.py         # Maps API keys → browser tabs, serializes access
├── server.py            # FastAPI server with auth and agentic tool loop
├── wrapper.py           # Core loop logic (clipboard + browser backends)
├── tools.py             # Tool handlers (read_file, write_file, shell) + sandbox
├── parser.py            # Extracts ```tool_call``` blocks from Claude's text
├── prompt_builder.py    # Formats prompts with tool protocol
└── cli.py               # CLI entry point (run / serve)
```

## Library usage

```python
import asyncio
from claude_code.browser import ClaudeBrowser
from claude_code.wrapper import ClaudeCodeWrapper

async def main():
    browser = ClaudeBrowser(headless=False)
    await browser.start()
    page = await browser.new_chat_page()

    wrapper = ClaudeCodeWrapper(allowed_roots=["./my-project"])
    result = await wrapper.run_with_browser(
        prompt="Add type hints to all functions",
        file_paths=["src/utils.py"],
        send_fn=lambda msg: browser.send_message(page, msg),
    )
    print(result["text"])
    await browser.stop()

asyncio.run(main())
```

## VPS deployment

1. Set up a Windows VPS
2. Install Python 3.10+ and git
3. Clone the repo, `pip install -e .`, `playwright install chromium`
4. First run with `--visible` via RDP to log into Claude
5. After login: `claude-code serve --slots 3 --port 5050`
6. The login persists in `~/.claude-code-wrapper/browser-profile/`
7. Point your apps at `http://<vps-ip>:5050/v1/messages`

## Security

- File operations sandboxed to `--allowed-roots` (defaults to cwd)
- Destructive shell commands blocked by pattern list
- Server binds to `127.0.0.1` by default — use a reverse proxy for remote access
- API keys are generated per-session and printed at startup
- Browser profile stores your Claude login — protect that directory
