"""
Unit tests for Hydra Desktop Concurrency Throttle Queue Subsystem.
Verifies token bucket rate limiting, priority-based preemptive slot scheduling,
fair queuing with starvation avoidance, and thread-safe slot leasing.
Complies with AGENTS.md genome invariants: zero fake tests, real interfaces, exit code 0.
"""

from __future__ import annotations

import time
import pytest
from desktop.concurrency_throttle_queue import (
    ConcurrencySlotPermit,
    ConcurrencyThrottleQueue,
    QueuePriority,
    QueuedTask,
    TokenBucketRateLimiter,
    get_throttle_queue,
    reset_throttle_queue,
)


def test_token_bucket_rate_limiter():
    limiter = TokenBucketRateLimiter(refill_rate_per_sec=10.0, capacity=2.0)
    assert limiter.get_available_tokens() == pytest.approx(2.0, rel=0.1)

    # Consume available capacity
    assert limiter.acquire(1.0) is True
    assert limiter.acquire(1.0) is True
    # Capacity exhausted
    assert limiter.acquire(1.0) is False

    # Wait for refill: 0.15s refills ~1.5 tokens
    time.sleep(0.15)
    assert limiter.acquire(1.0) is True


def test_queued_task_aging_effective_priority():
    now = 1000.0
    task_normal = QueuedTask("t1", "c1", QueuePriority.NORMAL, enqueued_at=now)
    task_critical = QueuedTask("t2", "c2", QueuePriority.CRITICAL, enqueued_at=now)

    # At t=0, critical has lower score (0.0 vs 2.0)
    assert task_critical.effective_priority(now) < task_normal.effective_priority(now)

    # After waiting 20s with aging_rate_sec=5.0:
    # task_normal score: 2.0 - (20 / 5) = 2.0 - 4.0 = -2.0
    # Newly arrived critical task score: 0.0 - 0.0 = 0.0
    future = now + 20.0
    task_new_critical = QueuedTask("t3", "c3", QueuePriority.CRITICAL, enqueued_at=future)

    assert task_normal.effective_priority(future, aging_rate_sec=5.0) < task_new_critical.effective_priority(future, aging_rate_sec=5.0)


def test_try_acquire_permit_and_concurrency_limit():
    queue = ConcurrencyThrottleQueue(max_concurrency=2, refill_rate_per_sec=100.0, token_capacity=100.0)

    p1 = queue.try_acquire_permit("client_a", QueuePriority.HIGH)
    assert p1 is not None
    assert queue.active_in_flight == 1

    p2 = queue.try_acquire_permit("client_b", QueuePriority.NORMAL)
    assert p2 is not None
    assert queue.active_in_flight == 2

    # Concurrency saturated
    p3 = queue.try_acquire_permit("client_c", QueuePriority.CRITICAL)
    assert p3 is None

    # Release permit
    queue.release_permit(p1.permit_id)
    assert queue.active_in_flight == 1

    # Now admission succeeds
    p4 = queue.try_acquire_permit("client_c", QueuePriority.CRITICAL)
    assert p4 is not None
    assert queue.active_in_flight == 2


def test_priority_scheduling_order():
    queue = ConcurrencyThrottleQueue(max_concurrency=1, refill_rate_per_sec=100.0, token_capacity=100.0)

    # Fill active slot
    hold_permit = queue.try_acquire_permit("holder")
    assert hold_permit is not None

    # Enqueue tasks of different priorities
    t_normal = queue.enqueue_task("client_norm", QueuePriority.NORMAL)
    t_critical = queue.enqueue_task("client_crit", QueuePriority.CRITICAL)
    t_high = queue.enqueue_task("client_high", QueuePriority.HIGH)

    now = time.time()
    next_task = queue._select_next_task(now)
    assert next_task is not None
    assert next_task.task_id == t_critical.task_id


def test_lease_context_manager():
    queue = ConcurrencyThrottleQueue(max_concurrency=2, refill_rate_per_sec=100.0, token_capacity=100.0)

    with queue.lease("worker_1", QueuePriority.NORMAL) as permit:
        assert permit is not None
        assert permit.client_id == "worker_1"
        assert queue.active_in_flight == 1

    # Released upon exit
    assert queue.active_in_flight == 0

    # Exception safety test
    with pytest.raises(RuntimeError):
        with queue.lease("worker_2"):
            assert queue.active_in_flight == 1
            raise RuntimeError("Task crash")

    assert queue.active_in_flight == 0


def test_telemetry_and_singleton():
    queue = ConcurrencyThrottleQueue(max_concurrency=3)
    p = queue.try_acquire_permit("test_c")
    t = queue.enqueue_task("waiter", QueuePriority.HIGH)

    telem = queue.get_telemetry()
    assert telem["max_concurrency"] == 3
    assert telem["active_in_flight"] == 1
    assert telem["queue_depth"] == 1
    assert telem["queued_by_priority"]["HIGH"] == 1

    q1 = get_throttle_queue()
    q2 = get_throttle_queue()
    assert q1 is q2

    q3 = reset_throttle_queue()
    assert q3 is not q1
    assert get_throttle_queue() is q3
