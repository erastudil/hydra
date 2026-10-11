"""
Deterministic integration test suite for Hydra Desktop Resilience:
WebSocket Heartbeat & Reconnection Re-attachment, Token Accounting & Pricing Formulas,
and Responsive Coordinate Scaling Fidelity.
Complies with AGENTS.md genome invariants: zero fake tests, real interfaces, exit code 0.
"""

import json
import time
from typing import Any, Dict
import pytest
from fastapi.testclient import TestClient

from hydra_cli.agent_runner import (
    AgentState,
    AutonomousAgentRunner,
    get_agent_runner,
    reset_agent_runner,
)
from hydra_cli.desktop import (
    MODEL_PRICING,
    ResponsiveCoordinateScaler,
    TokenAccountingManager,
    create_desktop_app,
    get_token_accounting,
    reset_token_accounting,
    resolve_and_complete_with_fallback,
)


@pytest.fixture(scope="module")
def desktop_client():
    app = create_desktop_app()
    with TestClient(app) as client:
        yield client


def test_websocket_ping_pong_and_heartbeat_exchange(desktop_client: TestClient):
    """
    Test WebSocket /ws/desktop ping/pong and heartbeat frames maintaining connection.
    """
    with desktop_client.websocket_connect("/ws/desktop") as ws:
        # 1. Plain text ping frame
        ws.send_text("ping")
        resp1_raw = ws.receive_text()
        resp1 = json.loads(resp1_raw)
        assert resp1.get("event") == "pong"
        assert "time" in resp1

        # 2. JSON ping frame with correlation ID
        ws.send_text(json.dumps({"action": "ping", "id": "hb-seq-101"}))
        resp2_raw = ws.receive_text()
        resp2 = json.loads(resp2_raw)
        assert resp2.get("event") == "pong"
        assert resp2.get("id") == "hb-seq-101"

        # 3. JSON ping frame with sequence number
        ws.send_text(json.dumps({"type": "ping", "seq": 42}))
        resp3_raw = ws.receive_text()
        resp3 = json.loads(resp3_raw)
        assert resp3.get("event") == "pong"
        assert resp3.get("seq") == 42

        # 4. Heartbeat frame with health check status
        ws.send_text(json.dumps({"action": "heartbeat"}))
        resp4_raw = ws.receive_text()
        resp4 = json.loads(resp4_raw)
        assert resp4.get("event") == "heartbeat_ack"
        assert resp4.get("status") == "healthy"
        assert "time" in resp4


def test_websocket_reconnection_reattachment_without_task_duplication(desktop_client: TestClient):
    """
    Test WebSocket reconnection re-attachment without duplicate task execution.
    """
    reset_agent_runner()
    runner = get_agent_runner()

    # Plan with artificial delays to hold runner in RUNNING state
    deliberate_plan = [
        {"action": "browser_inspect", "selector": "body", "delay_sec": 0.3},
        {"action": "browser_inspect", "selector": "body", "delay_sec": 0.3},
    ]

    th = runner.run_task_async("resilience task 1", steps=deliberate_plan, max_steps=5)
    time.sleep(0.02)

    # Client 1 connects and observes active task
    with desktop_client.websocket_connect("/ws/desktop") as ws1:
        ws1.send_text(json.dumps({"action": "agent_status"}))
        st1_raw = ws1.receive_text()
        st1 = json.loads(st1_raw)
        assert st1.get("event") == "agent_status"
        data1 = st1.get("data", {})
        assert data1.get("status") == AgentState.RUNNING
        assert (data1.get("current_task") or data1.get("task")) == "resilience task 1"

    # Client 1 disconnects (simulating network drop)
    assert runner.state in (AgentState.RUNNING, AgentState.COMPLETED)
    assert runner.current_task == "resilience task 1"

    # Client 2 reconnects and attaches to existing running state
    with desktop_client.websocket_connect("/ws/desktop") as ws2:
        ws2.send_text(json.dumps({"action": "reconnect"}))
        st2_raw = ws2.receive_text()
        st2 = json.loads(st2_raw)
        assert st2.get("event") == "attached"
        assert st2.get("running_task") == "resilience task 1"
        assert st2.get("reconnected") is True

        # Ensure no task duplication occurred
        assert runner.current_task == "resilience task 1"

        # Terminate task cleanly via WebSocket abort
        ws2.send_text(json.dumps({"action": "agent_abort"}))
        abort_raw = ws2.receive_text()
        abort_data = json.loads(abort_raw)
        assert abort_data.get("event") == "agent_aborted"

    th.join(timeout=1.0)
    assert runner.state == AgentState.ABORTED
    reset_agent_runner()


