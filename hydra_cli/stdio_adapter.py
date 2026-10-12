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
DECISIONS = {"allow": "y", "allow_always": "y", "skip": "n", "abort": "q"}
PERMISSIONS = ("manual", "accept_edits", "plan", "bypass")
READ_ONLY_TOOLS = frozenset({"read_file", "list_dir", "grep_search", "find_files", "search_files", "compact_context", "retrieve_context"})
EDIT_TOOLS = frozenset({"write_file", "edit_file"})
CAPABILITIES = ["turn", "cancel", "approval", "permission", "sessions", "history", "action"]
EXIT_OK = 0
EXIT_VERSION = 2
EXIT_FATAL = 70
MAX_TURNS = 8


class _Turn:
    """Everything one running turn owns. Parallel turns share nothing but the output lock."""

    def __init__(self, turn: str, thread: str, permission: str) -> None:
        self.id = turn
        self.thread = thread
        self.permission = permission
        self.cancel = threading.Event()
        self.gate = threading.Event()
        self.decision = ""
        self.pending: Optional[str] = None
        self.asked: Optional[tuple] = None
        self.worker: Optional[threading.Thread] = None

    def release(self, decision: str) -> None:
        self.decision = decision
        self.gate.set()


class Adapter:
    def __init__(self, reader: Any, writer: Any) -> None:
        self._reader = reader
        self._writer = writer
        self._write_lock = threading.Lock()
        self._state = threading.Lock()
        self._turns: Dict[str, _Turn] = {}
        self._busy_threads: Dict[str, str] = {}  # thread -> turn id
        self._requests: Dict[str, _Turn] = {}  # approval id -> the turn waiting on it
        self._approvals = 0
        self._always: set = set()  # (thread, tool) pairs the app allowed for good
        self._calls = 0
        self._ledgers: Dict[str, Any] = {}
        self.exit_code = EXIT_OK

    # output

    def emit(self, message: Dict[str, Any]) -> None:
        line = json.dumps(message, ensure_ascii=False, default=str, separators=(",", ":"))
        with self._write_lock:
            try:
                self._writer.write(line + "\n")
                self._writer.flush()
            except (OSError, ValueError):
                # the app closed its end of stdout, so nobody is left to talk to
                sys.stderr.write("hydra stdio : stdout closed by the app, exiting 70\n")
                sys.stderr.flush()
                os._exit(EXIT_FATAL)

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
        self._stop_turns()
        return self.exit_code

    def _stop_turns(self) -> None:
        with self._state:
            running = list(self._turns.values())
        for t in running:
            t.cancel.set()
            t.release("q")
        for t in running:
            if t.worker is not None:
                t.worker.join(timeout=30)

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
        try:
            result = handler(msg)
        except Exception as exc:  # one bad request must not end the session
            self.error(f"{type(exc).__name__}: {exc}", "internal", req=_text(msg.get("id")), turn=_text(msg.get("turn")))
            return True
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
        if msg.get("permission") is not None and msg.get("permission") not in PERMISSIONS:
            problems.append(f"permission must be one of {', '.join(PERMISSIONS)}")
        if msg.get("model") is not None and not isinstance(msg.get("model"), str):
            problems.append("model must be a string")
        if problems:
            return self.error("; ".join(problems), "bad_request", turn=turn)
        permission = msg.get("permission") or ("manual" if msg.get("approve") else "bypass")
        with self._state:
            if turn in self._turns:
                return self.error(f"turn id {turn} is already running", "busy", turn=turn)
            if thread in self._busy_threads:
                return self.error(f"thread {thread} is still running turn {self._busy_threads[thread]}", "busy", turn=turn)
            if len(self._turns) >= MAX_TURNS:
                return self.error(f"{MAX_TURNS} turns are already running", "busy", turn=turn)
            t = _Turn(turn, thread, permission)
            self._turns[turn] = t
            self._busy_threads[thread] = turn
            t.worker = threading.Thread(target=self._run_turn, args=(t, msg), name=f"hydra-turn-{turn}", daemon=True)
            t.worker.start()

    def _on_cancel(self, msg: Dict[str, Any]) -> None:
        turn = _text(msg.get("turn"))
        with self._state:
            t = self._turns.get(turn) if turn else None
            if t is None:
                return self.error(f"no running turn {turn!r}", "unknown_turn", turn=turn)
            t.cancel.set()
            t.release("q")

    def _on_approval(self, msg: Dict[str, Any]) -> None:
        req = _text(msg.get("id"))
        decision = DECISIONS.get(msg.get("decision"))
        if decision is None:
            return self.error("decision must be allow, allow_always, skip or abort", "bad_request", req=req)
        with self._state:
            t = self._requests.pop(req, None) if req else None
            if t is None:
                return self.error(f"no pending approval {req!r}", "unknown_approval", req=req)
            t.pending = None
            if msg.get("decision") == "allow_always" and t.asked is not None:
                self._always.add(t.asked)
            t.release(decision)

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
            from hydra_cli import desk_actions
            from hydra_cli.desktop_core import dispatch_command_palette_action

            try:
                result = desk_actions.run(name, params)
                if result is None:
                    result = dispatch_command_palette_action(name, params)
            except Exception as exc:
                return self.error(f"{type(exc).__name__}: {exc}", "action_failed", req=req)
            self.emit({"type": "action_result", "id": req, "action": name, "result": result})

        threading.Thread(target=run, name="hydra-action", daemon=True).start()

    # the turn worker

    def _approve(self, t: _Turn, tool: str, args_json: str) -> str:
        """Decide one tool call. Plan and accept_edits decide on their own for some tools, the rest ask the app."""
        turn, thread, permission = t.id, t.thread, t.permission
        if permission == "plan":
            decision, reason = ("y", "plan mode allows read only tools") if tool in READ_ONLY_TOOLS else ("n", "plan mode allows read only tools")
        elif tool in READ_ONLY_TOOLS and permission != "manual":
            decision, reason = "y", f"{permission} mode"
        elif permission == "accept_edits" and tool in EDIT_TOOLS:
            decision, reason = "y", "accept_edits mode"
        elif (thread, tool) in self._always:
            decision, reason = "y", "allowed always for this thread"
        else:
            decision, reason = "", ""
        if decision:
            self.emit({"type": "auto_decision", "turn": turn, "tool": tool,
                       "decision": "allow" if decision == "y" else "skip", "reason": reason})
            return decision
        with self._state:
            if t.cancel.is_set():
                return "q"
            self._approvals += 1
            req = f"a{self._approvals}"
            t.pending = req
            t.asked = (thread, tool)
            self._requests[req] = t
            t.gate.clear()
        self.emit({"type": "approval_request", "turn": turn, "id": req, "tool": tool, "arguments": _args(args_json)})
        t.gate.wait()
        return t.decision

    def _run_turn(self, t: _Turn, msg: Dict[str, Any]) -> None:
        turn, thread = t.id, t.thread
        from hydra_cli.agent import CANCELLED_TEXT, run_agent_loop, set_usage_sink
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

        def usage(u: Dict[str, Any]) -> None:
            from hydra_cli.desktop_core import get_token_accounting

            pt, ct = int(u.get("prompt_tokens") or 0), int(u.get("completion_tokens") or 0)
            rec = get_token_accounting().record_usage(alias, pt, ct)
            self.emit({"type": "usage", "turn": turn, "model": alias, "prompt_tokens": pt, "completion_tokens": ct,
                       "cost_usd": rec["total_cost_usd"]})

        set_usage_sink(usage)
        permission = t.permission
        set_approval_handler(lambda tool, args_json: self._approve(t, tool, args_json))
        set_diff_sink(lambda path, text: self.emit({"type": "diff", "turn": turn, "path": path, "diff": text}))
        set_tool_start_sink(start)
        set_tool_sink(result)
        final: Dict[str, Any]
        try:
            answer = run_agent_loop(
                alias=alias, prompt=msg["prompt"], session_id=thread, resume_session=True, cwd=msg["cwd"],
                steer_mode=permission != "bypass", ledger=ledger, strategy=msg.get("mode", "recall"),
                on_token=lambda text: self.emit({"type": "token", "turn": turn, "text": text}),
                max_context_chars=msg.get("context_chars") or get_context_window(alias) * 4,
                cancel=t.cancel,
            )
            cancelled = t.cancel.is_set() or answer == CANCELLED_TEXT
            ok = not cancelled and answer != "[Turn aborted by developer]"
            final = {"type": "done", "turn": turn, "text": CANCELLED_TEXT if cancelled else (answer or ""),
                     "ok": ok, "cancelled": cancelled}
        except Exception as exc:
            self.error(f"{type(exc).__name__}: {exc}", "turn_failed", turn=turn)
            final = {"type": "done", "turn": turn, "text": "", "ok": False, "cancelled": t.cancel.is_set()}
        finally:
            set_usage_sink(None)
            set_approval_handler(None)
            set_diff_sink(None)
            set_tool_sink(None)
            set_tool_start_sink(None)
        # The slot frees and `done` is written under one lock: a turn sent right after `done` is never refused as
        # busy, and its messages cannot overtake this `done`.
        with self._state:
            self._turns.pop(turn, None)
            self._busy_threads.pop(thread, None)
            if t.pending:
                self._requests.pop(t.pending, None)
            self.emit(final)


