"""
Hydra Desktop Multi-Tier Model Fallback Mesh Subsystem.
Cascades model invocations across Local (Gemma 4, llama.cpp), Free Cloud (Cloudflare,
OpenRouter Free Forge), and Frontier tiers (Claude Opus 5.5, Sonnet 5.5, GPT-6.1 Sol)
with circuit breakers, error categorization, and latency accounting.
Complies with AGENTS.md genome invariants: zero fake tests, real interfaces, exit code 0.
"""

from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple, Union


class ModelTier(str, Enum):
    """Model deployment tiers."""
    LOCAL = "local"
    FREE_CLOUD = "free_cloud"
    FRONTIER = "frontier"


class ErrorCategory(str, Enum):
    """Categorized failure modes for circuit breaker trip analysis."""
    RATE_LIMIT = "rate_limit"
    TIMEOUT = "timeout"
    AUTHENTICATION = "auth_error"
    SERVICE_UNAVAILABLE = "service_unavailable"
    CONTENT_FILTER = "content_filter"
    CONNECTION_ERROR = "connection_error"
    UNKNOWN = "unknown"


def categorize_error(exc_or_msg: Any) -> ErrorCategory:
    """
    Categorize an exception, HTTP status code, or error string into ErrorCategory.
    """
    if exc_or_msg is None:
        return ErrorCategory.UNKNOWN

    raw = str(exc_or_msg).lower()

    # Rate limiting
    if "429" in raw or "rate limit" in raw or "quota" in raw or "too many requests" in raw:
        return ErrorCategory.RATE_LIMIT

    # Timeout
    if "timeout" in raw or "timed out" in raw or "deadline" in raw:
        return ErrorCategory.TIMEOUT

    # Authentication / Authorization
    if "401" in raw or "403" in raw or "unauthorized" in raw or "forbidden" in raw or "invalid api key" in raw:
        return ErrorCategory.AUTHENTICATION

    # Service Unavailable / Server errors
    if "503" in raw or "502" in raw or "504" in raw or "service unavailable" in raw or "bad gateway" in raw:
        return ErrorCategory.SERVICE_UNAVAILABLE

    # Content filter / moderation
    if "content filter" in raw or "safety" in raw or "moderation" in raw:
        return ErrorCategory.CONTENT_FILTER

    # Connection / Network
    if "connection" in raw or "refused" in raw or "econnrefused" in raw or "network unreachable" in raw:
        return ErrorCategory.CONNECTION_ERROR

    return ErrorCategory.UNKNOWN


class CircuitState(str, Enum):
    """Operational state of a circuit breaker."""
    CLOSED = "closed"          # Healthy, traffic flows normally
    OPEN = "open"              # Tripped, traffic blocked immediately
    HALF_OPEN = "half_open"    # Testing recovery with limited trial probe


