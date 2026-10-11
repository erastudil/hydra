"""Agent worker: runs hydra_cli.agent.run_agent_loop off the UI thread and bridges events to signals."""

from __future__ import annotations

import json
import threading
from typing import Any, Dict, Optional

from PySide6.QtCore import QObject, Signal

from hydra_cli.agent import CANCELLED_TEXT, run_agent_loop
from hydra_cli.config import DEFAULT_ORCHESTRATOR_MODEL, get_context_window
from hydra_cli.context import SessionContextLedger
from hydra_cli.ui import set_approval_handler, set_diff_sink, set_tool_sink


class AgentWorker(QObject):
    """Lives in a QThread. Call run_turn via a queued signal; never touches widgets."""

    token = Signal(str)
    tool = Signal(str, str, bool, str)
    diff = Signal(str, str)
    approval = Signal(str, str)
    finished = Signal(str, bool)  # answer or error text, ok

    def __init__(self) -> None:
        super().__init__()
        self._decision = ""
        self._gate = threading.Event()
        self._ledgers: Dict[str, SessionContextLedger] = {}
        self._cancel = threading.Event()

    # called from the GUI thread
    def answer_approval(self, choice: str) -> None:
        self._decision = choice
        self._gate.set()

    def cancel(self) -> None:
        """Stop the running turn. Also releases a pending approval so the thread can exit."""
        self._cancel.set()
        self._decision = "q"
        self._gate.set()

    # runs in the worker thread
    def _approve(self, tool: str, args_json: str) -> str:
        self._gate.clear()
        self.approval.emit(tool, args_json)
        self._gate.wait()
        return self._decision

    def run_turn(self, req: Dict[str, Any]) -> None:
        self._cancel.clear()
        sid = req["session_id"]
        ledger = self._ledgers.get(sid)
        if ledger is None:
            ledger = self._ledgers[sid] = SessionContextLedger(session_id=sid)
        alias = req.get("alias") or DEFAULT_ORCHESTRATOR_MODEL
        set_approval_handler(self._approve)
        set_diff_sink(lambda path, text: self.diff.emit(path, text))
        set_tool_sink(lambda name, args, err, out: self.tool.emit(name, args, err, out[:4000]))
        try:
            answer = run_agent_loop(
                alias=alias,
                prompt=req["prompt"],
                session_id=sid,
                resume_session=True,
                cwd=req["cwd"],
                steer_mode=bool(req.get("approve")),
                ledger=ledger,
                strategy=req.get("mode", "recall"),
                on_token=lambda t: self.token.emit(t),
                max_context_chars=get_context_window(alias) * 4,
                cancel=self._cancel,
            )
            self.finished.emit(answer or "", answer != CANCELLED_TEXT)
        except Exception as exc:  # surfaced to the transcript, not swallowed
            self.finished.emit(f"{type(exc).__name__}: {exc}", False)
        finally:
            set_approval_handler(None)
            set_diff_sink(None)
            set_tool_sink(None)
