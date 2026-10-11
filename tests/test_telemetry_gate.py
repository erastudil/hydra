"""
Integration test suite for Hydra Desktop Sovereign Telemetry Gate.
Complies with AGENTS.md genome invariants: zero fake tests, real interfaces, exit code 0.
"""

from __future__ import annotations

import pytest

from desktop.telemetry_gate import (
    SovereignTelemetryGate,
    LocalMetricRecord,
    is_telemetry_endpoint,
    get_telemetry_gate,
    reset_telemetry_gate,
)


@pytest.fixture
def gate():
    g = SovereignTelemetryGate(strict_mode=True)
    yield g
    g.clear()


def test_telemetry_endpoint_detection():
    """Verify detection of third-party telemetry, analytics, and tracking domains."""
    blocked_urls = [
        "https://www.google-analytics.com/g/collect",
        "https://o12345.ingest.sentry.io/api/123/envelope/",
        "https://api.mixpanel.com/track",
        "https://api.segment.io/v1/t",
        "https://app.posthog.com/e/",
        "https://browser-intake-datadoghq.com/api/v2/rum",
        "https://telemetry.openai.com/events",
    ]
    for url in blocked_urls:
        blocked, reason = is_telemetry_endpoint(url)
        assert blocked is True, f"Expected {url} to be blocked"
        assert len(reason) > 0

    # Normal API endpoints are allowed
    allowed_urls = [
        "https://api.github.com/repos/erastudil/hydra",
        "https://api.openai.com/v1/chat/completions",
        "https://api.anthropic.com/v1/messages",
        "http://localhost:7777/api/status",
    ]
    for url in allowed_urls:
        blocked, _ = is_telemetry_endpoint(url)
        assert blocked is False, f"Expected {url} to be allowed"


def test_strict_mode_outbound_interception_raises_violation(gate: SovereignTelemetryGate):
    """Verify strict mode raises PermissionError upon attempted telemetry dispatch."""
    with pytest.raises(PermissionError) as exc_info:
        gate.inspect_outbound("https://www.google-analytics.com/collect", payload={"v": 1})

    assert "Security violation" in str(exc_info.value)
    assert "Outbound telemetry blocked" in str(exc_info.value)
    assert gate.blocked_attempts_count == 1

    # Allowed endpoint passes without error
    res = gate.inspect_outbound("https://api.openai.com/v1/chat/completions")
    assert res["allowed"] is True
    assert res["violation"] is False


def test_non_strict_mode_inspection_returns_structured_rejection():
    """Verify non-strict mode returns structured violation dict instead of throwing."""
    permissive_gate = SovereignTelemetryGate(strict_mode=False)

    res = permissive_gate.inspect_outbound("https://api.mixpanel.com/track", payload={"event": "click"})
    assert res["allowed"] is False
    assert res["violation"] is True
    assert "Blocked" in res["reason"]
    assert permissive_gate.blocked_attempts_count == 1


def test_local_only_telemetry_recording_and_summary(gate: SovereignTelemetryGate):
    """Verify local operational event metrics are recorded and counted strictly in local ledger."""
    gate.record_local_event("desktop_started", category="lifecycle")
    gate.record_local_event("agent_step_executed", category="agent", duration_ms=124.5)
    gate.record_local_event("agent_step_executed", category="agent", duration_ms=150.0)

    assert gate.total_local_events == 3

    summary = gate.get_summary()
    assert summary["fence_status"] == "ENFORCING"
    assert summary["total_local_events"] == 3
    assert summary["event_counters"]["agent_step_executed"] == 2
    assert summary["event_counters"]["desktop_started"] == 1


def test_global_singleton_telemetry_gate():
    """Verify singleton lifecycle for SovereignTelemetryGate."""
    g1 = get_telemetry_gate()
    g2 = get_telemetry_gate()
    assert g1 is g2

    g3 = reset_telemetry_gate()
    assert g3 is not g1
    assert get_telemetry_gate() is g3
