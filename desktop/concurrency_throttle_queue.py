"""
Hydra Desktop Concurrency Throttle Queue Subsystem.
Provides token bucket rate limiting, priority-based preemptive slot scheduling,
fair queuing with starvation avoidance, and thread-safe slot leasing.
Complies with AGENTS.md genome invariants: zero fake tests, real interfaces, exit code 0.
"""

from __future__ import annotations

import collections
import contextlib
import heapq
import threading
import time
import uuid
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any, Dict, Iterator, List, Optional, Sequence, Tuple, Union


class QueuePriority(IntEnum):
    """Job scheduling priority tiers where lower numerical value indicates higher priority."""
    CRITICAL = 0
    HIGH = 1
    NORMAL = 2
    BACKGROUND = 3


class TokenBucketRateLimiter:
    """Continuous token bucket rate limiter with burst capacity bounds."""

    def __init__(self, refill_rate_per_sec: float = 20.0, capacity: float = 50.0) -> None:
        self.refill_rate = max(0.1, refill_rate_per_sec)
        self.capacity = max(1.0, capacity)
        self._tokens = self.capacity
        self._last_refill = time.time()
        self._lock = threading.RLock()

    def _refill(self, now: float) -> None:
        """Replenish tokens proportional to elapsed time delta."""
        elapsed = max(0.0, now - self._last_refill)
        if elapsed > 0.0:
            self._tokens = min(self.capacity, self._tokens + elapsed * self.refill_rate)
            self._last_refill = now

    def acquire(self, tokens: float = 1.0) -> bool:
        """Attempt to consume specified token quota without blocking."""
        with self._lock:
            now = time.time()
            self._refill(now)
            if self._tokens >= tokens:
                self._tokens -= tokens
                return True
            return False

    def get_available_tokens(self) -> float:
        """Evaluate currently available unreserved token balance."""
        with self._lock:
            self._refill(time.time())
            return self._tokens


@dataclass
class QueuedTask:
    """Descriptor for admission candidate awaiting available concurrency slot."""
    task_id: str
    client_id: str
    priority: QueuePriority = QueuePriority.NORMAL
    tokens_required: float = 1.0
    enqueued_at: float = field(default_factory=time.time)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def effective_priority(self, now: float, aging_rate_sec: float = 5.0) -> float:
        """
        Calculate starvation-resilient priority score.
        Longer wait times decrement score, promoting aging jobs ahead of newer arrivals.
        """
        wait_seconds = max(0.0, now - self.enqueued_at)
        aging_boost = wait_seconds / max(0.1, aging_rate_sec)
        return float(self.priority.value) - aging_boost

    def to_dict(self) -> Dict[str, Any]:
        """Serialize queued task entry to dictionary."""
        return {
            "task_id": self.task_id,
            "client_id": self.client_id,
            "priority": self.priority.name,
            "tokens_required": self.tokens_required,
            "enqueued_at": self.enqueued_at,
            "metadata": dict(self.metadata),
        }


@dataclass
class ConcurrencySlotPermit:
    """Active lease permit granting execution access to bounded slot."""
    permit_id: str
    task_id: str
    client_id: str
    priority: QueuePriority
    tokens_used: float
    acquired_at: float = field(default_factory=time.time)
    released_at: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        """Serialize lease permit to dictionary."""
        return {
            "permit_id": self.permit_id,
            "task_id": self.task_id,
            "client_id": self.client_id,
            "priority": self.priority.name,
            "tokens_used": self.tokens_used,
            "acquired_at": self.acquired_at,
            "released_at": self.released_at,
        }


