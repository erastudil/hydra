"""
Integration test suite for Hydra Desktop Multi-Tier Model Fallback Mesh.
Complies with AGENTS.md genome invariants: zero fake tests, real interfaces, exit code 0.
"""

from __future__ import annotations

import time
import pytest

from desktop.model_fallback_mesh import (
    ModelFallbackMesh,
    ModelTier,
    ErrorCategory,
    CircuitState,
    CircuitBreaker,
    MeshNode,
    categorize_error,
    get_model_fallback_mesh,
    reset_model_fallback_mesh,
)


@pytest.fixture
def mesh():
    m = ModelFallbackMesh(auto_populate_defaults=True)
    yield m
    m.reset_all_breakers()


def test_error_categorization_matrix():
    """Verify error categorization maps status codes and strings to standard categories."""
    assert categorize_error("Error 429: Rate limit exceeded") == ErrorCategory.RATE_LIMIT
    assert categorize_error("Request timed out after 30s") == ErrorCategory.TIMEOUT
    assert categorize_error("HTTP 401 Unauthorized API key") == ErrorCategory.AUTHENTICATION
    assert categorize_error("503 Service Unavailable") == ErrorCategory.SERVICE_UNAVAILABLE
    assert categorize_error("Blocked by content filter safety policy") == ErrorCategory.CONTENT_FILTER
    assert categorize_error("Connection refused by peer (ECONNREFUSED)") == ErrorCategory.CONNECTION_ERROR
    assert categorize_error("Unknown system fault 999") == ErrorCategory.UNKNOWN
    assert categorize_error(None) == ErrorCategory.UNKNOWN


def test_circuit_breaker_lifecycle_and_trip():
    """Verify circuit breaker trips upon reaching failure threshold and enforces open state."""
    cb = CircuitBreaker(failure_threshold=2, recovery_timeout=0.2)
    assert cb.state == CircuitState.CLOSED
    assert cb.can_execute() is True

    # 1 failure: remains closed
    cb.record_failure(ErrorCategory.RATE_LIMIT)
    assert cb.state == CircuitState.CLOSED
    assert cb.failure_count == 1
    assert cb.can_execute() is True

    # 2 failures: trips to OPEN
    cb.record_failure(ErrorCategory.RATE_LIMIT)
    assert cb.state == CircuitState.OPEN
    assert cb.can_execute() is False

    # Immediate execution blocked
    assert cb.can_execute() is False

    # Simulate recovery timeout passing
    cb.last_failure_time = time.time() - 1.0
    assert cb.can_execute() is True
    assert cb.state == CircuitState.HALF_OPEN

    # Success in half open resets breaker
    cb.record_success()
    assert cb.state == CircuitState.CLOSED
    assert cb.failure_count == 0


def test_circuit_breaker_half_open_failure_re_trips():
    """Verify trial failure in half-open immediately re-trips to OPEN."""
    cb = CircuitBreaker(failure_threshold=1, recovery_timeout=0.1)
    cb.record_failure(ErrorCategory.TIMEOUT)
    assert cb.state == CircuitState.OPEN

    cb.last_failure_time = time.time() - 0.5
    assert cb.can_execute() is True
    assert cb.state == CircuitState.HALF_OPEN

    cb.record_failure(ErrorCategory.TIMEOUT)
    assert cb.state == CircuitState.OPEN
    assert cb.can_execute() is False


def test_default_mesh_nodes_topology(mesh: ModelFallbackMesh):
    """Verify default mesh nodes populated across local, free cloud, and frontier tiers."""
    assert mesh.total_nodes >= 7

    local_nodes = mesh.list_nodes(tier=ModelTier.LOCAL)
    assert len(local_nodes) >= 2
    local_ids = [n.node_id for n in local_nodes]
    assert "local_gemma" in local_ids
    assert "local_llama" in local_ids

    free_nodes = mesh.list_nodes(tier=ModelTier.FREE_CLOUD)
    assert len(free_nodes) >= 2
    free_ids = [n.node_id for n in free_nodes]
    assert "cloudflare_free" in free_ids
    assert "openrouter_free" in free_ids

    frontier_nodes = mesh.list_nodes(tier=ModelTier.FRONTIER)
    assert len(frontier_nodes) >= 3
    frontier_ids = [n.node_id for n in frontier_nodes]
    assert "frontier_sonnet" in frontier_ids
    assert "frontier_opus" in frontier_ids


