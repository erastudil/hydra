"""
Terminal stream display helpers.

Keeps model output from fighting the terminal: no carriage-return overwrites,
no mouse-tracking / alternate-screen sequences leaking from deltas, and
coalesced flushes so token-by-token generation still feels live without
flicker or scrollback fights in IDE terminals.
"""

from __future__ import annotations

import re
import sys
import time
from typing import Iterable, Optional, TextIO

# C0 controls except tab/newline. Includes bare CR which causes the classic
# "tokens appear then vanish" overwrite effect when models emit \r\n mid-stream.
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
# Strip CSI / OSC / charset sequences so model output cannot enable mouse
# tracking, alternate screens, or cursor warps that break mousewheel scrollback.
_ANSI_RE = re.compile(
    r"\x1b\[[0-9;?]*[ -/]*[@-~]"  # CSI
    r"|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)"  # OSC
    r"|\x1b[()][0-9A-Za-z]"  # charset
    r"|\x1b[NO]."  # SS2/SS3
)


def sanitize_stream_text(text: str) -> str:
    """Make a model delta safe to append to a normal scrolling terminal."""
    if not text:
        return ""
    # Normalize CRLF / bare CR before dropping other controls.
    text = text.replace("\r\n", "\n").replace("\r", "")
    text = _ANSI_RE.sub("", text)
    return _CONTROL_RE.sub("", text)


class TokenStreamWriter:
    """Append-only streaming writer with short coalesce windows.

    Flushing every tiny delta makes some terminals (notably IDE integrated
    terminals) redraw aggressively so earlier tokens appear to vanish. Batching
    for ~16ms or until a newline / size threshold keeps output live while
    preserving normal mousewheel scrollback.
    """

    def __init__(
        self,
        stream: Optional[TextIO] = None,
        *,
        interval_s: float = 0.016,
        max_buffer: int = 64,
    ) -> None:
        self._stream = stream if stream is not None else sys.stdout
        self._interval_s = max(0.0, float(interval_s))
        self._max_buffer = max(1, int(max_buffer))
        self._buf: list[str] = []
        self._buffered = 0
        # Start the coalesce clock now so the first tiny delta does not flush
        # immediately (monotonic()-0 would always exceed the interval).
        self._last_flush = time.monotonic()
        self.emitted = False

    def write(self, text: str) -> int:
        clean = sanitize_stream_text(text)
        if not clean:
            return 0
        self._buf.append(clean)
        self._buffered += len(clean)
        self.emitted = True
        now = time.monotonic()
        if (
            "\n" in clean
            or self._buffered >= self._max_buffer
            or (now - self._last_flush) >= self._interval_s
        ):
            self.flush()
        return len(clean)

    def flush(self) -> None:
        if not self._buf:
            return
        payload = "".join(self._buf)
        self._buf.clear()
        self._buffered = 0
        self._stream.write(payload)
        self._stream.flush()
        self._last_flush = time.monotonic()

    def finish(self, trailing_newline: bool = True) -> None:
        """Flush remaining bytes and optionally end the assistant turn on its own line."""
        self.flush()
        if trailing_newline:
            self._stream.write("\n")
            self._stream.flush()

    def write_all(self, chunks: Iterable[str], trailing_newline: bool = True) -> bool:
        for chunk in chunks:
            self.write(chunk)
        self.finish(trailing_newline=trailing_newline)
        return self.emitted
