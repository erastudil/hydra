"""Main window: sidebars left, transcript and composer center, diff and approval tabs right."""

from __future__ import annotations

import os
from typing import Any, Dict, Optional

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QFileDialog, QMainWindow, QSplitter, QTabWidget, QVBoxLayout, QWidget,
)

from hydra_cli.config import DEFAULT_ORCHESTRATOR_MODEL
from hydra_cli.gui import motion
from hydra_cli.gui.panes import ApprovalPane, Composer, DiffPane, Sidebar
from hydra_cli.gui.store import Store
from hydra_cli.gui.transcript import TranscriptView
from hydra_cli.gui.worker import AgentWorker


class MainWindow(QMainWindow):
    _start = Signal(dict)

    def __init__(self, store: Optional[Store] = None) -> None:
        super().__init__()
        self.setWindowTitle("Hydra")
        self.resize(1360, 860)
        self.store = store or Store()
        self.project_id: Optional[str] = None
        self.thread_id: Optional[str] = None
        self.busy = False
        self._streaming = False

        self.sidebar = Sidebar()
        self.transcript = TranscriptView()
        self.composer = Composer()
        self.diffs = DiffPane()
        self.approval = ApprovalPane()
        self.tabs = QTabWidget()
        self.tabs.addTab(self.diffs, "Changes")
        self.tabs.addTab(self.approval, "Approval")

        center = QWidget()
        cl = QVBoxLayout(center)
        cl.setContentsMargins(16, 0, 16, 16)
        cl.setSpacing(12)
        cl.addWidget(self.transcript, 1)
        cl.addWidget(self.composer)

        split = QSplitter(Qt.Horizontal)
        split.addWidget(self.sidebar)
        split.addWidget(center)
        split.addWidget(self.tabs)
        split.setSizes([260, 720, 380])
        self.sidebar.setMinimumWidth(200)
        self.tabs.setMinimumWidth(280)
        split.setStretchFactor(1, 1)
        split.setChildrenCollapsible(False)
        self.setCentralWidget(split)
        self.split = split
        self._side_width = 260
        QShortcut(QKeySequence("Ctrl+B"), self, activated=self.toggle_sidebar)
        self.tabs.currentChanged.connect(lambda _i: motion.fade_in(self.tabs.currentWidget()))

        self.composer.model.addItem(DEFAULT_ORCHESTRATOR_MODEL)
        self.composer.model.setCurrentText(self.store.data["settings"].get("model", DEFAULT_ORCHESTRATOR_MODEL))

        self.thread = QThread(self)
        self.worker = AgentWorker()
        self.worker.moveToThread(self.thread)
        self._start.connect(self.worker.run_turn)
        self.worker.token.connect(self._on_token)
        self.worker.tool.connect(self._on_tool)
        self.worker.diff.connect(self._on_diff)
        self.worker.approval.connect(self._on_approval)
        self.worker.finished.connect(self._on_finished)
        self.thread.start()

        self.sidebar.add_project.connect(self.pick_project)
        self.sidebar.new_thread.connect(self.create_thread)
        self.sidebar.project_selected.connect(self.select_project)
        self.sidebar.thread_selected.connect(self.select_thread)
        self.composer.submitted.connect(self.submit)
        self.composer.stop_requested.connect(self.worker.cancel, Qt.DirectConnection)
        self.approval.decided.connect(self.worker.answer_approval, Qt.DirectConnection)
        self.composer.mode.currentIndexChanged.connect(self._mode_changed)

        self._restore()

    def toggle_sidebar(self) -> None:
        """Collapse or restore the sidebar with an eased width glide."""
        sizes = self.split.sizes()
        now = sizes[0]
        open_ = now > 0
        if open_:
            self._side_width = now
        end = 0 if open_ else self._side_width
        self.sidebar.setMinimumWidth(0)

        def apply(width: int) -> None:
            cur = self.split.sizes()
            self.split.setSizes([width, cur[1] + cur[0] - width, cur[2]])

        def done() -> None:
            if end:
                self.sidebar.setMinimumWidth(200)

        motion.glide(self.split, now, end, apply, name="slow", done=done)

    # state
    def _restore(self) -> None:
        projects = self.store.projects()
        active_t = self.store.thread(self.store.data.get("active_thread") or "")
        pid = active_t["project_id"] if active_t else (projects[0]["id"] if projects else None)
        self.project_id = pid
        self.sidebar.set_projects(projects, pid)
        if pid:
            self._refresh_threads(active_t["id"] if active_t else None)
            if active_t:
                self.select_thread(active_t["id"])

    def _refresh_threads(self, active: Optional[str]) -> None:
        self.sidebar.set_threads(self.store.threads(self.project_id) if self.project_id else [], active)

    def pick_project(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Add project folder")
        if path:
            self.add_project(path)

    def add_project(self, path: str) -> Dict[str, Any]:
        proj = self.store.add_project(path)
        self.project_id = proj["id"]
        self.sidebar.set_projects(self.store.projects(), proj["id"])
        self._refresh_threads(None)
        return proj

    def select_project(self, pid: str) -> None:
        self.project_id = pid
        self._refresh_threads(None)

    def create_thread(self) -> Optional[Dict[str, Any]]:
        if not self.project_id:
            return None
        thread = self.store.new_thread(self.project_id, self.composer.mode.currentData())
        self._refresh_threads(thread["id"])
        self.select_thread(thread["id"])
        return thread

    def select_thread(self, tid: str) -> None:
        thread = self.store.thread(tid)
        if thread is None or self.busy:
            return
        self.thread_id = tid
        self.store.data["active_thread"] = tid
        self.store.save()
        self.transcript.load(thread["messages"])
        self.diffs.clear()
        for d in thread["diffs"]:
            self.diffs.add(d["path"], d["text"])
        self.composer.set_mode(thread["mode"])

    def _mode_changed(self) -> None:
        thread = self.store.thread(self.thread_id or "")
        if thread is not None:
            thread["mode"] = self.composer.mode.currentData()
            self.store.save()

    # turns
    def submit(self, text: str) -> None:
        if self.busy or not self.project_id:
            return
        if self.thread_id is None:
            self.create_thread()
        thread = self.store.thread(self.thread_id)
        project = self.store.project(self.project_id)
        self.store.add_message(thread["id"], "user", text)
        self.transcript.line("user", text)
        self.store.data["settings"]["model"] = self.composer.model.currentText().strip()
        self._set_busy(True)
        self._answer_parts: list = []
        self._start.emit({
            "prompt": text,
            "alias": self.composer.model.currentText().strip() or DEFAULT_ORCHESTRATOR_MODEL,
            "session_id": thread["session_id"],
            "cwd": project["path"],
            "mode": thread["mode"],
            "approve": self.composer.approve.isChecked(),
        })
        self._refresh_threads(thread["id"])

    def _set_busy(self, busy: bool) -> None:
        self.busy = busy
        self.composer.set_busy(busy)

    def _on_token(self, text: str) -> None:
        if not self._streaming:
            self._streaming = True
            self.transcript.begin_assistant()
        self._answer_parts.append(text)
        self.transcript.stream(text)

    def _on_tool(self, name: str, args: str, is_error: bool, out: str) -> None:
        summary = f"{name} {args[:160]}" + (" (error)" if is_error else "")
        self.transcript.line("tool", summary)

    def _on_diff(self, path: str, text: str) -> None:
        self.diffs.add(path, text)
        if self.thread_id:
            self.store.add_diff(self.thread_id, path, text)

    def _on_approval(self, tool: str, args: str) -> None:
        self.approval.request(tool, args)
        self.tabs.setCurrentWidget(self.approval)

    def _on_finished(self, text: str, ok: bool) -> None:
        if self._streaming:
            self.transcript.end_assistant()
            self._streaming = False
        if ok:
            final = "".join(self._answer_parts) or text
            if not self._answer_parts and text:
                self.transcript.line("assistant", text)
            self.store.add_message(self.thread_id, "assistant", final)
        else:
            self.transcript.line("error", text)
            self.store.add_message(self.thread_id, "error", text)
        self._set_busy(False)

    def closeEvent(self, event) -> None:  # noqa: N802
        self.worker.cancel()
        self.thread.quit()
        self.thread.wait(3000)
        self.store.save()
        super().closeEvent(event)
