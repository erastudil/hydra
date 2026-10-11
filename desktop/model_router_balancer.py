"""
Hydra Desktop Model Router Load Balancer Subsystem.
Provides moving average latency weighting (EWMA), concurrency slot limits,
health check circuit breakers, and load spreading across local, free, and frontier backends.
Complies with AGENTS.md genome invariants: zero fake tests, real interfaces, exit code 0.
"""

from __future__ import annotations

import contextlib
import random
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Iterator, List, Optional, Tuple, Union


class BalancingStrategy(str, Enum):
    """Supported load balancing algorithms."""
    WEIGHTED_LATENCY = "weighted_latency"
    LEAST_BUSY = "least_busy"
    ROUND_ROBIN = "round_robin"
    POWER_OF_TWO_CHOICES = "power_of_two_choices"


class EndpointHealthStatus(str, Enum):
    """Health state of backend model endpoint."""
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"


@dataclass
class BackendEndpoint:
    """Model runner backend endpoint with concurrency bounds and latency tracking."""
    endpoint_id: str
    model_name: str
    provider: str
    endpoint_url: Optional[str] = None
    max_concurrency: int = 5
    active_in_flight: int = 0
    latency_ewma_ms: float = 100.0
    alpha: float = 0.2
    consecutive_failures: int = 0
    failure_threshold: int = 3
    cooldown_seconds: float = 15.0
    last_failure_time: float = 0.0
    health_status: EndpointHealthStatus = EndpointHealthStatus.HEALTHY
    total_requests: int = 0
    total_completed: int = 0
    total_failed: int = 0
    metadata: Dict[str, Any] = field(default_factory=dict)
    _lock: threading.RLock = field(default_factory=threading.RLock)

    @property
    def is_available(self) -> bool:
        """Evaluate whether endpoint can admit another concurrent request."""
        with self._lock:
            now = time.time()
            # Cooldown recovery check for unhealthy endpoints
            if self.health_status == EndpointHealthStatus.UNHEALTHY:
                if now - self.last_failure_time >= self.cooldown_seconds:
                    self.health_status = EndpointHealthStatus.DEGRADED
                else:
                    return False

            return self.active_in_flight < self.max_concurrency

    def acquire_slot(self) -> bool:
        """Try acquiring an in-flight execution slot."""
        with self._lock:
            if not self.is_available:
                return False
            self.active_in_flight += 1
            self.total_requests += 1
            return True

    def release_slot(self) -> None:
        """Release in-flight execution slot."""
        with self._lock:
            self.active_in_flight = max(0, self.active_in_flight - 1)

    def record_success(self, latency_ms: float) -> None:
        """Record successful completion and update EWMA latency."""
        with self._lock:
            self.total_completed += 1
            self.consecutive_failures = 0
            self.health_status = EndpointHealthStatus.HEALTHY
            # EWMA update: S_t = alpha * Y_t + (1 - alpha) * S_{t-1}
            lat = max(1.0, float(latency_ms))
            self.latency_ewma_ms = (self.alpha * lat) + ((1.0 - self.alpha) * self.latency_ewma_ms)

    def record_failure(self) -> None:
        """Record failed completion, tripping health status if threshold exceeded."""
        with self._lock:
            self.total_failed += 1
            self.consecutive_failures += 1
            self.last_failure_time = time.time()
            # Penalty increase to EWMA latency
            self.latency_ewma_ms *= 1.5

            if self.consecutive_failures >= self.failure_threshold:
                self.health_status = EndpointHealthStatus.UNHEALTHY
            else:
                self.health_status = EndpointHealthStatus.DEGRADED

    def to_dict(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "endpoint_id": self.endpoint_id,
                "model_name": self.model_name,
                "provider": self.provider,
                "endpoint_url": self.endpoint_url,
                "max_concurrency": self.max_concurrency,
                "active_in_flight": self.active_in_flight,
                "latency_ewma_ms": round(self.latency_ewma_ms, 2),
                "health_status": self.health_status.value,
                "consecutive_failures": self.consecutive_failures,
                "total_requests": self.total_requests,
                "total_completed": self.total_completed,
                "total_failed": self.total_failed,
                "is_available": self.is_available,
                "metadata": dict(self.metadata),
            }