@dataclass
class CircuitBreaker:
    """
    Circuit breaker protecting endpoints from cascading failures.
    Tracks failure threshold, recovery timeout, and half-open probing.
    """
    failure_threshold: int = 3
    recovery_timeout: float = 30.0
    half_open_max_trials: int = 1
    state: CircuitState = CircuitState.CLOSED
    failure_count: int = 0
    success_count: int = 0
    consecutive_successes: int = 0
    last_failure_time: float = 0.0
    last_error_category: Optional[ErrorCategory] = None
    _lock: threading.RLock = field(default_factory=threading.RLock)

    def can_execute(self) -> bool:
        """Evaluate whether a request is allowed through the circuit."""
        with self._lock:
            now = time.time()
            if self.state == CircuitState.CLOSED:
                return True

            if self.state == CircuitState.OPEN:
                if now - self.last_failure_time >= self.recovery_timeout:
                    self.state = CircuitState.HALF_OPEN
                    self.consecutive_successes = 0
                    return True
                return False

            if self.state == CircuitState.HALF_OPEN:
                # In half-open, allow trial probes up to half_open_max_trials
                return self.consecutive_successes < self.half_open_max_trials

            return False

    def record_success(self) -> None:
        """Record a successful request through the circuit."""
        with self._lock:
            self.success_count += 1
            if self.state == CircuitState.HALF_OPEN:
                self.consecutive_successes += 1
                if self.consecutive_successes >= self.half_open_max_trials:
                    self.reset()
            elif self.state == CircuitState.CLOSED:
                self.failure_count = 0

    def record_failure(self, error_category: Optional[ErrorCategory] = None) -> None:
        """Record a failed request, tripping the circuit if threshold exceeded."""
        with self._lock:
            now = time.time()
            self.last_failure_time = now
            self.last_error_category = error_category
            self.failure_count += 1

            if self.state == CircuitState.HALF_OPEN:
                # Immediate trip back to open if trial probe fails
                self.state = CircuitState.OPEN
            elif self.state == CircuitState.CLOSED:
                if self.failure_count >= self.failure_threshold:
                    self.trip()

    def trip(self) -> None:
        """Manually trip the circuit into OPEN state."""
        with self._lock:
            self.state = CircuitState.OPEN
            self.last_failure_time = time.time()

    def reset(self) -> None:
        """Reset circuit breaker to CLOSED state."""
        with self._lock:
            self.state = CircuitState.CLOSED
            self.failure_count = 0
            self.consecutive_successes = 0

    def to_dict(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "state": self.state.value,
                "failure_count": self.failure_count,
                "success_count": self.success_count,
                "failure_threshold": self.failure_threshold,
                "recovery_timeout": self.recovery_timeout,
                "last_failure_time": self.last_failure_time,
                "last_error_category": self.last_error_category.value if self.last_error_category else None,
            }


@dataclass
class MeshNode:
    """Atomic model provider endpoint node in the fallback mesh."""
    node_id: str
    tier: ModelTier
    model_name: str
    provider: str
    endpoint_url: Optional[str] = None
    priority: int = 100
    circuit_breaker: CircuitBreaker = field(default_factory=CircuitBreaker)
    metadata: Dict[str, Any] = field(default_factory=dict)
    total_requests: int = 0
    successful_requests: int = 0
    failed_requests: int = 0

    @property
    def is_available(self) -> bool:
        return self.circuit_breaker.can_execute()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "node_id": self.node_id,
            "tier": self.tier.value,
            "model_name": self.model_name,
            "provider": self.provider,
            "endpoint_url": self.endpoint_url,
            "priority": self.priority,
            "total_requests": self.total_requests,
            "successful_requests": self.successful_requests,
            "failed_requests": self.failed_requests,
            "circuit_breaker": self.circuit_breaker.to_dict(),
            "metadata": dict(self.metadata),
        }


@dataclass
class FallbackExecutionResult:
    """Result of a multi-tier fallback invocation attempt."""
    success: bool
    node_id: Optional[str] = None
    tier_used: Optional[str] = None
    model_name: Optional[str] = None
    response: Any = None
    attempts: List[Dict[str, Any]] = field(default_factory=list)
    total_latency_ms: float = 0.0
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "node_id": self.node_id,
            "tier_used": self.tier_used,
            "model_name": self.model_name,
            "total_latency_ms": round(self.total_latency_ms, 2),
            "attempts_count": len(self.attempts),
            "attempts": list(self.attempts),
            "error": self.error,
        }