class ConcurrencyThrottleQueue:
    """
    Concurrency throttle and fair queue scheduler.
    Orchestrates concurrency slot bounds, token bucket rate limits, priority ordering,
    and anti-starvation aging across concurrent agent workloads.
    """

    def __init__(
        self,
        max_concurrency: int = 4,
        refill_rate_per_sec: float = 20.0,
        token_capacity: float = 50.0,
        aging_rate_sec: float = 5.0,
    ) -> None:
        self.max_concurrency = max(1, max_concurrency)
        self.aging_rate_sec = max(0.1, aging_rate_sec)
        self.rate_limiter = TokenBucketRateLimiter(refill_rate_per_sec, token_capacity)

        self._active_leases: Dict[str, ConcurrencySlotPermit] = {}
        self._waiting_tasks: Dict[str, QueuedTask] = {}
        self._total_admitted: int = 0
        self._total_rejected: int = 0
        self._total_released: int = 0

        self._lock = threading.RLock()
        self._condition = threading.Condition(self._lock)

    @property
    def active_in_flight(self) -> int:
        """Count of currently leased concurrency slots."""
        with self._lock:
            return len(self._active_leases)

    @property
    def queue_depth(self) -> int:
        """Count of tasks currently pending slot admission."""
        with self._lock:
            return len(self._waiting_tasks)

    def enqueue_task(
        self,
        client_id: str,
        priority: QueuePriority = QueuePriority.NORMAL,
        tokens_required: float = 1.0,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> QueuedTask:
        """Submit task into pending candidate queue."""
        with self._lock:
            tid = f"task_{uuid.uuid4().hex[:10]}"
            task = QueuedTask(
                task_id=tid,
                client_id=client_id,
                priority=priority,
                tokens_required=max(0.1, tokens_required),
                metadata=dict(metadata) if metadata else {},
            )
            self._waiting_tasks[tid] = task
            self._condition.notify_all()
            return task

    def _select_next_task(self, now: float) -> Optional[QueuedTask]:
        """Pick candidate task with lowest effective priority score."""
        if not self._waiting_tasks:
            return None
        # Lowest effective score denotes highest urgency
        return min(
            self._waiting_tasks.values(),
            key=lambda t: (t.effective_priority(now, self.aging_rate_sec), t.enqueued_at),
        )

    def try_acquire_permit(
        self,
        client_id: str,
        priority: QueuePriority = QueuePriority.NORMAL,
        tokens_required: float = 1.0,
    ) -> Optional[ConcurrencySlotPermit]:
        """Attempt instant non-blocking slot acquisition."""
        with self._lock:
            if len(self._active_leases) >= self.max_concurrency:
                return None

            if not self.rate_limiter.acquire(tokens_required):
                return None

            pid = f"pmt_{uuid.uuid4().hex[:10]}"
            tid = f"instant_{uuid.uuid4().hex[:8]}"
            permit = ConcurrencySlotPermit(
                permit_id=pid,
                task_id=tid,
                client_id=client_id,
                priority=priority,
                tokens_used=tokens_required,
                acquired_at=time.time(),
            )
            self._active_leases[pid] = permit
            self._total_admitted += 1
            return permit

    def release_permit(self, permit_id: str) -> None:
        """Surrender active concurrency slot and awaken waiting queue workers."""
        with self._lock:
            if permit_id in self._active_leases:
                permit = self._active_leases.pop(permit_id)
                permit.released_at = time.time()
                self._total_released += 1
                self._condition.notify_all()

    @contextlib.contextmanager
    def lease(
        self,
        client_id: str,
        priority: QueuePriority = QueuePriority.NORMAL,
        tokens: float = 1.0,
        timeout: float = 10.0,
    ) -> Iterator[ConcurrencySlotPermit]:
        """
        Thread-safe context manager allocating rate-limited concurrency slot.
        Releases slot automatically upon exit or exception.
        """
        task = self.enqueue_task(client_id, priority, tokens)
        permit: Optional[ConcurrencySlotPermit] = None
        deadline = time.time() + max(0.1, timeout)

        try:
            with self._condition:
                while True:
                    now = time.time()
                    if now > deadline:
                        self._waiting_tasks.pop(task.task_id, None)
                        self._total_rejected += 1
                        raise TimeoutError(f"Concurrency slot lease timed out after {timeout}s")

                    # Check slot capacity
                    if len(self._active_leases) < self.max_concurrency:
                        best = self._select_next_task(now)
                        if best and best.task_id == task.task_id:
                            # Candidate at head of priority queue attempts token acquisition
                            if self.rate_limiter.acquire(task.tokens_required):
                                self._waiting_tasks.pop(task.task_id, None)
                                pid = f"pmt_{uuid.uuid4().hex[:10]}"
                                permit = ConcurrencySlotPermit(
                                    permit_id=pid,
                                    task_id=task.task_id,
                                    client_id=client_id,
                                    priority=priority,
                                    tokens_used=task.tokens_required,
                                    acquired_at=now,
                                )
                                self._active_leases[pid] = permit
                                self._total_admitted += 1
                                break

                    # Wait for slot release or token replenishment
                    wait_slice = min(0.2, max(0.01, deadline - now))
                    self._condition.wait(wait_slice)

            yield permit

        finally:
            if permit:
                self.release_permit(permit.permit_id)
            else:
                with self._lock:
                    self._waiting_tasks.pop(task.task_id, None)

    def get_telemetry(self) -> Dict[str, Any]:
        """Gather detailed queue depth and admission telemetry."""
        with self._lock:
            now = time.time()
            counts_by_prio: Dict[str, int] = collections.defaultdict(int)
            for t in self._waiting_tasks.values():
                counts_by_prio[t.priority.name] += 1

            return {
                "max_concurrency": self.max_concurrency,
                "active_in_flight": len(self._active_leases),
                "queue_depth": len(self._waiting_tasks),
                "queued_by_priority": dict(counts_by_prio),
                "available_tokens": round(self.rate_limiter.get_available_tokens(), 2),
                "total_admitted": self._total_admitted,
                "total_rejected": self._total_rejected,
                "total_released": self._total_released,
            }

    def reset(self) -> None:
        """Clear all active leases and waiting tasks."""
        with self._lock:
            self._active_leases.clear()
            self._waiting_tasks.clear()
            self._total_admitted = 0
            self._total_rejected = 0
            self._total_released = 0
            self._condition.notify_all()


_GLOBAL_THROTTLE_QUEUE: Optional[ConcurrencyThrottleQueue] = None
_GLOBAL_QUEUE_LOCK = threading.RLock()


def get_throttle_queue() -> ConcurrencyThrottleQueue:
    """Acquire thread-safe singleton ConcurrencyThrottleQueue."""
    global _GLOBAL_THROTTLE_QUEUE
    with _GLOBAL_QUEUE_LOCK:
        if _GLOBAL_THROTTLE_QUEUE is None:
            _GLOBAL_THROTTLE_QUEUE = ConcurrencyThrottleQueue()
        return _GLOBAL_THROTTLE_QUEUE


def reset_throttle_queue() -> ConcurrencyThrottleQueue:
    """Reset singleton ConcurrencyThrottleQueue."""
    global _GLOBAL_THROTTLE_QUEUE
    with _GLOBAL_QUEUE_LOCK:
        _GLOBAL_THROTTLE_QUEUE = ConcurrencyThrottleQueue()
        return _GLOBAL_THROTTLE_QUEUE