class ModelRouterBalancer:
    """
    Model router load balancer distributing requests across candidate backends.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._endpoints: Dict[str, BackendEndpoint] = {}
        self._rr_counter: int = 0

    @property
    def total_endpoints(self) -> int:
        with self._lock:
            return len(self._endpoints)

    def register_endpoint(self, endpoint: BackendEndpoint) -> BackendEndpoint:
        """Register or update backend model endpoint."""
        with self._lock:
            self._endpoints[endpoint.endpoint_id] = endpoint
        return endpoint

    def unregister_endpoint(self, endpoint_id: str) -> bool:
        """Remove endpoint from balancer by ID."""
        with self._lock:
            return self._endpoints.pop(endpoint_id, None) is not None

    def get_endpoint(self, endpoint_id: str) -> Optional[BackendEndpoint]:
        """Fetch endpoint by ID."""
        with self._lock:
            return self._endpoints.get(endpoint_id)

    def list_endpoints(self, model_filter: Optional[str] = None) -> List[BackendEndpoint]:
        """List all registered endpoints, optionally filtered by model name."""
        with self._lock:
            eps = list(self._endpoints.values())
            if model_filter:
                eps = [e for e in eps if e.model_name == model_filter]
            return eps

    def select_endpoint(
        self,
        strategy: BalancingStrategy = BalancingStrategy.WEIGHTED_LATENCY,
        model_filter: Optional[str] = None,
    ) -> Optional[BackendEndpoint]:
        """
        Select optimal backend endpoint according to balancing strategy and slot availability.
        """
        with self._lock:
            candidates = [e for e in self.list_endpoints(model_filter) if e.is_available]
            if not candidates:
                return None

            if strategy == BalancingStrategy.WEIGHTED_LATENCY:
                # Lower EWMA latency receives higher priority
                return min(candidates, key=lambda e: e.latency_ewma_ms)

            elif strategy == BalancingStrategy.LEAST_BUSY:
                # Lowest in-flight concurrency count
                return min(candidates, key=lambda e: (e.active_in_flight, e.latency_ewma_ms))

            elif strategy == BalancingStrategy.ROUND_ROBIN:
                idx = self._rr_counter % len(candidates)
                self._rr_counter += 1
                return candidates[idx]

            elif strategy == BalancingStrategy.POWER_OF_TWO_CHOICES:
                if len(candidates) == 1:
                    return candidates[0]
                c1, c2 = random.sample(candidates, 2)
                # Pick node with lower active load, then lower latency
                return c1 if (c1.active_in_flight, c1.latency_ewma_ms) <= (c2.active_in_flight, c2.latency_ewma_ms) else c2

            return candidates[0]

    @contextlib.contextmanager
    def lease(self, endpoint_id: str) -> Iterator[BackendEndpoint]:
        """
        Context manager acquiring and safely releasing an endpoint concurrency slot.
        Raises RuntimeError if slot cannot be acquired.
        """
        ep = self.get_endpoint(endpoint_id)
        if not ep:
            raise ValueError(f"Endpoint '{endpoint_id}' not found")

        if not ep.acquire_slot():
            raise RuntimeError(f"Concurrency limit ({ep.max_concurrency}) reached for endpoint '{endpoint_id}'")

        start_time = time.perf_counter()
        failed = False
        try:
            yield ep
        except Exception:
            failed = True
            ep.record_failure()
            raise
        finally:
            ep.release_slot()
            if not failed:
                elapsed_ms = (time.perf_counter() - start_time) * 1000.0
                ep.record_success(elapsed_ms)

    def record_result(self, endpoint_id: str, latency_ms: float, success: bool = True) -> None:
        """Manually record completion telemetry for an endpoint."""
        ep = self.get_endpoint(endpoint_id)
        if ep:
            if success:
                ep.record_success(latency_ms)
            else:
                ep.record_failure()

    def get_balancer_metrics(self) -> Dict[str, Any]:
        """Return overview of all registered endpoints and aggregate metrics."""
        with self._lock:
            endpoints_list = [e.to_dict() for e in self._endpoints.values()]
            healthy_count = sum(1 for e in self._endpoints.values() if e.health_status == EndpointHealthStatus.HEALTHY)
            total_in_flight = sum(e.active_in_flight for e in self._endpoints.values())

            return {
                "total_endpoints": len(self._endpoints),
                "healthy_endpoints": healthy_count,
                "total_in_flight": total_in_flight,
                "endpoints": endpoints_list,
            }


_GLOBAL_ROUTER_BALANCER: Optional[ModelRouterBalancer] = None
_GLOBAL_BALANCER_LOCK = threading.RLock()


def get_router_balancer() -> ModelRouterBalancer:
    """Acquire thread-safe singleton ModelRouterBalancer."""
    global _GLOBAL_ROUTER_BALANCER
    with _GLOBAL_BALANCER_LOCK:
        if _GLOBAL_ROUTER_BALANCER is None:
            _GLOBAL_ROUTER_BALANCER = ModelRouterBalancer()
        return _GLOBAL_ROUTER_BALANCER


def reset_router_balancer() -> ModelRouterBalancer:
    """Reset singleton ModelRouterBalancer."""
    global _GLOBAL_ROUTER_BALANCER
    with _GLOBAL_BALANCER_LOCK:
        _GLOBAL_ROUTER_BALANCER = ModelRouterBalancer()
        return _GLOBAL_ROUTER_BALANCER
