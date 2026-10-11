"""Sidebar, composer, diff pane, approval pane."""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtGui import QColor, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (
    QComboBox, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QPlainTextEdit,
    QPushButton, QTabWidget, QVBoxLayout, QWidget,
)

from hydra_cli.context import CONTEXT_MODES
from hydra_cli.gui import theme

MODE_LABELS = {"recall": "Dynamic threading", "compact": "Compaction"}


class Sidebar(QWidget):
    """Project list over thread list. Persistence lives in Store; this only emits intent."""

    project_selected = Signal(str)
    thread_selected = Signal(str)
    add_project = Signal()
    new_thread = Signal()
    delete_thread = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        self.projects = QListWidget()
        self.threads = QListWidget()
        row = QHBoxLayout()
        row.addWidget(QLabel("Projects"))
        add = QPushButton("+")
        add.setFixedWidth(32)
        add.clicked.connect(self.add_project)
        row.addWidget(add)
        lay.addLayout(row)
        lay.addWidget(self.projects, 1)
        row2 = QHBoxLayout()
        row2.addWidget(QLabel("Threads"))
        new = QPushButton("+")
        new.setFixedWidth(32)
        new.clicked.connect(self.new_thread)
        row2.addWidget(new)
        lay.addLayout(row2)
        lay.addWidget(self.threads, 2)
        self.projects.currentItemChanged.connect(
            lambda cur, _prev: cur and self.project_selected.emit(cur.data(Qt.UserRole)))
        self.threads.currentItemChanged.connect(
            lambda cur, _prev: cur and self.thread_selected.emit(cur.data(Qt.UserRole)))

    def set_projects(self, projects: List[Dict[str, Any]], active: Optional[str]) -> None:
        self.projects.blockSignals(True)
        self.projects.clear()
        for p in projects:
            item = QListWidgetItem(p["name"])
            item.setData(Qt.UserRole, p["id"])
            item.setToolTip(p["path"])
            self.projects.addItem(item)
            if p["id"] == active:
                self.projects.setCurrentItem(item)
        self.projects.blockSignals(False)

    def set_threads(self, threads: List[Dict[str, Any]], active: Optional[str]) -> None:
        self.threads.blockSignals(True)
        self.threads.clear()
        for t in threads:
            item = QListWidgetItem(t["title"])
            item.setData(Qt.UserRole, t["id"])
            self.threads.addItem(item)
            if t["id"] == active:
                self.threads.setCurrentItem(item)
        self.threads.blockSignals(False)


class Composer(QWidget):
    """Input pane: Enter sends, Shift+Enter newline, model box, context mode, approval toggle."""

    submitted = Signal(str)
    stop_requested = Signal()

    def __init__(self) -> None:
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.input = QPlainTextEdit()
        self.input.setPlaceholderText("Ask Hydra to work on this project. Enter sends, Shift+Enter adds a line.")
        self.input.setFixedHeight(96)
        self.input.setFont(theme.mono())
        self.input.installEventFilter(self)
        lay.addWidget(self.input)
        row = QHBoxLayout()
        self.model = QComboBox()
        self.model.setEditable(True)
        self.model.setMinimumWidth(180)
        row.addWidget(self.model)
        self.mode = QComboBox()
        known = {name for name, _ in CONTEXT_MODES}
        for name in ("recall", "compact"):
            if name in known:
                self.mode.addItem(MODE_LABELS[name], name)
        row.addWidget(self.mode)
        self.approve = QPushButton("Approve tools")
        self.approve.setCheckable(True)
        self.approve.setChecked(True)
        row.addWidget(self.approve)
        row.addStretch(1)
        self.stop = QPushButton("Stop")
        self.stop.setObjectName("danger")
        self.stop.setEnabled(False)
        self.stop.clicked.connect(self.stop_requested)
        row.addWidget(self.stop)
        self.send = QPushButton("Send")
        self.send.setObjectName("primary")
        self.send.clicked.connect(self._submit)
        row.addWidget(self.send)
        lay.addLayout(row)

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        if obj is self.input and event.type() == QEvent.KeyPress:
            if event.key() in (Qt.Key_Return, Qt.Key_Enter) and not (event.modifiers() & Qt.ShiftModifier):
                self._submit()
                return True
        return super().eventFilter(obj, event)

    def _submit(self) -> None:
        text = self.input.toPlainText().strip()
        if text and self.send.isEnabled():
            self.input.clear()
            self.submitted.emit(text)

    def set_busy(self, busy: bool) -> None:
        self.send.setEnabled(not busy)
        self.stop.setEnabled(busy)

    def set_mode(self, mode: str) -> None:
        idx = self.mode.findData(mode)
        self.mode.setCurrentIndex(idx if idx >= 0 else 0)


class DiffPane(QPlainTextEdit):
    """File changes made by the agent, colored by unified diff markers."""

    def __init__(self) -> None:
        super().__init__()
        self.setReadOnly(True)
        self.setFont(theme.mono())
        self.setLineWrapMode(QPlainTextEdit.NoWrap)

    def add(self, path: str, text: str) -> None:
        cur = self.textCursor()
        cur.movePosition(QTextCursor.End)
        head = QTextCharFormat()
        head.setForeground(QColor(theme.PALETTE["accent"]))
        cur.insertText(f"--- {path}\n", head)
        for ln in text.splitlines():
            fmt = QTextCharFormat()
            if ln.startswith("+") and not ln.startswith("+++"):
                fmt.setBackground(QColor(theme.PALETTE["add"]))
            elif ln.startswith("-") and not ln.startswith("---"):
                fmt.setBackground(QColor(theme.PALETTE["del"]))
            elif ln.startswith("@@"):
                fmt.setForeground(QColor(theme.PALETTE["muted"]))
            cur.insertText(ln + "\n", fmt)
        cur.insertText("\n")


class ApprovalPane(QWidget):
    """Shows the pending tool call. Buttons answer the blocked agent thread."""

    decided = Signal(str)  # y, n, q

    def __init__(self) -> None:
        super().__init__()
        lay = QVBoxLayout(self)
        self.title = QLabel("No pending tool call")
        self.title.setObjectName("muted")
        self.body = QPlainTextEdit()
        self.body.setReadOnly(True)
        self.body.setFont(theme.mono())
        lay.addWidget(self.title)
        lay.addWidget(self.body, 1)
        row = QHBoxLayout()
        self.allow = QPushButton("Allow")
        self.allow.setObjectName("primary")
        self.deny = QPushButton("Skip")
        self.abort = QPushButton("Abort turn")
        self.abort.setObjectName("danger")
        for b, code in ((self.allow, "y"), (self.deny, "n"), (self.abort, "q")):
            b.clicked.connect(lambda _c=False, c=code: self._decide(c))
            row.addWidget(b)
        lay.addLayout(row)
        self._set_enabled(False)

    def _set_enabled(self, on: bool) -> None:
        for b in (self.allow, self.deny, self.abort):
            b.setEnabled(on)

    def request(self, tool: str, args_json: str) -> None:
        try:
            pretty = json.dumps(json.loads(args_json), indent=2)
        except ValueError:
            pretty = args_json
        self.title.setText(f"Approve tool : {tool}")
        self.body.setPlainText(pretty)
        self._set_enabled(True)

    def _decide(self, code: str) -> None:
        self._set_enabled(False)
        self.title.setText("No pending tool call")
        self.body.clear()
        self.decided.emit(code)
