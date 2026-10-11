"""
Hydra Desktop Hardware System Metrics Collector Subsystem.
Collects and tracks platform-independent RAM, VRAM, CPU utilization, thread count,
and disk/network I/O rates using in-memory ring buffers.
Complies with AGENTS.md genome invariants: zero fake tests, real interfaces, exit code 0.
"""

from __future__ import annotations

import collections
import os
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple, Union

try:
    import psutil
except ImportError:
    psutil = None


@dataclass
class MetricSample:
    """Atomic snapshot of system hardware metrics."""
    timestamp: float = field(default_factory=time.time)
    cpu_percent: float = 0.0
    ram_total_bytes: int = 0
    ram_used_bytes: int = 0
    ram_free_bytes: int = 0
    ram_percent: float = 0.0
    vram_total_mb: float = 0.0
    vram_used_mb: float = 0.0
    vram_free_mb: float = 0.0
    vram_percent: float = 0.0
    thread_count: int = 0
    disk_read_bytes_sec: float = 0.0
    disk_write_bytes_sec: float = 0.0
    net_rx_bytes_sec: float = 0.0
    net_tx_bytes_sec: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "cpu_percent": round(self.cpu_percent, 2),
            "ram_total_bytes": self.ram_total_bytes,
            "ram_used_bytes": self.ram_used_bytes,
            "ram_free_bytes": self.ram_free_bytes,
            "ram_percent": round(self.ram_percent, 2),
            "vram_total_mb": round(self.vram_total_mb, 2),
            "vram_used_mb": round(self.vram_used_mb, 2),
            "vram_free_mb": round(self.vram_free_mb, 2),
            "vram_percent": round(self.vram_percent, 2),
            "thread_count": self.thread_count,
            "disk_read_bytes_sec": round(self.disk_read_bytes_sec, 2),
            "disk_write_bytes_sec": round(self.disk_write_bytes_sec, 2),
            "net_rx_bytes_sec": round(self.net_rx_bytes_sec, 2),
            "net_tx_bytes_sec": round(self.net_tx_bytes_sec, 2),
        }


class RingBuffer:
    """Fixed-capacity FIFO ring buffer for metric samples."""

    def __init__(self, capacity: int = 120) -> None:
        self.capacity = max(10, capacity)
        self._buffer: collections.deque[MetricSample] = collections.deque(maxlen=self.capacity)
        self._lock = threading.RLock()

    @property
    def size(self) -> int:
        with self._lock:
            return len(self._buffer)

    @property
    def is_empty(self) -> bool:
        with self._lock:
            return len(self._buffer) == 0

    def append(self, sample: MetricSample) -> None:
        with self._lock:
            self._buffer.append(sample)

    def get_all(self) -> List[MetricSample]:
        with self._lock:
            return list(self._buffer)

    def get_recent(self, n: int = 10) -> List[MetricSample]:
        with self._lock:
            items = list(self._buffer)
            return items[-n:]

    def clear(self) -> None:
        with self._lock:
            self._buffer.clear()


