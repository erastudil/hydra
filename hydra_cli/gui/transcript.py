"""Transcript view. Tokens are buffered and flushed on a timer: one append per tick, never a full relayout."""

from __future__ import annotations

from typing import List

from PySide6.QtCore import QTimer
from PySide6.QtGui import QColor, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import QPlainTextEdit

from hydra_cli.gui import theme


class TranscriptView(QPlainTextEdit):
    FLUSH_MS = 33

    def __init__(self) -> None:
        super().__init__()
        self.setReadOnly(True)
        self.setFont(theme.mono())
        self.setLineWrapMode(QPlainTextEdit.WidgetWidth)
        self.setUndoRedoEnabled(False)
        self._pending: List[str] = []
        self._timer = QTimer(self)
        self._timer.setInterval(self.FLUSH_MS)
        self._timer.timeout.connect(self.flush)

    def _fmt(self, color: str) -> QTextCharFormat:
        fmt = QTextCharFormat()
        fmt.setForeground(QColor(color))
        return fmt

    def _write(self, text: str, color: str) -> None:
        bar = self.verticalScrollBar()
        stick = bar.value() >= bar.maximum() - 4
        cur = self.textCursor()
        cur.movePosition(QTextCursor.End)
        cur.insertText(text, self._fmt(color))
        if stick:
            bar.setValue(bar.maximum())

    def stream(self, text: str) -> None:
        self._pending.append(text)
        if not self._timer.isActive():
            self._timer.start()

    def flush(self) -> None:
        if self._pending:
            chunk = "".join(self._pending)
            self._pending.clear()
            self._write(chunk, theme.PALETTE["text"])
        else:
            self._timer.stop()

    def line(self, role: str, text: str) -> None:
        self.flush()
        color = {"user": theme.PALETTE["accent"], "error": theme.PALETTE["danger"]}.get(role, theme.PALETTE["muted"])
        if role == "assistant":
            color = theme.PALETTE["text"]
        label = {"user": "you", "assistant": "hydra", "tool": "tool", "error": "error"}.get(role, role)
        self._write(f"\n{label} : ", theme.PALETTE["muted"])
        self._write(text + "\n", color)

    def begin_assistant(self) -> None:
        self.flush()
        self._write("\nhydra : ", theme.PALETTE["muted"])

    def end_assistant(self) -> None:
        self.flush()
        self._write("\n", theme.PALETTE["text"])

    def load(self, messages: List[dict]) -> None:
        self.clear()
        for m in messages:
            self.line(m["role"], m["text"])
