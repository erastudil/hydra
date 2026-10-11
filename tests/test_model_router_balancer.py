"""
Integration test suite for Hydra Desktop Model Router Load Balancer.
Complies with AGENTS.md genome invariants: zero fake tests, real interfaces, exit code 0.
"""

from __future__ import annotations

import time
import pytest

from desktop.model_router_balancer import (
    ModelRouterBalancer,
    BackendEndpoint,
    BalancingStrategy,
    EndpointHealthStatus,
    get_router_balancer,
    reset_router_balancer,
)


@pytest.fixture
def balancer():
    b = ModelRouterBalancer()
    yield b


def test_endpoint_concurrency_slots():
    """Verify concurrency slot acquisition, saturation blocking, and release."""
    ep = BackendEndpoint(
        endpoint_id="test_ep_1",
        model_name="gemma-4",
        provider="ollama",
        max_concurrency=2,
    )

    assert ep.is_available is True
    assert ep.acquire_slot() is True
    assert ep.acquire_slot() is True
    # Max concurrency 2 reached
    assert ep.is_available is False
    assert ep.acquire_slot() is False

    ep.release_slot()
    assert ep.is_available is True
    assert ep.acquire_slot() is True


def test_ewma_latency_and_health_circuit_breaker():
    """Verify EWMA latency tracking and failure threshold health status trip."""
    ep = BackendEndpoint(
        endpoint_id="test_ep_2",
        model_name="claude-sonnet-5.5",
        provider="anthropic",
        latency_ewma_ms=100.0,
        alpha=0.5,
        failure_threshold=2,
        cooldown_seconds=0.1,
    )

    # Success updates EWMA: 0.5 * 50 + 0.5 * 100 = 75
    ep.record_success(50.0)
    assert ep.latency_ewma_ms == 75.0
    assert ep.health_status == EndpointHealthStatus.HEALTHY

    # 1 failure: degraded
    ep.record_failure()
    assert ep.health_status == EndpointHealthStatus.DEGRADED

    # 2 failures: trips to unhealthy
    ep.record_failure()
    assert ep.health_status == EndpointHealthStatus.UNHEALTHY
    assert ep.is_available is False

    # Simulate cooldown
    time.sleep(0.15)
    assert ep.is_available is True


def test_balancing_strategies(balancer: ModelRouterBalancer):
    """Verify weighted latency, least busy, round robin, and P2C routing strategies."""
    e1 = BackendEndpoint(
        endpoint_id="node_fast",
        model_name="gpt-4o",
        provider="openai",
        latency_ewma_ms=50.0,
        active_in_flight=1,
    )
    e2 = BackendEndpoint(
        endpoint_id="node_slow",
        model_name="gpt-4o",
        provider="openai",
        latency_ewma_ms=200.0,
        active_in_flight=0,
    )
    balancer.register_endpoint(e1)
    balancer.register_endpoint(e2)

    # WEIGHTED_LATENCY selects node_fast (50ms vs 200ms)
    sel_lat = balancer.select_endpoint(strategy=BalancingStrategy.WEIGHTED_LATENCY)
    assert sel_lat.endpoint_id == "node_fast"

    # LEAST_BUSY selects node_slow (0 in flight vs 1 in flight)
    sel_busy = balancer.select_endpoint(strategy=BalancingStrategy.LEAST_BUSY)
    assert sel_busy.endpoint_id == "node_slow"

    # ROUND_ROBIN alternates
    sel_rr1 = balancer.select_endpoint(strategy=BalancingStrategy.ROUND_ROBIN)
    sel_rr2 = balancer.select_endpoint(strategy=BalancingStrategy.ROUND_ROBIN)
    assert sel_rr1.endpoint_id != sel_rr2.endpoint_id

    # POWER_OF_TWO_CHOICES selects valid node
    sel_p2c = balancer.select_endpoint(strategy=BalancingStrategy.POWER_OF_TWO_CHOICES)
    assert sel_p2c.endpoint_id in ("node_fast", "node_slow")


def test_lease_context_manager(balancer: ModelRouterBalancer):
    """Verify lease acquires and releases slot automatically and records latency."""
    ep = BackendEndpoint(
        endpoint_id="node_leased",
        model_name="llama-3.1",
        provider="groq",
        max_concurrency=1,
    )
    balancer.register_endpoint(ep)

    assert ep.active_in_flight == 0
    with balancer.lease("node_leased") as leased_ep:
        assert leased_ep.active_in_flight == 1

    assert ep.active_in_flight == 0
    assert ep.total_completed == 1


def test_lease_handles_exceptions_and_records_failure(balancer: ModelRouterBalancer):
    """Verify lease context records failure when enclosed block raises."""
    ep = BackendEndpoint(
        endpoint_id="node_failing",
        model_name="mock",
        provider="mock",
    )
    balancer.register_endpoint(ep)

    with pytest.raises(ValueError):
        with balancer.lease("node_failing"):
            raise ValueError("Upstream failure")

    assert ep.active_in_flight == 0
    assert ep.total_failed == 1


def test_global_singleton_balancer():
    """Verify singleton lifecycle for ModelRouterBalancer."""
    b1 = get_router_balancer()
    b2 = get_router_balancer()
    assert b1 is b2

    b3 = reset_router_balancer()
    assert b3 is not b1
    assert get_router_balancer() is b3
