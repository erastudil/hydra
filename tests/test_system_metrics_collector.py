"""
Integration test suite for Hydra Desktop System Metrics Collector.
Complies with AGENTS.md genome invariants: zero fake tests, real interfaces, exit code 0.
"""

from __future__ import annotations

import time
import pytest

from desktop.system_metrics_collector import (
    SystemMetricsCollector,
    RingBuffer,
    MetricSample,
    query_nvidia_vram_metrics,
    get_system_metrics_collector,
    reset_system_metrics_collector,
)


@pytest.fixture
def collector():
    c = SystemMetricsCollector(buffer_capacity=50)
    yield c
    c.buffer.clear()


def test_ring_buffer_fifo_eviction():
    """Verify ring buffer enforces capacity bound and FIFO eviction."""
    rb = RingBuffer(capacity=10)
    assert rb.is_empty is True
    assert rb.size == 0

    for i in range(15):
        s = MetricSample(timestamp=float(i), cpu_percent=float(i))
        rb.append(s)

    assert rb.size == 10
    assert rb.is_empty is False

    all_items = rb.get_all()
    assert len(all_items) == 10
    # First 5 items (0..4) evicted, remaining are 5..14
    assert all_items[0].cpu_percent == 5.0
    assert all_items[-1].cpu_percent == 14.0

    recent_3 = rb.get_recent(3)
    assert len(recent_3) == 3
    assert [s.cpu_percent for s in recent_3] == [12.0, 13.0, 14.0]

    rb.clear()
    assert rb.size == 0
    assert rb.is_empty is True


def test_collect_sample_and_system_metrics(collector: SystemMetricsCollector):
    """Verify real hardware metrics collected from operating system."""
    sample = collector.collect_sample()
    assert isinstance(sample, MetricSample)
    assert sample.ram_total_bytes > 0
    assert sample.ram_used_bytes > 0
    assert sample.ram_percent >= 0.0
    assert sample.thread_count >= 1

    d = sample.to_dict()
    assert "cpu_percent" in d
    assert "ram_total_bytes" in d
    assert "vram_total_mb" in d
    assert "disk_read_bytes_sec" in d


def test_collector_summary(collector: SystemMetricsCollector):
    """Verify rolling summary statistics computed over samples."""
    collector.collect_sample()
    collector.collect_sample()

    summary = collector.get_summary()
    assert summary["sample_count"] >= 2
    assert "cpu_current" in summary
    assert "cpu_mean" in summary
    assert summary["ram_total_gb"] > 0
    assert summary["thread_count"] >= 1


def test_query_vram_metrics_returns_valid_tuple():
    """Verify query_nvidia_vram_metrics returns 3-tuple of floats without throwing."""
    total, used, free = query_nvidia_vram_metrics()
    assert isinstance(total, float)
    assert isinstance(used, float)
    assert isinstance(free, float)
    assert total >= 0.0
    assert used >= 0.0
    assert free >= 0.0


def test_global_singleton_metrics_collector():
    """Verify singleton lifecycle for SystemMetricsCollector."""
    c1 = get_system_metrics_collector()
    c2 = get_system_metrics_collector()
    assert c1 is c2

    c3 = reset_system_metrics_collector()
    assert c3 is not c1
    assert get_system_metrics_collector() is c3
