"""Frame probe: stream synthetic tokens into the transcript while timing UI-thread event-loop gaps."""

from __future__ import annotations

import json
import time
from typing import Any, Dict, List

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from hydra_cli.gui.window import MainWindow


def measure(win: MainWindow, seconds: float = 2.0, chunk: int = 240, tick_ms: int = 4) -> Dict[str, Any]:
    """Return gap statistics (ms) between 8 ms heartbeat ticks while text streams at roughly 60 KB/s."""
    app = QApplication.instance()
    gaps: List[float] = []
    last = [time.perf_counter()]
    sent = [0]
    end = time.perf_counter() + seconds
    text = ("hydra streams tokens through a batched flush " * 8)[:chunk]

    def beat() -> None:
        now = time.perf_counter()
        gaps.append((now - last[0]) * 1000.0)
        last[0] = now

    def feed() -> None:
        win.transcript.stream(text)
        sent[0] += len(text)

    beat_t = QTimer()
    beat_t.setInterval(8)
    beat_t.timeout.connect(beat)
    feed_t = QTimer()
    feed_t.setInterval(tick_ms)
    feed_t.timeout.connect(feed)
    win.transcript.begin_assistant()
    beat_t.start()
    feed_t.start()
    while time.perf_counter() < end:
        app.processEvents()
        time.sleep(0.001)
    feed_t.stop()
    beat_t.stop()
    win.transcript.end_assistant()
    gaps.sort()
    n = len(gaps)
    return {
        "ticks": n,
        "chars": sent[0],
        "p50_ms": round(gaps[n // 2], 2),
        "p95_ms": round(gaps[int(n * 0.95)], 2),
        "max_ms": round(max(gaps), 2),
        "fps_equiv": round(1000.0 / max(gaps[n // 2], 1e-3), 1),
        "doc_chars": len(win.transcript.toPlainText()),
    }


def write(win: MainWindow, out_path: str) -> Dict[str, Any]:
    stats = measure(win)
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(stats, fh)
    return stats