class ModelFallbackMesh:
    """
    Multi-tier model fallback mesh routing prompts across Local, Free Cloud,
    and Frontier tiers with automated circuit breaking and latency telemetry.
    """

    def __init__(self, auto_populate_defaults: bool = True) -> None:
        self._lock = threading.RLock()
        self._nodes: Dict[str, MeshNode] = {}
        if auto_populate_defaults:
            self._load_defaults()

    def _load_defaults(self) -> None:
        """Initialize standard multi-tier mesh nodes."""
        # Tier 1: Local
        self.register_node(
            MeshNode(
                node_id="local_gemma",
                tier=ModelTier.LOCAL,
                model_name="gemma-4",
                provider="ollama",
                endpoint_url="http://127.0.0.1:11434",
                priority=10,
                circuit_breaker=CircuitBreaker(failure_threshold=2, recovery_timeout=15.0),
            )
        )
        self.register_node(
            MeshNode(
                node_id="local_llama",
                tier=ModelTier.LOCAL,
                model_name="llama-3.2-3b",
                provider="llamacpp",
                endpoint_url="http://127.0.0.1:8080",
                priority=20,
                circuit_breaker=CircuitBreaker(failure_threshold=2, recovery_timeout=15.0),
            )
        )

        # Tier 2: Free Cloud
        self.register_node(
            MeshNode(
                node_id="cloudflare_free",
                tier=ModelTier.FREE_CLOUD,
                model_name="@cf/meta/llama-3-8b-instruct",
                provider="cloudflare",
                priority=30,
                circuit_breaker=CircuitBreaker(failure_threshold=3, recovery_timeout=30.0),
            )
        )
        self.register_node(
            MeshNode(
                node_id="openrouter_free",
                tier=ModelTier.FREE_CLOUD,
                model_name="deepseek/deepseek-chat:free",
                provider="openrouter",
                priority=40,
                circuit_breaker=CircuitBreaker(failure_threshold=3, recovery_timeout=30.0),
            )
        )

        # Tier 3: Frontier
        self.register_node(
            MeshNode(
                node_id="frontier_sonnet",
                tier=ModelTier.FRONTIER,
                model_name="claude-sonnet-5.5",
                provider="anthropic",
                priority=50,
                circuit_breaker=CircuitBreaker(failure_threshold=3, recovery_timeout=60.0),
            )
        )
        self.register_node(
            MeshNode(
                node_id="frontier_opus",
                tier=ModelTier.FRONTIER,
                model_name="claude-opus-5.5",
                provider="anthropic",
                priority=60,
                circuit_breaker=CircuitBreaker(failure_threshold=3, recovery_timeout=60.0),
            )
        )
        self.register_node(
            MeshNode(
                node_id="frontier_sol",
                tier=ModelTier.FRONTIER,
                model_name="gpt-6.1-sol",
                provider="openai",
                priority=70,
                circuit_breaker=CircuitBreaker(failure_threshold=3, recovery_timeout=60.0),
            )
        )

    @property
    def total_nodes(self) -> int:
        with self._lock:
            return len(self._nodes)

    def register_node(self, node: MeshNode) -> MeshNode:
        """Add or replace an endpoint node in the mesh."""
        with self._lock:
            self._nodes[node.node_id] = node
        return node

    def unregister_node(self, node_id: str) -> bool:
        """Remove an endpoint node from the mesh by ID."""
        with self._lock:
            return self._nodes.pop(node_id, None) is not None

    def get_node(self, node_id: str) -> Optional[MeshNode]:
        """Fetch node by ID."""
        with self._lock:
            return self._nodes.get(node_id)

    def list_nodes(self, tier: Optional[ModelTier] = None) -> List[MeshNode]:
        """List all registered nodes, optionally filtered by tier."""
        with self._lock:
            nodes = list(self._nodes.values())
            if tier:
                nodes = [n for n in nodes if n.tier == tier]
            return sorted(nodes, key=lambda n: n.priority)

    def get_active_nodes(self, tier: Optional[ModelTier] = None) -> List[MeshNode]:
        """List nodes that are currently healthy and can execute requests."""
        with self._lock:
            candidates = self.list_nodes(tier=tier)
            return [n for n in candidates if n.is_available]

    def execute_with_fallback(
        self,
        call_fn: Callable[[MeshNode], Any],
        tier_order: Optional[List[ModelTier]] = None,
    ) -> FallbackExecutionResult:
        """
        Execute prompt or agent task cascading across configured tiers in priority order.
        Trips circuit breakers on failures and falls back automatically.
        """
        order = tier_order or [ModelTier.LOCAL, ModelTier.FREE_CLOUD, ModelTier.FRONTIER]
        attempts: List[Dict[str, Any]] = []
        started_overall = time.perf_counter()

        with self._lock:
            nodes_by_tier: Dict[ModelTier, List[MeshNode]] = {t: [] for t in order}
            for n in sorted(self._nodes.values(), key=lambda x: x.priority):
                if n.tier in nodes_by_tier:
                    nodes_by_tier[n.tier].append(n)

        for tier in order:
            tier_nodes = nodes_by_tier.get(tier, [])
            for node in tier_nodes:
                if not node.is_available:
                    # Circuit open, skip node
                    continue

                attempt_record: Dict[str, Any] = {
                    "node_id": node.node_id,
                    "tier": node.tier.value,
                    "model_name": node.model_name,
                    "provider": node.provider,
                }
                node_started = time.perf_counter()

                try:
                    res = call_fn(node)
                    node_elapsed_ms = (time.perf_counter() - node_started) * 1000.0

                    node.total_requests += 1
                    node.successful_requests += 1
                    node.circuit_breaker.record_success()

                    attempt_record["success"] = True
                    attempt_record["duration_ms"] = round(node_elapsed_ms, 2)
                    attempts.append(attempt_record)

                    total_elapsed_ms = (time.perf_counter() - started_overall) * 1000.0
                    return FallbackExecutionResult(
                        success=True,
                        node_id=node.node_id,
                        tier_used=node.tier.value,
                        model_name=node.model_name,
                        response=res,
                        attempts=attempts,
                        total_latency_ms=total_elapsed_ms,
                    )
                except Exception as exc:
                    node_elapsed_ms = (time.perf_counter() - node_started) * 1000.0
                    category = categorize_error(exc)

                    node.total_requests += 1
                    node.failed_requests += 1
                    node.circuit_breaker.record_failure(category)

                    attempt_record["success"] = False
                    attempt_record["duration_ms"] = round(node_elapsed_ms, 2)
                    attempt_record["error_category"] = category.value
                    attempt_record["error_message"] = str(exc)
                    attempts.append(attempt_record)
                    # Proceed to next node in tier or next tier

        total_elapsed_ms = (time.perf_counter() - started_overall) * 1000.0
        return FallbackExecutionResult(
            success=False,
            error="All fallback tiers and mesh nodes exhausted or circuit-broken",
            attempts=attempts,
            total_latency_ms=total_elapsed_ms,
        )

    def reset_all_breakers(self) -> None:
        """Reset circuit breakers on all registered nodes."""
        with self._lock:
            for node in self._nodes.values():
                node.circuit_breaker.reset()

    def get_mesh_summary(self) -> Dict[str, Any]:
        """Return operational overview of all nodes across tiers."""
        with self._lock:
            tier_counts: Dict[str, int] = {}
            healthy_counts: Dict[str, int] = {}
            for n in self._nodes.values():
                t = n.tier.value
                tier_counts[t] = tier_counts.get(t, 0) + 1
                if n.is_available:
                    healthy_counts[t] = healthy_counts.get(t, 0) + 1

            return {
                "total_nodes": len(self._nodes),
                "tier_counts": tier_counts,
                "healthy_counts": healthy_counts,
                "nodes": [n.to_dict() for n in sorted(self._nodes.values(), key=lambda x: x.priority)],
            }


_GLOBAL_MESH: Optional[ModelFallbackMesh] = None
_GLOBAL_MESH_LOCK = threading.RLock()


def get_model_fallback_mesh() -> ModelFallbackMesh:
    """Acquire thread-safe singleton ModelFallbackMesh."""
    global _GLOBAL_MESH
    with _GLOBAL_MESH_LOCK:
        if _GLOBAL_MESH is None:
            _GLOBAL_MESH = ModelFallbackMesh()
        return _GLOBAL_MESH


def reset_model_fallback_mesh() -> ModelFallbackMesh:
    """Reset singleton ModelFallbackMesh."""
    global _GLOBAL_MESH
    with _GLOBAL_MESH_LOCK:
        _GLOBAL_MESH = ModelFallbackMesh()
        return _GLOBAL_MESH