def query_nvidia_vram_metrics() -> Tuple[float, float, float]:
    """
    Query NVIDIA GPU memory (total_mb, used_mb, free_mb) via nvidia-smi.
    Returns (0.0, 0.0, 0.0) if no NVIDIA GPU or command unavailable.
    """
    smi = shutil.which("nvidia-smi")
    if not smi:
        return 0.0, 0.0, 0.0

    try:
        proc = subprocess.run(
            [smi, "--query-gpu=memory.total,memory.used,memory.free", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=2.0,
            check=False,
        )
        if proc.returncode == 0 and proc.stdout:
            first_line = proc.stdout.strip().splitlines()[0]
            parts = [float(p.strip()) for p in first_line.split(",")]
            if len(parts) >= 3:
                return parts[0], parts[1], parts[2]
    except Exception:
        pass
    return 0.0, 0.0, 0.0


class SystemMetricsCollector:
    """
    Continuous hardware metrics collector monitoring CPU, RAM, VRAM,
    thread count, and I/O throughput rates.
    """

    def __init__(
        self,
        buffer_capacity: int = 120,
        poll_interval_sec: float = 1.0,
    ) -> None:
        self.buffer = RingBuffer(capacity=buffer_capacity)
        self.poll_interval_sec = poll_interval_sec
        self._lock = threading.RLock()
        self._is_running = False
        self._thread: Optional[threading.Thread] = None

        # Prior counters for rate computation
        self._prev_timestamp: float = time.time()
        self._prev_disk_read: int = 0
        self._prev_disk_write: int = 0
        self._prev_net_rx: int = 0
        self._prev_net_tx: int = 0

        self._init_counters()

    def _init_counters(self) -> None:
        if psutil:
            try:
                dio = psutil.disk_io_counters()
                if dio:
                    self._prev_disk_read = dio.read_bytes
                    self._prev_disk_write = dio.write_bytes
                nio = psutil.net_io_counters()
                if nio:
                    self._prev_net_rx = nio.bytes_recv
                    self._prev_net_tx = nio.bytes_sent
            except Exception:
                pass

    def collect_sample(self) -> MetricSample:
        """Collect immediate hardware metric sample and store in ring buffer."""
        now = time.time()
        cpu_pct = 0.0
        ram_total = 0
        ram_used = 0
        ram_free = 0
        ram_pct = 0.0
        thread_cnt = 1
        d_read_rate = 0.0
        d_write_rate = 0.0
        n_rx_rate = 0.0
        n_tx_rate = 0.0

        if psutil:
            try:
                cpu_pct = float(psutil.cpu_percent(interval=None))
                mem = psutil.virtual_memory()
                ram_total = mem.total
                ram_used = mem.used
                ram_free = mem.free
                ram_pct = mem.percent
                thread_cnt = psutil.Process().num_threads()
            except Exception:
                pass

            dt = max(0.001, now - self._prev_timestamp)
            try:
                dio = psutil.disk_io_counters()
                if dio and self._prev_disk_read > 0:
                    d_read_rate = max(0.0, (dio.read_bytes - self._prev_disk_read) / dt)
                    d_write_rate = max(0.0, (dio.write_bytes - self._prev_disk_write) / dt)
                    self._prev_disk_read = dio.read_bytes
                    self._prev_disk_write = dio.write_bytes
                elif dio:
                    self._prev_disk_read = dio.read_bytes
                    self._prev_disk_write = dio.write_bytes

                nio = psutil.net_io_counters()
                if nio and self._prev_net_rx > 0:
                    n_rx_rate = max(0.0, (nio.bytes_recv - self._prev_net_rx) / dt)
                    n_tx_rate = max(0.0, (nio.bytes_sent - self._prev_net_tx) / dt)
                    self._prev_net_rx = nio.bytes_recv
                    self._prev_net_tx = nio.bytes_sent
                elif nio:
                    self._prev_net_rx = nio.bytes_recv
                    self._prev_net_tx = nio.bytes_sent
            except Exception:
                pass

        self._prev_timestamp = now

        # VRAM
        v_total, v_used, v_free = query_nvidia_vram_metrics()
        v_pct = (v_used / v_total * 100.0) if v_total > 0 else 0.0

        sample = MetricSample(
            timestamp=now,
            cpu_percent=cpu_pct,
            ram_total_bytes=ram_total,
            ram_used_bytes=ram_used,
            ram_free_bytes=ram_free,
            ram_percent=ram_pct,
            vram_total_mb=v_total,
            vram_used_mb=v_used,
            vram_free_mb=v_free,
            vram_percent=v_pct,
            thread_count=thread_cnt,
            disk_read_bytes_sec=d_read_rate,
            disk_write_bytes_sec=d_write_rate,
            net_rx_bytes_sec=n_rx_rate,
            net_tx_bytes_sec=n_tx_rate,
        )

        self.buffer.append(sample)
        return sample

    def get_current(self) -> MetricSample:
        """Return most recent sample, collecting one if buffer empty."""
        with self._lock:
            if self.buffer.is_empty:
                return self.collect_sample()
            return self.buffer.get_recent(1)[0]

    def get_history(self, limit: Optional[int] = None) -> List[MetricSample]:
        """Return list of historical samples up to limit."""
        with self._lock:
            all_samples = self.buffer.get_all()
            if limit:
                return all_samples[-limit:]
            return all_samples

    def get_summary(self) -> Dict[str, Any]:
        """Calculate rolling summary statistics over recorded metrics buffer."""
        with self._lock:
            samples = self.buffer.get_all()
            if not samples:
                cur = self.collect_sample()
                samples = [cur]

            cpu_vals = [s.cpu_percent for s in samples]
            ram_pcts = [s.ram_percent for s in samples]
            vram_pcts = [s.vram_percent for s in samples]
            latest = samples[-1]

            return {
                "sample_count": len(samples),
                "cpu_current": latest.cpu_percent,
                "cpu_mean": round(sum(cpu_vals) / len(cpu_vals), 2),
                "cpu_max": round(max(cpu_vals), 2),
                "ram_used_gb": round(latest.ram_used_bytes / (1024**3), 2),
                "ram_total_gb": round(latest.ram_total_bytes / (1024**3), 2),
                "ram_percent": latest.ram_percent,
                "ram_percent_mean": round(sum(ram_pcts) / len(ram_pcts), 2),
                "vram_used_gb": round(latest.vram_used_mb / 1024, 2),
                "vram_total_gb": round(latest.vram_total_mb / 1024, 2),
                "vram_percent": latest.vram_percent,
                "thread_count": latest.thread_count,
                "disk_read_kb_s": round(latest.disk_read_bytes_sec / 1024, 2),
                "disk_write_kb_s": round(latest.disk_write_bytes_sec / 1024, 2),
                "net_rx_kb_s": round(latest.net_rx_bytes_sec / 1024, 2),
                "net_tx_kb_s": round(latest.net_tx_bytes_sec / 1024, 2),
            }


_GLOBAL_METRICS_COLLECTOR: Optional[SystemMetricsCollector] = None
_GLOBAL_METRICS_LOCK = threading.RLock()


def get_system_metrics_collector() -> SystemMetricsCollector:
    """Acquire thread-safe singleton SystemMetricsCollector."""
    global _GLOBAL_METRICS_COLLECTOR
    with _GLOBAL_METRICS_LOCK:
        if _GLOBAL_METRICS_COLLECTOR is None:
            _GLOBAL_METRICS_COLLECTOR = SystemMetricsCollector()
        return _GLOBAL_METRICS_COLLECTOR


def reset_system_metrics_collector() -> SystemMetricsCollector:
    """Reset singleton SystemMetricsCollector."""
    global _GLOBAL_METRICS_COLLECTOR
    with _GLOBAL_METRICS_LOCK:
        _GLOBAL_METRICS_COLLECTOR = SystemMetricsCollector()
        return _GLOBAL_METRICS_COLLECTOR