def test_token_accounting_tier_resolution_and_pricing_formulas():
    """
    Test TokenAccountingManager pricing resolution and cost formulas across model tiers.
    """
    mgr = TokenAccountingManager()

    # Tier resolution
    assert mgr.resolve_pricing("opus 5.5")["tier"] == "frontier"
    assert mgr.resolve_pricing("sonnet 5.5")["tier"] == "frontier"
    assert mgr.resolve_pricing("claude-3-7-sonnet")["tier"] == "frontier"
    assert mgr.resolve_pricing("gpt-6.1")["tier"] == "frontier"
    assert mgr.resolve_pricing("gemini 3.8")["tier"] == "standard"
    assert mgr.resolve_pricing("glm 5.3 flash")["tier"] == "standard"
    assert mgr.resolve_pricing("free")["tier"] == "free"
    assert mgr.resolve_pricing("qwen")["tier"] == "free"
    assert mgr.resolve_pricing("unknown-vendor-model")["tier"] == "standard"

    # Frontier pricing math: Sonnet 5.5 (3.00 prompt, 15.00 completion per million)
    rec_sonnet = mgr.record_usage("sonnet 5.5", prompt_tokens=1_000_000, completion_tokens=1_000_000)
    assert rec_sonnet["prompt_cost_usd"] == 3.0
    assert rec_sonnet["completion_cost_usd"] == 15.0
    assert rec_sonnet["total_cost_usd"] == 18.0

    # Frontier pricing math: Opus 5.5 (15.00 prompt, 75.00 completion per million)
    rec_opus = mgr.record_usage("opus 5.5", prompt_tokens=2_000_000, completion_tokens=500_000)
    assert rec_opus["prompt_cost_usd"] == 30.0
    assert rec_opus["completion_cost_usd"] == 37.5
    assert rec_opus["total_cost_usd"] == 67.5

    # Standard pricing math: Gemini 3.8 (0.50 prompt, 1.50 completion per million)
    rec_gemini = mgr.record_usage("gemini 3.8", prompt_tokens=100_000, completion_tokens=50_000)
    assert rec_gemini["prompt_cost_usd"] == 0.05
    assert rec_gemini["completion_cost_usd"] == 0.075
    assert rec_gemini["total_cost_usd"] == 0.125

    # Free tier pricing math: Qwen / Free Forge (0.00 prompt, 0.00 completion)
    rec_free = mgr.record_usage("qwen-3.8-27b:free", prompt_tokens=500_000, completion_tokens=200_000)
    assert rec_free["prompt_cost_usd"] == 0.0
    assert rec_free["completion_cost_usd"] == 0.0
    assert rec_free["total_cost_usd"] == 0.0


