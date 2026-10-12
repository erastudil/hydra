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
import time
import subprocess
from typing import Any, Dict, List, Optional

PROTOCOL_VERSION = 1
MAX_LINE_BYTES = 1 << 20
TOOL_OUTPUT_CHARS = 4000
CONTEXT_MODES = ("recall", "compact", "sliding")
DECISIONS = {"allow": "y", "allow_always": "y", "skip": "n", "abort": "q"}
PERMISSIONS = ("manual", "accept_edits", "plan", "bypass")
READ_ONLY_TOOLS = frozenset({"task_output", "git_status", "git_diff", "read_file", "list_dir", "grep_search", "find_files", "search_files", "compact_context", "retrieve_context"})
EDIT_TOOLS = frozenset({"write_file", "edit_file"})
CAPABILITIES = ["turn", "cancel", "approval", "permission", "rules", "checkpoint", "skills", "mcp", "tasks", "sessions", "history", "action"]
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
        self._rules_lock = threading.Lock()
        self._mcp: Dict[str, Any] = {}
        from hydra_cli.native_tools import BACKGROUND_TASKS

        self._tasks = BACKGROUND_TASKS
        self._tasks.add_listener(lambda info: self.emit({"type": "task_update", **_task_view(info)}))
        self._mcp_lock = threading.Lock()
        self._shadow_lock = threading.RLock()  # one shadow git operation at a time: init and index locks do not mix
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
        self._tasks.adopt()
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
        self._tasks.stop_all()
        with self._mcp_lock:
            for _stamp, registry in self._mcp.values():
                registry.shutdown()
            self._mcp.clear()
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
            "history": self._on_history, "action": self._on_action, "rules": self._on_rules, "skills": self._on_skills, "mcp": self._on_mcp, "tasks": self._on_tasks, "task_stop": self._on_task_stop,
            "checkpoints": self._on_checkpoints, "rewind": self._on_rewind,
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
        stored = _load_meta(thread) if thread else {}
        cwd = msg.get("cwd", stored.get("cwd"))
        mode = msg.get("mode", stored.get("mode", "recall"))
        for key in ("model", "permission"):
            if msg.get(key) is None and stored.get(key) is not None:
                msg[key] = stored[key]
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
        skills = msg.get("skills", [])
        known_skills = {sk["id"] for sk in _skills()}
        if not isinstance(skills, list) or any(not isinstance(x, str) or x not in known_skills for x in skills):
            problems.append(f"skills must be a list of known skill ids: {', '.join(sorted(known_skills))}")
        if msg.get("model") is not None and not isinstance(msg.get("model"), str):
            problems.append("model must be a string")
        if problems:
            return self.error("; ".join(problems), "bad_request", turn=turn)
        permission = msg.get("permission") or ("manual" if msg.get("approve") else "bypass")
        msg["cwd"], msg["mode"] = cwd, mode
        with self._state:
            if turn in self._turns:
                return self.error(f"turn id {turn} is already running", "busy", turn=turn)
            if thread in self._busy_threads:
                return self.error(f"thread {thread} is still running turn {self._busy_threads[thread]}", "busy", turn=turn)
            if len(self._turns) >= MAX_TURNS:
                return self.error(f"{MAX_TURNS} turns are already running", "busy", turn=turn)
            t = _Turn(turn, thread, permission)
            _save_meta(thread, {"cwd": cwd, "mode": mode, "model": msg.get("model"), "permission": permission,
                                "title": stored.get("title") or prompt.strip()[:80], "updated": int(time.time())})
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
                problem = self._add_rule(t.asked, msg.get("prefix"), msg.get("scope", "thread"))
                if problem:
                    self.error(problem, "bad_request", turn=t.id, req=req)
            t.release(decision)

    # checkpoints: a shadow git repository outside the project, so the project's own repository is never touched

    def _git(self, cwd: str, *args: str, stdin: Optional[bytes] = None) -> "subprocess.CompletedProcess[bytes]":
        import hashlib
        import subprocess
        from pathlib import Path

        digest = hashlib.sha1(os.path.realpath(cwd).encode("utf-8")).hexdigest()[:16]
        gitdir = Path.home() / ".hydra" / "shadow" / f"{digest}.git"
        if not gitdir.exists():
            gitdir.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run(["git", "init", "--bare", "--quiet", str(gitdir)], check=True, capture_output=True)
            (gitdir / "info").mkdir(exist_ok=True)
            (gitdir / "info" / "exclude").write_text(".git/\n.hydra/\n", encoding="utf-8")
        cmd = ["git", "-c", "user.name=hydra", "-c", "user.email=hydra@localhost", "-c", "core.autocrlf=false",
               "-c", "gc.auto=0", "-c", "core.quotepath=off", f"--git-dir={gitdir}", f"--work-tree={cwd}", *args]
        return subprocess.run(cmd, input=stdin, capture_output=True, cwd=cwd)

    def _snapshot(self, cwd: str, label: str, thread: str) -> Dict[str, Any]:
        """Commit the whole project state to the shadow repository. Returns checkpoint fields for the app."""
        try:
            with self._shadow_lock:
                added = self._git(cwd, "add", "-A")
                if added.returncode:
                    return {"ok": False, "id": None, "reason": added.stderr.decode("utf-8", "replace").strip()[:300]}
                made = self._git(cwd, "commit", "--quiet", "--allow-empty", "-m", f"hydra checkpoint thread={thread} {label}")
                if made.returncode:
                    return {"ok": False, "id": None, "reason": made.stderr.decode("utf-8", "replace").strip()[:300]}
                return {"ok": True, "id": self._git(cwd, "rev-parse", "HEAD").stdout.decode().strip(), "reason": None}
        except Exception as exc:  # git missing or unusable: the turn still runs, the app is told
            return {"ok": False, "id": None, "reason": f"{type(exc).__name__}: {exc}"[:300]}

    def _on_checkpoints(self, msg: Dict[str, Any]) -> None:
        req = _text(msg.get("id"))
        thread = "".join(c for c in str(msg.get("thread") or "") if c.isalnum() or c in "-_.")
        cwd = _load_meta(thread).get("cwd") if thread else None
        if not cwd or not os.path.isdir(cwd):
            return self.error("thread must be a known thread with a project", "bad_request", req=req)
        out = self._git(cwd, "log", "--format=%H%x00%ct%x00%s", f"--grep=hydra checkpoint thread={thread} ")
        found = []
        for line in out.stdout.decode("utf-8", "replace").splitlines():
            sha, when, subject = (line.split("\x00") + ["", "", ""])[:3]
            if not sha or "turn=" not in subject:
                continue
            found.append({"id": sha, "turn": subject.split("turn=", 1)[1], "time": int(when or 0)})
        self.emit({"type": "checkpoint_list", "id": req, "thread": thread, "checkpoints": found})

    def _on_rewind(self, msg: Dict[str, Any]) -> None:
        req = _text(msg.get("id"))
        thread = "".join(c for c in str(msg.get("thread") or "") if c.isalnum() or c in "-_.")
        target = _text(msg.get("checkpoint"))
        cwd = _load_meta(thread).get("cwd") if thread else None
        if not cwd or not os.path.isdir(cwd) or not target:
            return self.error("rewind needs a known thread with a project and a checkpoint id", "bad_request", req=req)
        with self._state:
            if thread in self._busy_threads:
                return self.error(f"thread {thread} is running a turn", "busy", req=req)
            self._busy_threads[thread] = f"rewind {req}"
        try:
            with self._shadow_lock:
                self._rewind_locked(req, thread, cwd, target)
        finally:
            with self._state:
                self._busy_threads.pop(thread, None)

    def _rewind_locked(self, req: Optional[str], thread: str, cwd: str, target: str) -> None:
        known = self._git(cwd, "log", "--format=%H", f"--grep=hydra checkpoint thread={thread} ").stdout.decode().split()
        if target not in known:
            return self.error(f"{target!r} is not a checkpoint of thread {thread}", "bad_request", req=req)
        backup = self._snapshot(cwd, "turn=rewind-backup", thread)
        if not backup["ok"]:
            return self.error(f"could not save the current state first: {backup['reason']}", "internal", req=req)
        diff = self._git(cwd, "diff", "--name-status", "--no-renames", "-z", backup["id"], target)
        changes = diff.stdout.decode("utf-8", "replace").split("\x00")
        restore, remove = [], []
        for status, path in zip(changes[0::2], changes[1::2]):
            (remove if status == "D" else restore).append(path)
        root = os.path.realpath(cwd)
        for path in remove:
            full = os.path.realpath(os.path.join(cwd, path))
            if full.startswith(root + os.sep) and os.path.isfile(full):
                os.remove(full)
        if restore:
            done = self._git(cwd, "restore", f"--source={target}", "--worktree", "--pathspec-from-file=-", "--pathspec-file-nul",
                             stdin=("\x00".join(restore) + "\x00").encode("utf-8"))
            if done.returncode:
                return self.error(f"restore failed: {done.stderr.decode('utf-8', 'replace').strip()[:300]}", "internal", req=req)
        self.emit({"type": "rewind_result", "id": req, "thread": thread, "checkpoint": target, "backup": backup["id"],
                   "restored": sorted(restore), "removed": sorted(remove)})

    # approval rules

    def _rule_files(self, thread: str) -> Dict[str, str]:
        import hashlib
        from pathlib import Path

        home = Path.home() / ".hydra"
        files = {"thread": str(home / "sessions" / thread / "rules.json")}
        cwd = _load_meta(thread).get("cwd")
        if cwd:
            digest = hashlib.sha1(os.path.realpath(cwd).encode("utf-8")).hexdigest()[:16]
            files["project"] = str(home / "projects" / digest / "rules.json")
        return files

    def _read_rules(self, thread: str) -> List[Dict[str, Any]]:
        found: List[Dict[str, Any]] = []
        for scope, path in self._rule_files(thread).items():
            try:
                with open(path, "r", encoding="utf-8") as handle:
                    data = json.load(handle)
            except (OSError, ValueError):
                continue
            found.extend({**r, "scope": scope} for r in data if isinstance(r, dict) and r.get("id") and r.get("tool"))
        return found

    def _write_rules(self, thread: str, scope: str, rules: List[Dict[str, Any]]) -> None:
        path = self._rule_files(thread)[scope]
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump([{k: v for k, v in r.items() if k != "scope"} for r in rules], handle)
        os.replace(tmp, path)

    def _match_rule(self, thread: str, tool: str, args_json: str) -> Optional[Dict[str, Any]]:
        command = ""
        if tool == "run_command":
            args = _args(args_json)
            command = str(args.get("command", "")) if isinstance(args, dict) else ""
        with self._rules_lock:
            rules = self._read_rules(thread)
        for rule in rules:
            if rule["tool"] != tool:
                continue
            prefix = rule.get("prefix")
            if prefix is None:
                return rule
            if tool == "run_command" and _command_has_prefix(command, prefix):
                return rule
        return None

    def _add_rule(self, asked: tuple, prefix: Any, scope: Any) -> Optional[str]:
        import uuid

        thread, tool, args_json = asked
        if scope not in ("thread", "project"):
            return "scope must be thread or project"
        if prefix is not None and (not isinstance(prefix, str) or not prefix.strip() or tool != "run_command"):
            return "prefix must be a non-empty string and only applies to run_command"
        if prefix is not None:
            args = _args(args_json)
            if not isinstance(args, dict) or not _command_has_prefix(str(args.get("command", "")), prefix):
                return "prefix must match the command being approved"
        if scope not in self._rule_files(thread):
            return "this thread has no project yet"
        with self._rules_lock:
            mine = [r for r in self._read_rules(thread) if r["scope"] == scope]
            mine.append({"id": uuid.uuid4().hex[:8], "tool": tool, "prefix": prefix.strip() if prefix else None})
            self._write_rules(thread, scope, mine)
        return None

    def _on_rules(self, msg: Dict[str, Any]) -> None:
        req = _text(msg.get("id"))
        thread = "".join(c for c in str(msg.get("thread") or "") if c.isalnum() or c in "-_.")
        op = msg.get("op", "list")
        if not thread or op not in ("list", "remove"):
            return self.error("thread must be set and op must be list or remove", "bad_request", req=req)
        with self._rules_lock:
            if op == "remove":
                rid = _text(msg.get("rule"))
                for scope in self._rule_files(thread):
                    kept = [r for r in self._read_rules(thread) if r["scope"] == scope and r["id"] != rid]
                    self._write_rules(thread, scope, kept)
            rules = self._read_rules(thread)
        self.emit({"type": "rules_result", "id": req, "thread": thread, "rules": rules})

    def _mcp_registry(self, cwd: str) -> Any:
        """The MCP registry for a project, built from its config and reused while the config file is unchanged."""
        from hydra_cli.mcp_registry import McpRegistry

        config = next((p for p in (os.path.join(cwd, ".hydra", "mcp_servers.json"),
                                   os.path.join(os.path.expanduser("~"), ".hydra", "mcp_servers.json"))
                       if os.path.isfile(p)), None)
        if config is None:
            return None
        config = os.path.realpath(config)
        stamp = os.stat(config).st_mtime_ns
        with self._mcp_lock:
            cached = self._mcp.get(config)
            if cached is not None and cached[0] == stamp:
                return cached[1]
            if cached is not None:
                cached[1].shutdown()
            try:
                registry = McpRegistry(config_path=config, auto_load=False)
            except (OSError, ValueError) as exc:
                registry = McpRegistry(auto_load=False)
                registry.config_path = config
                registry.errors["config"] = f"{type(exc).__name__}: {exc}"
            registry.get_tools()
            self._mcp[config] = (stamp, registry)
            return registry

    def _on_mcp(self, msg: Dict[str, Any]) -> None:
        req = _text(msg.get("id"))
        cwd = msg.get("cwd")
        if not isinstance(cwd, str) or not os.path.isdir(cwd):
            return self.error("mcp needs cwd, an existing directory", "bad_request", req=req)
        registry = self._mcp_registry(cwd)
        servers: List[Dict[str, Any]] = []
        if registry is not None:
            with self._mcp_lock:
                if msg.get("refresh"):
                    registry.get_tools(refresh=True)
                tools = registry.get_tools()
                names = sorted(set(registry._server_configs) | set(registry.errors))
                for name in names:
                    servers.append({"name": name, "ok": name not in registry.errors, "error": registry.errors.get(name),
                                    "tools": [t["name"] for t in tools if t.get("_server") == name]})
        self.emit({"type": "mcp_status", "id": req, "config": registry.config_path if registry else None,
                   "servers": servers})

    def _on_tasks(self, msg: Dict[str, Any]) -> None:
        self.emit({"type": "task_list", "id": _text(msg.get("id")), "tasks": [_task_view(x) for x in self._tasks.list()]})

    def _on_task_stop(self, msg: Dict[str, Any]) -> None:
        req = _text(msg.get("id"))
        task = msg.get("task")
        if not isinstance(task, str) or task not in {x["task"] for x in self._tasks.list()}:
            return self.error(f"no task {task!r}", "bad_request", req=req)
        self._tasks.stop(task)
        self._on_tasks(msg)

    def _on_skills(self, msg: Dict[str, Any]) -> None:
        listed = [{"id": sk["id"], "keys": sk["keys"], "title": (sk["body"].splitlines() or [""])[0]} for sk in _skills()]
        self.emit({"type": "skill_list", "id": _text(msg.get("id")), "skills": listed})

    def _on_sessions(self, msg: Dict[str, Any]) -> None:
        from pathlib import Path

        root = Path.home() / ".hydra" / "sessions"
        found: List[Dict[str, Any]] = []
        if root.is_dir():
            for entry in sorted(root.iterdir()):
                db = entry / "ledger.db"
                if db.is_file():
                    meta = _load_meta(entry.name)
                    found.append({"thread": entry.name, "updated": int(db.stat().st_mtime), "cwd": meta.get("cwd"),
                                  "mode": meta.get("mode"), "model": meta.get("model"),
                                  "permission": meta.get("permission"), "title": meta.get("title")})
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
        self.emit({"type": "history_result", "id": req, "thread": thread, "turns": turns, "meta": _load_meta(thread)})

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
        rule: Optional[Dict[str, Any]] = None
        if permission == "plan":
            decision, reason = ("y", "plan mode allows read only tools") if tool in READ_ONLY_TOOLS else ("n", "plan mode allows read only tools")
        elif tool in READ_ONLY_TOOLS and permission != "manual":
            decision, reason = "y", f"{permission} mode"
        elif permission == "accept_edits" and tool in EDIT_TOOLS:
            decision, reason = "y", "accept_edits mode"
        elif (rule := self._match_rule(thread, tool, args_json)) is not None:
            decision, reason = "y", f"rule {rule['id']}"
        else:
            decision, reason = "", ""
        if decision:
            out = {"type": "auto_decision", "turn": turn, "tool": tool,
                   "decision": "allow" if decision == "y" else "skip", "reason": reason}
            if rule is not None:
                out["rule"] = rule["id"]
            self.emit(out)
            return decision
        with self._state:
            if t.cancel.is_set():
                return "q"
            self._approvals += 1
            req = f"a{self._approvals}"
            t.pending = req
            t.asked = (thread, tool, args_json)
            self._requests[req] = t
            t.gate.clear()
        self.emit({"type": "approval_request", "turn": turn, "id": req, "tool": tool, "arguments": _args(args_json)})
        t.gate.wait()
        return t.decision

    def _run_turn(self, t: _Turn, msg: Dict[str, Any]) -> None:
        turn, thread = t.id, t.thread
        from hydra_cli.agent import CANCELLED_TEXT, run_agent_loop, set_retry_sink, set_usage_sink
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
        set_retry_sink(lambda info: self.emit({"type": "retry", "turn": turn, **info}))
        self._tasks.owner.set({"thread": thread, "turn": turn})
        permission = t.permission
        set_approval_handler(lambda tool, args_json: self._approve(t, tool, args_json))
        set_diff_sink(lambda path, text: self.emit({"type": "diff", "turn": turn, "path": path, "diff": text}))
        set_tool_start_sink(start)
        set_tool_sink(result)
        final: Dict[str, Any]
        try:
            if msg.get("checkpoint", True):
                self.emit({"type": "checkpoint", "turn": turn, "thread": thread, **self._snapshot(msg["cwd"], f"turn={turn}", thread)})
            from hydra_cli.agent import find_project_rules

            chosen = [sk for sk in _skills() if sk["id"] in (msg.get("skills") or [])]
            extra = "\n\n".join(f"[SKILL: {sk['id']}]\n{sk['body']}" for sk in chosen)
            mcp = self._mcp_registry(msg["cwd"])
            with self._mcp_lock:
                mcp_tools = [tool["name"] for tool in mcp.get_tools()] if mcp else []
                mcp_errors = dict(mcp.errors) if mcp else {}
            self.emit({"type": "context", "turn": turn, "rules": find_project_rules(msg["cwd"]),
                       "skills": [sk["id"] for sk in chosen], "mcp_tools": mcp_tools, "mcp_errors": mcp_errors})
            answer = run_agent_loop(
                alias=alias, prompt=msg["prompt"], session_id=thread, resume_session=True, cwd=msg["cwd"],
                steer_mode=permission != "bypass", ledger=ledger, strategy=msg.get("mode", "recall"),
                on_token=lambda text: self.emit({"type": "token", "turn": turn, "text": text}),
                max_context_chars=msg.get("context_chars") or get_context_window(alias) * 4,
                cancel=t.cancel,
                extra_context=extra,
                registry=mcp,
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
            set_retry_sink(None)
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


def _meta_path(thread: str) -> str:
    from pathlib import Path

    return str(Path.home() / ".hydra" / "sessions" / thread / "thread.json")


def _load_meta(thread: str) -> Dict[str, Any]:
    """What the app chose for a thread last time: cwd, mode, model, permission, title. Empty when the thread is new."""
    try:
        with open(_meta_path(thread), "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_meta(thread: str, meta: Dict[str, Any]) -> None:
    path = _meta_path(thread)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(meta, handle)
    os.replace(tmp, path)


_SHELL_CONTROL = ("&&", "||", ";", "|", "`", "$(", "\n", ">", "<", "&")


def _command_has_prefix(command: str, prefix: str) -> bool:
    """True when the command starts with the prefix at a word boundary and chains nothing else after it."""
    command, prefix = command.strip(), prefix.strip()
    if not prefix or any(token in command for token in _SHELL_CONTROL):
        return False
    return command == prefix or command.startswith(prefix + " ")


def _task_view(info: Dict[str, Any]) -> Dict[str, Any]:
    keys = ("task", "thread", "turn", "command", "cwd", "pid", "state", "exit_code", "started", "ended", "adopted")
    return {key: info.get(key) for key in keys}


def _skills() -> List[Dict[str, Any]]:
    from hydra_cli.alice_gate import load_skills

    return load_skills()


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