CREATE_NO_WINDOW = 0x08000000


def hidden_popen_kwargs(kwargs: Dict[str, Any]) -> Dict[str, Any]:
    """Popen kwargs with the no-window flag added. A console child of a windowless parent otherwise opens its own console."""
    out = dict(kwargs)
    out["creationflags"] = int(out.get("creationflags") or 0) | CREATE_NO_WINDOW
    return out


def hide_child_consoles() -> bool:
    """On Windows make every subprocess this process starts (sandbox, git, tools) windowless. No-op elsewhere."""
    if os.name != "nt":
        return False
    import subprocess

    original = subprocess.Popen.__init__
    if getattr(original, "_hydra_hidden", False):
        return True

    def init(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        original(self, *args, **hidden_popen_kwargs(kwargs))

    init._hydra_hidden = True  # type: ignore[attr-defined]
    subprocess.Popen.__init__ = init  # type: ignore[method-assign]
    return True


def _text(value: Any) -> Optional[str]:
    return value if isinstance(value, str) and value else None


def _args(args_json: str) -> Any:
    try:
        return json.loads(args_json)
    except ValueError:
        return args_json


def main(argv: Optional[List[str]] = None) -> int:
    """Take over the process stdio, then serve until shutdown or EOF."""
    hide_child_consoles()
    proto_out = io.TextIOWrapper(io.FileIO(os.dup(1), "wb"), encoding="utf-8", newline="\n", write_through=True)
    proto_in = io.BufferedReader(io.FileIO(os.dup(0), "rb"))
    os.dup2(2, 1)
    devnull = os.open(os.devnull, os.O_RDONLY)
    os.dup2(devnull, 0)
    os.close(devnull)
    sys.stdout = sys.stderr
    sys.stdin = open(os.devnull, "r", encoding="utf-8")
    return Adapter(proto_in, proto_out).serve()