def test_token_accounting_multiple_invocations_summation():
    """
    Test token summation and aggregate cost calculations across multiple model invocations.
    """
    mgr = TokenAccountingManager()

    # Invocation 1: Sonnet 5.5 (10,000 prompt, 2,000 completion) -> $0.03 + $0.03 = $0.06
    mgr.record_usage("sonnet 5.5", prompt_tokens=10_000, completion_tokens=2_000)

    # Invocation 2: Opus 5.5 (5,000 prompt, 1,000 completion) -> $0.075 + $0.075 = $0.15
    mgr.record_usage("opus 5.5", prompt_tokens=5_000, completion_tokens=1_000)

    # Invocation 3: Free Forge (50,000 prompt, 10,000 completion) -> $0.00
    mgr.record_usage("free", prompt_tokens=50_000, completion_tokens=10_000)

    # Invocation 4: Gemini 3.8 (40,000 prompt, 10,000 completion) -> $0.02 + $0.015 = $0.035
    mgr.record_usage("gemini 3.8", prompt_tokens=40_000, completion_tokens=10_000)

    summary = mgr.get_summary()

    # Exact token summation
    assert summary["total_prompt_tokens"] == 10_000 + 5_000 + 50_000 + 40_000  # 105,000
    assert summary["total_completion_tokens"] == 2_000 + 1_000 + 10_000 + 10_000  # 23,000
    assert summary["total_tokens"] == 128_000
    assert summary["record_count"] == 4

    # Exact cost summation
    expected_cost = round(0.06 + 0.15 + 0.0 + 0.035, 6)
    assert abs(summary["total_cost_usd"] - expected_cost) < 1e-6

    # Tier breakdown accuracy
    tb = summary["tier_breakdown"]
    assert tb["frontier"]["prompt_tokens"] == 15_000
    assert tb["frontier"]["completion_tokens"] == 3_000
    assert abs(tb["frontier"]["cost_usd"] - 0.21) < 1e-6

    assert tb["standard"]["prompt_tokens"] == 40_000
    assert tb["standard"]["completion_tokens"] == 10_000
    assert abs(tb["standard"]["cost_usd"] - 0.035) < 1e-6

    assert tb["free"]["prompt_tokens"] == 50_000
    assert tb["free"]["completion_tokens"] == 10_000
    assert tb["free"]["cost_usd"] == 0.0


def test_token_accounting_api_endpoints(desktop_client: TestClient):
    """
    Test Desktop API endpoints: /api/token/accounting GET, POST record, and POST reset.
    """
    # 1. Reset ledger
    rst = desktop_client.post("/api/token/accounting/reset")
    assert rst.status_code == 200
    assert rst.json().get("summary", {}).get("total_tokens") == 0

    # 2. Record usage via API
    rec = desktop_client.post("/api/token/accounting/record", json={
        "model": "sonnet 5.5",
        "prompt_tokens": 20_000,
        "completion_tokens": 5_000,
    })
    assert rec.status_code == 200
    rec_data = rec.json()
    assert rec_data.get("isError") is False
    assert rec_data["recorded"]["total_tokens"] == 25_000
    assert rec_data["recorded"]["total_cost_usd"] > 0

    # 3. Retrieve summary via GET
    get_res = desktop_client.get("/api/token/accounting")
    assert get_res.status_code == 200
    sum_data = get_res.json()
    assert sum_data.get("total_tokens") == 25_000
    assert sum_data.get("record_count") == 1


