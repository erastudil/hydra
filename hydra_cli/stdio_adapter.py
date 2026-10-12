"""Stdio JSON lines adapter. A native app spawns `python -m hydra_cli stdio` and drives the agent over pipes.

The wire format lives in docs/STDIO_PROTOCOL.md. Stdout carries protocol lines only: at start the real
stdout descriptor is moved to a private handle and descriptor 1 is pointed at stderr, so prints, tools
and child processes cannot write into the protocol stream.
"""

from __future__ import annotations

import io
import json
import os
import sys
import threading
from typing import Any, Dict, List, Optional

PROTOCOL_VERSION = 1
MAX_LINE_BYTES = 1 << 20
TOOL_OUTPUT_CHARS = 4000
CONTEXT_MODES = ("recall", "compact", "sliding")
DECISIONS = {"allow": "y", "skip": "n", "abort": "q"}
CAPABILITIES = ["turn", "cancel", "approval", "sessions", "history", "action"]
EXIT_OK = 0
EXIT_VERSION = 2
EXIT_FATAL = 70


class Adapter:
    def __init__(self, reader: Any, writer: Any) -> None:
        self._reader = reader
        self._writer = writer
        self._write_lock = threading.Lock()
        self._state = threading.Lock()
        self._worker: Optional[threading.Thread] = None
        self._turn_id: Optional[str] = None
        self._cancel = threading.Event()
        self._pending: Optional[str] = None  # id of the approval request being awaited
        self._decision = ""
        self._gate = threading.Event()
        self._approvals = 0
        self._calls = 0
        self._ledgers: Dict[str, Any] = {}
        self.exit_code = EXIT_OK

    # output

    def emit(self, message: Dict[str, Any]) -> None:
        line = json.dumps(message, ensure_ascii=False, default=str, separators=(",", ":"))
        with self._write_lock:
            self._writer.write(line + "\n")
            self._writer.flush()

    def error(self, message: str, code: str, turn: Optional[str] = None, req: Optional[str] = None) -> None:
        from hydra_cli.providers import redact

        safe = redact(message)
        out: Dict[str, Any] = {"type": "error", "code": code, "message": safe, "redacted": safe != str(message)}
        if turn is not None:
            out["turn"] = turn
        if req is not None:
            out["id"] = req
        self.emit(out)

    # input

    def _read_line(self) -> Optional[bytes]:
        """One line without its terminator. None at EOF. Oversize lines come back as b'' plus a flag via exception."""
        raw = self._reader.readline(MAX_LINE_BYTES + 1)
        if not raw:
            return None
        if len(raw) > MAX_LINE_BYTES and not raw.endswith(b"\n"):
            while True:
                rest = self._reader.readline(MAX_LINE_BYTES)
                if not rest or rest.endswith(b"\n"):
                    break
            raise ValueError(f"line longer than {MAX_LINE_BYTES} bytes")
        return raw.rstrip(b"\r\n")

    def serve(self) -> int:
        self.emit({"type": "hello", "protocol": PROTOCOL_VERSION, "agent": "hydra", "capabilities": CAPABILITIES,
                   "context_modes": list(CONTEXT_MODES)})
        try:
            while True:
                try:
                    raw = self._read_line()
                except ValueError as exc:
                    self.error(str(exc), "line_too_long")
                    continue
                if raw is None:
                    break
                if not raw.strip():
                    continue
                if not self._dispatch(raw):
                    break
        except Exception as exc:  # reader failure is fatal, the app sees the exit code
            self.error(f"{type(exc).__name__}: {exc}", "fatal")
            self.exit_code = EXIT_FATAL
        self._stop_turn()
        return self.exit_code

    def _stop_turn(self) -> None:
        self._cancel.set()
        self._release("q")
        worker = self._worker
        if worker is not None:
            worker.join(timeout=30)

    def _release(self, decision: str) -> None:
        self._decision = decision
        self._gate.set()

    def _dispatch(self, raw: bytes) -> bool:
        try:
            msg = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            self.error(f"malformed line: {exc}", "malformed")
            return True
        if not isinstance(msg, dict) or not isinstance(msg.get("type"), str):
            self.error("every line must be a JSON object with a string type", "malformed")
            return True
        kind = msg["type"]
        handler = {
            "hello": self._on_hello, "turn": self._on_turn, "cancel": self._on_cancel,
            "approval": self._on_approval, "shutdown": lambda m: False, "sessions": self._on_sessions,
            "history": self._on_history, "action": self._on_action,
        }.get(kind)
        if handler is None:
            self.error(f"unknown message type {kind!r}", "unknown_type", req=_text(msg.get("id")))
            return True
        result = handler(msg)
        return result is not False

    # handlers, all run on the reader thread and return quickly

    def _on_hello(self, msg: Dict[str, Any]) -> bool:
        if msg.get("protocol") != PROTOCOL_VERSION:
            self.error(f"unsupported protocol {msg.get('protocol')!r}, this agent speaks {PROTOCOL_VERSION}", "version")
            self.exit_code = EXIT_VERSION
            return False
        return True

    def _on_turn(self, msg: Dict[str, Any]) -> None:
        turn = _text(msg.get("turn"))
        if not turn:
            return self.error("turn needs a non-empty string turn id", "bad_request")
        prompt = msg.get("prompt")
        thread = "".join(c for c in str(msg.get("thread") or "") if c.isalnum() or c in "-_.")
        cwd = msg.get("cwd")
        mode = msg.get("mode", "recall")
        chars = msg.get("context_chars")
        problems = []
        if not isinstance(prompt, str) or not prompt.strip():
            problems.append("prompt must be a non-empty string")
        if not thread:
            problems.append("thread must be a non-empty string of letters, digits, dash, underscore or dot")
        if not isinstance(cwd, str) or not os.path.isdir(cwd):
            problems.append("cwd must be an existing directory")
        if mode not in CONTEXT_MODES:
            problems.append(f"mode must be one of {', '.join(CONTEXT_MODES)}")
        if chars is not None and (not isinstance(chars, int) or isinstance(chars, bool) or chars < 4096):
            problems.append("context_chars must be an integer of at least 4096")
        if msg.get("model") is not None and not isinstance(msg.get("model"), str):
            problems.append("model must be a string")
        if problems:
            return self.error("; ".join(problems), "bad_request", turn=turn)
        with self._state:
            if self._turn_id is not None:
                return self.error(f"turn {self._turn_id} is still running", "busy", turn=turn)
            self._turn_id = turn
            self._cancel.clear()
            self._gate.clear()
            self._pending = None
            self._worker = threading.Thread(target=self._run_turn, args=(turn, msg, thread), name="hydra-turn", daemon=True)
            self._worker.start()

    def _on_cancel(self, msg: Dict[str, Any]) -> None:
        turn = _text(msg.get("turn"))
        with self._state:
            if turn is None or turn != self._turn_id:
                return self.error(f"no running turn {turn!r}", "unknown_turn", turn=turn)
            self._cancel.set()
            self._release("q")

    def _on_approval(self, msg: Dict[str, Any]) -> None:
        req = _text(msg.get("id"))
        decision = DECISIONS.get(msg.get("decision"))
        if decision is None:
            return self.error("decision must be allow, skip or abort", "bad_request", req=req)
        with self._state:
            if req is None or req != self._pending:
                return self.error(f"no pending approval {req!r}", "unknown_approval", req=req)
            self._pending = None
            self._release(decision)

    def _on_sessions(self, msg: Dict[str, Any]) -> None:
        from pathlib import Path

        root = Path.home() / ".hydra" / "sessions"
        found: List[Dict[str, Any]] = []
        if root.is_dir():
            for entry in sorted(root.iterdir()):
                db = entry / "ledger.db"
                if db.is_file():
                    found.append({"thread": entry.name, "updated": int(db.stat().st_mtime)})
        self.emit({"type": "session_list", "id": _text(msg.get("id")), "sessions": found})

    def _on_history(self, msg: Dict[str, Any]) -> None:
        from hydra_cli.context import SessionContextLedger

        req = _text(msg.get("id"))
        thread = "".join(c for c in str(msg.get("thread") or "") if c.isalnum() or c in "-_.")
        if not thread:
            return self.error("thread must be a non-empty string", "bad_request", req=req)
        limit = msg.get("limit", 200)
        if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
            return self.error("limit must be a positive integer", "bad_request", req=req)
        ledger = self._ledgers.get(thread)
        if ledger is None:
            ledger = SessionContextLedger(session_id=thread)
        turns = [{"role": t["role"], "content": t["content"]} for t in ledger.turns][-limit:]
        ledger.end_turn()
        self.emit({"type": "history_result", "id": req, "thread": thread, "turns": turns})

    def _on_action(self, msg: Dict[str, Any]) -> None:
        req = _text(msg.get("id"))
        name = msg.get("action")
        params = msg.get("params", {})
        if not isinstance(name, str) or not isinstance(params, dict):
            return self.error("action must be a string and params an object", "bad_request", req=req)

        def run() -> None:
            from hydra_cli.desktop_core import dispatch_command_palette_action

            try:
                result = dispatch_command_palette_action(name, params)
            except Exception as exc:
                return self.error(f"{type(exc).__name__}: {exc}", "action_failed", req=req)
            self.emit({"type": "action_result", "id": req, "action": name, "result": result})

        threading.Thread(target=run, name="hydra-action", daemon=True).start()

    # the turn worker

    def _approve(self, turn: str, tool: str, args_json: str) -> str:
        with self._state:
            if self._cancel.is_set():
                return "q"
            self._approvals += 1
            req = f"a{self._approvals}"
            self._pending = req
            self._gate.clear()
        self.emit({"type": "approval_request", "turn": turn, "id": req, "tool": tool, "arguments": _args(args_json)})
        self._gate.wait()
        return self._decision

    def _run_turn(self, turn: str, msg: Dict[str, Any], thread: str) -> None:
        from hydra_cli.agent import CANCELLED_TEXT, run_agent_loop
        from hydra_cli.config import DEFAULT_ORCHESTRATOR_MODEL, get_context_window
        from hydra_cli.context import SessionContextLedger
        from hydra_cli.ui import set_approval_handler, set_diff_sink, set_tool_sink, set_tool_start_sink

        alias = msg.get("model") or DEFAULT_ORCHESTRATOR_MODEL
        ledger = self._ledgers.get(thread)
        if ledger is None:
            ledger = self._ledgers[thread] = SessionContextLedger(session_id=thread)
        calls = {"n": 0}

        def start(tool: str, args_json: str) -> None:
            calls["n"] += 1
            self.emit({"type": "tool_start", "turn": turn, "call": calls["n"], "tool": tool, "arguments": _args(args_json)})

        def result(tool: str, args_json: str, is_error: bool, output: str) -> None:
            self.emit({"type": "tool_result", "turn": turn, "call": calls["n"], "tool": tool, "is_error": bool(is_error),
                       "output": output[:TOOL_OUTPUT_CHARS], "truncated": len(output) > TOOL_OUTPUT_CHARS})

        set_approval_handler(lambda tool, args_json: self._approve(turn, tool, args_json))
        set_diff_sink(lambda path, text: self.emit({"type": "diff", "turn": turn, "path": path, "diff": text}))
        set_tool_start_sink(start)
        set_tool_sink(result)
        final: Dict[str, Any]
        try:
            answer = run_agent_loop(
                alias=alias, prompt=msg["prompt"], session_id=thread, resume_session=True, cwd=msg["cwd"],
                steer_mode=bool(msg.get("approve")), ledger=ledger, strategy=msg.get("mode", "recall"),
                on_token=lambda text: self.emit({"type": "token", "turn": turn, "text": text}),
                max_context_chars=msg.get("context_chars") or get_context_window(alias) * 4,
                cancel=self._cancel,
            )
            cancelled = self._cancel.is_set() or answer == CANCELLED_TEXT
            ok = not cancelled and answer != "[Turn aborted by developer]"
            final = {"type": "done", "turn": turn, "text": CANCELLED_TEXT if cancelled else (answer or ""),
                     "ok": ok, "cancelled": cancelled}
        except Exception as exc:
            self.error(f"{type(exc).__name__}: {exc}", "turn_failed", turn=turn)
            final = {"type": "done", "turn": turn, "text": "", "ok": False, "cancelled": self._cancel.is_set()}
        finally:
            set_approval_handler(None)
            set_diff_sink(None)
            set_tool_sink(None)
            set_tool_start_sink(None)
        # The slot frees and `done` is written under one lock: a turn sent right after `done` is never refused as
        # busy, and its messages cannot overtake this `done`.
        with self._state:
            self._turn_id = None
            self._pending = None
            self.emit(final)


def _text(value: Any) -> Optional[str]:
    return value if isinstance(value, str) and value else None


def _args(args_json: str) -> Any:
    try:
        return json.loads(args_json)
    except ValueError:
        return args_json


def main(argv: Optional[List[str]] = None) -> int:
    """Take over the process stdio, then serve until shutdown or EOF."""
    proto_out = io.TextIOWrapper(io.FileIO(os.dup(1), "wb"), encoding="utf-8", newline="\n", write_through=True)
    proto_in = io.BufferedReader(io.FileIO(os.dup(0), "rb"))
    os.dup2(2, 1)
    devnull = os.open(os.devnull, os.O_RDONLY)
    os.dup2(devnull, 0)
    os.close(devnull)
    sys.stdout = sys.stderr
    sys.stdin = open(os.devnull, "r", encoding="utf-8")
    return Adapter(proto_in, proto_out).serve()
