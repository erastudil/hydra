"""Transcript view. Tokens are buffered and flushed on a timer: one append per tick, never a full relayout."""

from __future__ import annotations

from typing import List

from PySide6.QtCore import QTimer
from PySide6.QtGui import QColor, QFont, QTextBlockFormat, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import QPlainTextEdit

from hydra_cli.gui import motion, theme


class TranscriptView(QPlainTextEdit):
    FLUSH_MS = 33

    def __init__(self) -> None:
        super().__init__()
        self.setReadOnly(True)
        self.document().setIndentWidth(theme.SPACE['md'])
        self._card = ""
        self._quiet = False
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
        stick = bar.value() >= bar.maximum() - 4 or motion.is_gliding(bar)
        cur = self.textCursor()
        cur.movePosition(QTextCursor.End)
        cur.insertText(text, self._fmt(color))
        if stick:
            motion.glide_scroll(self)

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

    def _spacer(self, bg: str = "") -> None:
        """One thin empty block. QPlainTextEdit ignores block top and bottom margins, so gaps are real blocks."""
        fmt = QTextBlockFormat()
        if bg:
            fmt.setBackground(QColor(bg))
        gap = QTextCharFormat()
        gap.setFontPointSize(theme.SPACE["sm"] / 2)
        cur = self.textCursor()
        cur.movePosition(QTextCursor.End)
        cur.insertBlock(fmt, gap)

    def _block(self, role: str) -> None:
        """Start a message block: user and error messages sit on a tinted card, tool rows stay dim."""
        pal = theme.PALETTE
        card = {"user": pal["raised"], "error": pal["del"]}.get(role, "")
        fmt = QTextBlockFormat()
        fmt.setLineHeight(140, QTextBlockFormat.ProportionalHeight.value)
        fmt.setIndent(1 if card else 0)
        if card:
            fmt.setBackground(QColor(card))
        if not self.document().isEmpty():
            self._spacer()
        if card:
            self._spacer(card)
        cur = self.textCursor()
        cur.movePosition(QTextCursor.End)
        if self.document().isEmpty():
            cur.setBlockFormat(fmt)
        else:
            cur.insertBlock(fmt)
        self._card = card
        self._block_top = self._tail_y()

    def _tail_y(self) -> int:
        cur = self.textCursor()
        cur.movePosition(QTextCursor.End)
        return self.cursorRect(cur).top()

    def _arrive(self, top: int) -> None:
        """Fade the newest message in. Called once per message, never per streamed token."""
        if self._quiet:
            return
        bar = self.verticalScrollBar()
        follows = bar.value() >= bar.maximum() - 4 or motion.is_gliding(bar)
        final_top = top - (bar.maximum() - bar.value()) if follows else top
        motion.reveal(self, max(0, final_top), theme.PALETTE["bg"])

    def _label(self, label: str, color: str) -> None:
        fmt = self._fmt(color)
        fmt.setFontWeight(QFont.DemiBold)
        fmt.setFontPointSize(theme.FONTS["small_pt"])
        cur = self.textCursor()
        cur.movePosition(QTextCursor.End)
        cur.insertText(label.upper() + "\n", fmt)

    def line(self, role: str, text: str) -> None:
        self.flush()
        pal = theme.PALETTE
        label = {"user": "you", "assistant": "hydra", "tool": "tool", "error": "error"}.get(role, role)
        label_color = {"user": pal["accent"], "assistant": pal["accent"], "error": pal["danger"]}.get(role, pal["muted"])
        body_color = {"tool": pal["muted"], "error": pal["text"]}.get(role, pal["text"])
        self._block(role)
        self._label(label, label_color)
        self._write(text, body_color)
        if self._card:
            self._spacer(self._card)
        self._arrive(self._block_top)

    def begin_assistant(self) -> None:
        self.flush()
        self._block("assistant")
        self._label("hydra", theme.PALETTE["accent"])

    def end_assistant(self) -> None:
        self.flush()

    def load(self, messages: List[dict]) -> None:
        self.clear()
        self._quiet = True
        for m in messages:
            self.line(m["role"], m["text"])
        self._quiet = False
