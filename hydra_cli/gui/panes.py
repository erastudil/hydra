"""Sidebar, composer, diff pane, approval pane."""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtGui import QColor, QFont, QTextBlockFormat, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (
    QComboBox, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QPlainTextEdit,
    QPushButton, QTabWidget, QVBoxLayout, QWidget,
)

from hydra_cli.context import CONTEXT_MODES
from hydra_cli.gui import motion, theme

def _section(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("section")
    return label


MODE_LABELS = {"recall": "Dynamic threading", "compact": "Compaction", "sliding": "Sliding window"}


class Sidebar(QWidget):
    """Project list over thread list. Persistence lives in Store; this only emits intent."""

    project_selected = Signal(str)
    thread_selected = Signal(str)
    add_project = Signal()
    new_thread = Signal()
    delete_thread = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("sidebar")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(theme.SPACE["md"], theme.SPACE["md"], theme.SPACE["md"], theme.SPACE["md"])
        lay.setSpacing(theme.SPACE["xs"])
        self.projects = QListWidget()
        self.threads = QListWidget()
        for lst in (self.projects, self.threads):
            lst.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            lst.setTextElideMode(Qt.ElideRight)
        row = QHBoxLayout()
        row.addWidget(_section("PROJECTS"))
        add = QPushButton("+")
        add.setObjectName("icon")
        add.setToolTip("Add project")
        add.clicked.connect(self.add_project)
        row.addWidget(add)
        lay.addLayout(row)
        lay.addWidget(self.projects, 1)
        row2 = QHBoxLayout()
        row2.addWidget(_section("THREADS"))
        new = QPushButton("+")
        new.setObjectName("icon")
        new.setToolTip("New thread")
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
        lay.setSpacing(theme.SPACE["sm"])
        self.input = QPlainTextEdit()
        self.input.setPlaceholderText("Ask Hydra to work on this project. Enter sends, Shift+Enter adds a line.")
        self.input.setObjectName("input")
        self.input.setFixedHeight(96)
        self.input.setFont(theme.mono())
        self.input.installEventFilter(self)
        lay.addWidget(self.input)
        row = QHBoxLayout()
        row.setSpacing(theme.SPACE["sm"])
        self.model = QComboBox()
        self.model.setEditable(True)
        self.model.setMinimumWidth(180)
        row.addWidget(self.model)
        self.mode = QComboBox()
        for name, _note in CONTEXT_MODES:
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
        motion.HoverGlow(self.send, theme.PALETTE["ring"])
        motion.HoverGlow(self.input, theme.PALETTE["ring"], 14)

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
        self.setObjectName("diff")
        self.setReadOnly(True)
        self.setFont(theme.mono())
        self.setLineWrapMode(QPlainTextEdit.NoWrap)

    def add(self, path: str, text: str) -> None:
        pal = theme.PALETTE
        cur = self.textCursor()
        cur.movePosition(QTextCursor.End)
        head = QTextCharFormat()
        head.setForeground(QColor(pal["text"]))
        head.setFontWeight(QFont.DemiBold)
        head_block = QTextBlockFormat()
        head_block.setBackground(QColor(pal["raised"]))
        head_block.setTopMargin(theme.SPACE["md"])
        cur.insertBlock(head_block) if not self.document().isEmpty() else cur.setBlockFormat(head_block)
        cur.insertText(path, head)
        for ln in text.splitlines():
            blk = QTextBlockFormat()
            fmt = QTextCharFormat()
            if ln.startswith("+") and not ln.startswith("+++"):
                blk.setBackground(QColor(pal["add"]))
            elif ln.startswith("-") and not ln.startswith("---"):
                blk.setBackground(QColor(pal["del"]))
            elif ln.startswith("@@"):
                fmt.setForeground(QColor(pal["accent"]))
                blk.setTopMargin(theme.SPACE["xs"])
            elif ln.startswith(("---", "+++")):
                fmt.setForeground(QColor(pal["muted"]))
            cur.insertBlock(blk)
            cur.insertText(ln, fmt)


class ApprovalPane(QWidget):
    """Shows the pending tool call. Buttons answer the blocked agent thread."""

    decided = Signal(str)  # y, n, q

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("approval")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(theme.SPACE["lg"], theme.SPACE["md"], theme.SPACE["lg"], theme.SPACE["lg"])
        lay.setSpacing(theme.SPACE["sm"])
        self.title = QLabel("No pending tool call")
        self.title.setObjectName("muted")
        self.body = QPlainTextEdit()
        self.body.setObjectName("approvalBody")
        self.body.setReadOnly(True)
        self.body.setFont(theme.mono())
        lay.addWidget(self.title)
        lay.addWidget(self.body, 1)
        row = QHBoxLayout()
        row.setSpacing(theme.SPACE["sm"])
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
        self.title.setObjectName("title")
        self.title.style().unpolish(self.title)
        self.title.style().polish(self.title)
        self.title.setText(f"Approve tool : {tool}")
        self.body.setPlainText(pretty)
        self._set_enabled(True)
        motion.fade_in(self.body)
        motion.fade_in(self.title)

    def _decide(self, code: str) -> None:
        self._set_enabled(False)
        self.title.setObjectName("muted")
        self.title.style().unpolish(self.title)
        self.title.style().polish(self.title)
        self.title.setText("No pending tool call")
        self.body.clear()
        self.decided.emit(code)