def test_fallback_cascades_from_local_to_free_cloud():
    """Verify execution fails on local nodes and automatically cascades to free cloud."""
    m = ModelFallbackMesh(auto_populate_defaults=False)
    # Register 1 local node that fails
    m.register_node(
        MeshNode(
            node_id="local_gemma",
            tier=ModelTier.LOCAL,
            model_name="gemma-4",
            provider="ollama",
            priority=10,
        )
    )
    # Register 1 free cloud node that succeeds
    m.register_node(
        MeshNode(
            node_id="cloudflare_free",
            tier=ModelTier.FREE_CLOUD,
            model_name="@cf/meta/llama-3-8b-instruct",
            provider="cloudflare",
            priority=20,
        )
    )

    def dispatch(node: MeshNode):
        if node.node_id == "local_gemma":
            raise RuntimeError("Local daemon offline (connection refused)")
        return "response from " + node.model_name

    res = m.execute_with_fallback(dispatch)
    assert res.success is True
    assert res.node_id == "cloudflare_free"
    assert res.tier_used == ModelTier.FREE_CLOUD.value
    assert res.response == "response from @cf/meta/llama-3-8b-instruct"
    assert len(res.attempts) == 2
    assert res.attempts[0]["success"] is False
    assert res.attempts[0]["error_category"] == ErrorCategory.CONNECTION_ERROR.value
    assert res.attempts[1]["success"] is True


def test_all_nodes_exhausted_returns_failure_report():
    """Verify all nodes failing returns clean diagnostic result without crashing."""
    m = ModelFallbackMesh(auto_populate_defaults=False)
    m.register_node(
        MeshNode(
            node_id="test_node_1",
            tier=ModelTier.LOCAL,
            model_name="test-1",
            provider="ollama",
            priority=10,
        )
    )

    def fail_always(node: MeshNode):
        raise ValueError("Simulated provider outage 503")

    res = m.execute_with_fallback(fail_always)
    assert res.success is False
    assert res.error is not None
    assert len(res.attempts) == 1
    assert res.attempts[0]["error_category"] == ErrorCategory.SERVICE_UNAVAILABLE.value


def test_open_circuit_breaker_skipped_in_fallback():
    """Verify nodes with open circuit breakers are skipped during fallback dispatch."""
    m = ModelFallbackMesh(auto_populate_defaults=False)
    n1 = MeshNode(
        node_id="failing_node",
        tier=ModelTier.LOCAL,
        model_name="m1",
        provider="ollama",
        priority=10,
        circuit_breaker=CircuitBreaker(failure_threshold=1),
    )
    n2 = MeshNode(
        node_id="backup_node",
        tier=ModelTier.LOCAL,
        model_name="m2",
        provider="ollama",
        priority=20,
    )
    m.register_node(n1)
    m.register_node(n2)

    # Trip n1
    n1.circuit_breaker.trip()
    assert n1.is_available is False

    calls = []
    def dispatch(node: MeshNode):
        calls.append(node.node_id)
        return "ok"

    res = m.execute_with_fallback(dispatch)
    assert res.success is True
    assert res.node_id == "backup_node"
    assert calls == ["backup_node"]


def test_global_singleton_mesh():
    """Verify singleton lifecycle for ModelFallbackMesh."""
    m1 = get_model_fallback_mesh()
    m2 = get_model_fallback_mesh()
    assert m1 is m2

    m3 = reset_model_fallback_mesh()
    assert m3 is not m1
    assert get_model_fallback_mesh() is m3
