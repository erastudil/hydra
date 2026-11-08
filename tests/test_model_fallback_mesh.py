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


# ===========================================================================
# WO-2 Launch Preflight Verification Suite (PREFLIGHT-SPEC.md)
# ===========================================================================


def test_modes_default_is_local():
    """Verify hydra_cli/modes.json default_mode is 'local' (Part A / BRIEF item 1)."""
    import json
    import os
    from hydra_cli import modes

    modes_path = os.path.join(os.path.dirname(modes.__file__), "modes.json")
    with open(modes_path, encoding="utf-8") as fh:
        raw = json.load(fh)
    assert raw["default_mode"] == "local"


def test_preflight_closed_failure_reasons():
    """Verify failure reasons come strictly from PREFLIGHT-SPEC.md §5.4 closed list."""
    from hydra_cli.preflight import is_valid_failure_reason

    for valid in (
        "timeout",
        "http 401",
        "http 503",
        "model absent",
        "context 4096 < 131072",
        "no server",
        "no key configured",
    ):
        assert is_valid_failure_reason(valid), f"Expected valid reason: {valid}"

    for invalid in (
        "",
        "degraded",
        "unknown error",
        "connection refused",
        "http abc",
        "context 131072",
    ):
        assert not is_valid_failure_reason(invalid), f"Expected invalid reason: {invalid}"


def test_preflight_local_light_free_routes_and_context_gate(monkeypatch, tmp_path):
    """Verify LOCAL (/v1/models + /props n_ctx>=131072), LIGHT, FREE, caching, and notices."""
    import json
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer
    from hydra_cli import preflight

    state = {
        "models": ["gemma-e2b", "muse-spark-1.3-contributor", "nvidia/nemotron-3.5-lightning:free"],
        "n_ctx": 131072,
        "status": 200,
        "hits": [],
    }

    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            state["hits"].append(("GET", self.path))
            if state["status"] != 200:
                self.send_response(state["status"])
                self.end_headers()
                return
            if self.path.endswith("/v1/models") or self.path.endswith("/models"):
                body = json.dumps({"data": [{"id": m} for m in state["models"]]}).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(body)
                return
            if self.path.endswith("/props"):
                body = json.dumps(
                    {"default_generation_settings": {"n_ctx": state["n_ctx"]}}
                ).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(body)
                return
            self.send_response(404)
            self.end_headers()

        def do_POST(self):
            state["hits"].append(("POST", self.path))
            body = json.dumps({"choices": [{"message": {"content": "ok"}}]}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            return

    server = HTTPServer(("127.0.0.1", 0), _Handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{port}"

    try:
        monkeypatch.setenv("CHEAPERINFERENCE_API_KEY", "test-ci-key-123456")
        monkeypatch.setenv("CHEAPERINFERENCE_API_BASE", f"{base_url}/v1")
        monkeypatch.delenv("NVIDIA_API_KEY", raising=False)

        preflight.invalidate_preflight_cache()
        report = preflight.run_preflight(
            mode="local",
            force=True,
            local_ports=[port],
            light_base_url=f"{base_url}/v1",
            check_balance=False,
        )
        assert report.local.ok is True
        assert report.local.n_ctx == 131072
        assert report.light.ok is True
        assert report.free.status == "FAIL"
        assert report.free.reason == "no key configured"
        assert report.free.gets_count == 0
        assert report.completion_probes_count == 0
        assert report.gets_count <= 3
        strip = preflight.format_route_strip(report)
        assert strip == "local ● gemma-e2b 128K · light ● spark · free ○ no key configured"
        assert preflight.format_preflight_notice(report, mode="local") is None

        # With NVIDIA_API_KEY present, free route is SKIPPED (0 GETs) when local and light pass
        monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-test-key-123456")
        preflight.invalidate_preflight_cache()
        report_with_nv = preflight.run_preflight(
            mode="local",
            force=True,
            local_ports=[port],
            light_base_url=f"{base_url}/v1",
            free_base_url=f"{base_url}/v1",
            check_balance=False,
        )
        assert report_with_nv.free.status == "SKIPPED"
        assert report_with_nv.free.gets_count == 0

        # Context mismatch (< 131072) fails with exact reason "context <n> < 131072"
        state["n_ctx"] = 4096
        preflight.invalidate_preflight_cache()
        bad_ctx = preflight.probe_local_route(ports=[port])
        assert bad_ctx.ok is False
        assert bad_ctx.reason == "context 4096 < 131072"

        # Model absent fails with "model absent"
        state["n_ctx"] = 131072
        state["models"] = ["other-model"]
        absent = preflight.probe_local_route(ports=[port])
        assert absent.ok is False
        assert absent.reason == "model absent"

        # Down local server with managed state file reports managed start available
        state_file = tmp_path / "local-server-state.json"
        state_file.write_text(json.dumps({"pid": 99999, "port": 8080}), encoding="utf-8")
        monkeypatch.setattr("hydra_cli.local_server.state_path", lambda: str(state_file))
        down_res = preflight.probe_local_route(ports=[1])
        assert down_res.ok is False
        assert down_res.reason == "no server"
        assert down_res.managed_configured is True
        assert down_res.start_available is True
        assert down_res.down_message == "local DOWN: no server on :8080 (managed server configured; start available)"

        down_report = preflight.PreflightReport(
            mode="local",
            default_route="local",
            local=down_res,
            light=report.light,
            free=report.free,
        )
        notice = preflight.format_preflight_notice(down_report, mode="local")
        assert notice is not None
        assert "local DOWN: no server on :8080 (managed server configured; start available)" in notice
        assert "never substitutes a cloud route" in notice

        # Light mode when spark is down announces free fallback
        light_down_report = preflight.PreflightReport(
            mode="light",
            default_route="light",
            local=down_res,
            light=preflight.RouteProbeResult(
                route="light",
                status="FAIL",
                model_id="muse-spark-1.3-contributor",
                short_label="spark",
                reason="http 503",
            ),
            free=report.free,
        )
        l_notice = preflight.format_preflight_notice(light_down_report, mode="light")
        assert l_notice == "spark unreachable (http 503). Answering on free (Nemotron) until spark returns."
    finally:
        server.shutdown()


def test_tui_20s_waiting_on_provider_status():
    """Verify HydraTUI._status shows 'waiting on <provider>... Ns' after 20s pre-first-token wait."""
    import time
    from hydra_cli.tui import HydraTUI

    tui = HydraTUI(commands=[])
    tui.provider = "llama.cpp"
    tui._waiting = None
    tui._streaming = False
    tui._busy_since = time.monotonic() - 25.0
    frags = tui._status()
    rendered = "".join(text for _, text in frags)
    assert "waiting on llama.cpp... " in rendered
    assert "25s" in rendered or "26s" in rendered
