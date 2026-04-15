"""Local HTTP server that exposes an Anthropic-compatible /v1/messages endpoint.

Incoming requests are converted into pasteable prompts; the user copies Claude's
response back; tool calls are executed locally; the loop continues until done.
"""

from __future__ import annotations

import json
import os
import threading
import uuid
import time
from pathlib import Path
from typing import Any

from flask import Flask, request, jsonify, Response

from .wrapper import ClaudeCodeWrapper
from .prompt_builder import build_initial_prompt, build_tool_results_prompt
from .parser import parse_response


# ------------------------------------------------------------------ #
#  In-memory session store                                            #
# ------------------------------------------------------------------ #

class Session:
    """Tracks one multi-turn agentic conversation."""

    def __init__(self, session_id: str, wrapper: ClaudeCodeWrapper) -> None:
        self.id = session_id
        self.wrapper = wrapper
        self.state: str = "idle"  # idle | waiting_for_paste | done
        self.pending_prompt: str = ""  # prompt waiting to be copied
        self.tool_results: list[dict[str, Any]] = []
        self.final_text: str = ""
        self.turns: int = 0
        self.error: str | None = None


_sessions: dict[str, Session] = {}
_sessions_lock = threading.Lock()


# ------------------------------------------------------------------ #
#  Flask app                                                          #
# ------------------------------------------------------------------ #

def create_app(
    allowed_roots: list[str] | None = None,
    working_dir: str | None = None,
    max_turns: int = 40,
) -> Flask:
    app = Flask(__name__)

    _roots = [str(Path(r).resolve()) for r in (allowed_roots or [os.getcwd()])]
    _wdir = working_dir or _roots[0]

    # -------------------------------------------------------------- #
    #  POST /v1/messages  — start or continue an agentic session      #
    # -------------------------------------------------------------- #
    @app.route("/v1/messages", methods=["POST"])
    def messages():
        """Accept an Anthropic-style Messages request.

        Required body fields:
            prompt (str): The user's natural-language task.
        Optional:
            file_paths (list[str]): Files to pre-load.
            session_id (str): Resume an existing session.
        """
        body = request.get_json(force=True)
        prompt = body.get("prompt") or body.get("content") or ""

        # Also accept the full Anthropic format
        if not prompt and "messages" in body:
            for msg in body["messages"]:
                if msg.get("role") == "user":
                    content = msg.get("content", "")
                    if isinstance(content, list):
                        prompt = " ".join(
                            b.get("text", "") for b in content if b.get("type") == "text"
                        )
                    else:
                        prompt = content
                    break

        if not prompt:
            return jsonify({"error": "No prompt provided"}), 400

        file_paths = body.get("file_paths", [])

        wrapper = ClaudeCodeWrapper(
            allowed_roots=_roots,
            working_dir=_wdir,
            max_turns=max_turns,
            auto_copy=True,
        )

        session_id = str(uuid.uuid4())
        session = Session(session_id, wrapper)

        formatted = build_initial_prompt(prompt, file_paths)
        session.pending_prompt = formatted
        session.state = "waiting_for_paste"

        with _sessions_lock:
            _sessions[session_id] = session

        return jsonify({
            "session_id": session_id,
            "status": "waiting_for_paste",
            "prompt": formatted,
            "message": "Copy the 'prompt' field and paste it into claude.ai. Then POST Claude's response to /v1/sessions/<session_id>/respond",
        })

    # -------------------------------------------------------------- #
    #  POST /v1/sessions/<id>/respond  — paste Claude's reply back    #
    # -------------------------------------------------------------- #
    @app.route("/v1/sessions/<session_id>/respond", methods=["POST"])
    def respond(session_id: str):
        with _sessions_lock:
            session = _sessions.get(session_id)
        if not session:
            return jsonify({"error": "Session not found"}), 404
        if session.state != "waiting_for_paste":
            return jsonify({"error": f"Session state is '{session.state}', not waiting_for_paste"}), 409

        body = request.get_json(force=True)
        raw_response = body.get("response", "")
        if not raw_response:
            return jsonify({"error": "No response provided"}), 400

        parsed = parse_response(raw_response)
        session.turns += 1

        if parsed.text:
            session.final_text = parsed.text

        if not parsed.has_tool_calls:
            # Done — no more tool calls
            session.state = "done"
            return jsonify({
                "session_id": session_id,
                "status": "done",
                "text": session.final_text,
                "tool_results": session.tool_results,
                "turns": session.turns,
            })

        # Execute tool calls locally
        turn_results: list[dict[str, Any]] = []
        for tc in parsed.tool_calls:
            from .tools import dispatch_tool
            result_str = dispatch_tool(
                tool_name=tc.tool,
                tool_input=tc.params,
                allowed_roots=session.wrapper.allowed_roots,
                working_dir=session.wrapper.working_dir,
            )
            entry = {
                "tool_name": tc.tool,
                "tool_input": tc.params,
                "result": result_str,
            }
            turn_results.append(entry)
            session.tool_results.append(entry)

        # Build follow-up prompt
        follow_up = build_tool_results_prompt(turn_results)
        session.pending_prompt = follow_up
        session.state = "waiting_for_paste"

        return jsonify({
            "session_id": session_id,
            "status": "waiting_for_paste",
            "prompt": follow_up,
            "tool_calls_executed": [
                {"tool": r["tool_name"], "summary": r["result"][:200]}
                for r in turn_results
            ],
            "turns": session.turns,
            "message": "Tools executed. Copy the 'prompt' field and paste it into claude.ai, then POST the response again.",
        })

    # -------------------------------------------------------------- #
    #  GET /v1/sessions/<id>  — check session status                  #
    # -------------------------------------------------------------- #
    @app.route("/v1/sessions/<session_id>", methods=["GET"])
    def get_session(session_id: str):
        with _sessions_lock:
            session = _sessions.get(session_id)
        if not session:
            return jsonify({"error": "Session not found"}), 404
        return jsonify({
            "session_id": session_id,
            "status": session.state,
            "pending_prompt": session.pending_prompt if session.state == "waiting_for_paste" else None,
            "final_text": session.final_text if session.state == "done" else None,
            "tool_results": session.tool_results,
            "turns": session.turns,
        })

    # -------------------------------------------------------------- #
    #  GET /health                                                    #
    # -------------------------------------------------------------- #
    @app.route("/health", methods=["GET"])
    def health():
        return jsonify({"status": "ok", "sessions": len(_sessions)})

    return app