def test_canvas_scale_factor_math_across_dynamic_dimensions():
    """
    Test ResponsiveCoordinateScaler scale factors and [0, 1000] mapping fidelity
    across varied dynamic window and screen dimensions.
    """
    # Standard 1080p display with 960x540 viewport (50% scale)
    scaler1 = ResponsiveCoordinateScaler(
        screen_width=1920,
        screen_height=1080,
        canvas_width=960,
        canvas_height=540,
    )
    assert scaler1.scale_x == 0.5
    assert scaler1.scale_y == 0.5

    # Center point mapping
    cx, cy = 480, 270
    nx, ny = scaler1.canvas_to_normalized(cx, cy)
    assert nx == 500.0 and ny == 500.0
    sx, sy = scaler1.normalized_to_screen(nx, ny)
    assert sx == 960 and sy == 540

    # Direct canvas-to-screen
    assert scaler1.canvas_to_screen(cx, cy) == (960, 540)
    assert scaler1.screen_to_canvas(960, 540) == (480, 270)

    # Dynamic resize to 1280x720 canvas
    scaler1.update_canvas_dimensions(1280, 720)
    assert abs(scaler1.scale_x - (1280 / 1920)) < 1e-6
    assert abs(scaler1.scale_y - (720 / 1080)) < 1e-6
    assert scaler1.canvas_to_normalized(640, 360) == (500.0, 500.0)

    # Aspect ratio variation: 4:3 canvas (800x600) on 16:9 screen (1920x1080)
    scaler2 = ResponsiveCoordinateScaler(
        screen_width=1920,
        screen_height=1080,
        canvas_width=800,
        canvas_height=600,
    )
    assert abs(scaler2.scale_x - (800 / 1920)) < 1e-6
    assert abs(scaler2.scale_y - (600 / 1080)) < 1e-6

    # 4K Display (3840x2160)
    scaler4k = ResponsiveCoordinateScaler(
        screen_width=3840,
        screen_height=2160,
        canvas_width=1920,
        canvas_height=1080,
    )
    assert scaler4k.scale_x == 0.5
    assert scaler4k.scale_y == 0.5
    assert scaler4k.normalized_to_screen(500.0, 500.0) == (1920, 1080)

    # Round-trip fidelity check across grid points
    test_grid = [0.0, 100.0, 250.0, 500.0, 750.0, 900.0, 1000.0]
    for px_norm in test_grid:
        for py_norm in test_grid:
            scr_x, scr_y = scaler1.normalized_to_screen(px_norm, py_norm)
            norm_back_x, norm_back_y = scaler1.screen_to_normalized(scr_x, scr_y)
            assert abs(px_norm - norm_back_x) <= 1.0
            assert abs(py_norm - norm_back_y) <= 1.0

            canv_x, canv_y = scaler1.normalized_to_canvas(px_norm, py_norm)
            norm_canv_x, norm_canv_y = scaler1.canvas_to_normalized(canv_x, canv_y)
            assert abs(px_norm - norm_canv_x) <= 1.0
            assert abs(py_norm - norm_canv_y) <= 1.0


def test_responsive_coordinates_api_endpoint(desktop_client: TestClient):
    """
    Test Desktop API /api/coordinates/scale endpoint transformations.
    """
    # 1. Canvas to screen conversion
    res1 = desktop_client.post("/api/coordinates/scale", json={
        "action": "canvas_to_screen",
        "x": 480,
        "y": 270,
        "screen_width": 1920,
        "screen_height": 1080,
        "canvas_width": 960,
        "canvas_height": 540,
    })
    assert res1.status_code == 200
    d1 = res1.json()
    assert d1["x"] == 960
    assert d1["y"] == 540
    assert d1["normalized"] == [500.0, 500.0]
    assert d1["scale_x"] == 0.5

    # 2. Screen to canvas conversion
    res2 = desktop_client.post("/api/coordinates/scale", json={
        "action": "screen_to_canvas",
        "x": 960,
        "y": 540,
        "screen_width": 1920,
        "screen_height": 1080,
        "canvas_width": 960,
        "canvas_height": 540,
    })
    assert res2.status_code == 200
    d2 = res2.json()
    assert d2["x"] == 480
    assert d2["y"] == 270

    # 3. Normalized to screen
    res3 = desktop_client.post("/api/coordinates/scale", json={
        "action": "normalized_to_screen",
        "x": 1000.0,
        "y": 1000.0,
        "screen_width": 1920,
        "screen_height": 1080,
    })
    assert res3.status_code == 200
    d3 = res3.json()
    assert d3["x"] == 1920
    assert d3["y"] == 1080

    # 4. Unknown action returns 400
    res4 = desktop_client.post("/api/coordinates/scale", json={"action": "invalid_action"})
    assert res4.status_code == 400
    assert res4.json().get("isError") is True
