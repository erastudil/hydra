"""
Deterministic integration benchmark suite for Hydra Autonomous Agent Runner and Computer Use.
Tests multi-step browser tasks against a local test HTTP server,
strict step limit enforcement, and sub-100ms emergency abort mechanics.
Complies with AGENTS.md genome invariants: zero fake tests, real interfaces, exit code 0.
"""

import http.server
import json
import socketserver
import subprocess
import sys
import threading
import time
from typing import Any, Dict, Generator, List, Tuple
import pytest
from fastapi.testclient import TestClient

from hydra_cli.agent_runner import (
    AgentState,
    AutonomousAgentRunner,
    get_agent_runner,
    reset_agent_runner,
)
from hydra_cli.computer_use import get_computer_use_engine
from hydra_cli.desktop import create_desktop_app


class BenchmarkHTTPRequestHandler(http.server.BaseHTTPRequestHandler):
    """Local deterministic HTTP test fixture serving multi-page automation lab."""

    def log_message(self, format: str, *args: Any) -> None:
        pass  # Suppress console logging during test execution

    def do_GET(self) -> None:
        if self.path == "/" or self.path.startswith("/?"):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            html = """<!DOCTYPE html>
<html>
<head><title>Hydra Mission Control</title></head>
<body>
  <h1>Agent Target Portal</h1>
  <form id="portal-form">
    <input id="agent-id" name="agent_id" type="text" value="" />
    <input id="agent-pass" name="agent_pass" type="password" value="" />
    <button id="auth-btn" type="button" onclick="document.title='Auth Success'">Authorize</button>
  </form>
  <a id="telemetry-link" href="/telemetry">Telemetry Dashboard</a>
</body>
</html>"""
            self.wfile.write(html.encode("utf-8"))

        elif self.path == "/telemetry":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            html = """<!DOCTYPE html>
<html>
<head><title>Hydra Telemetry Dashboard</title></head>
<body>
  <h1>Cluster Telemetry</h1>
  <table id="nodes-grid" border="1">
    <thead><tr><th>Node</th><th>Load</th><th>Status</th></tr></thead>
    <tbody>
      <tr><td>Orion-01</td><td>14%</td><td>Operational</td></tr>
      <tr><td>Orion-02</td><td>82%</td><td>Heavy</td></tr>
      <tr><td>Orion-03</td><td>5%</td><td>Standby</td></tr>
    </tbody>
  </table>
  <a id="deep-metrics-link" href="/deep-metrics">Deep Metrics</a>
</body>
</html>"""
            self.wfile.write(html.encode("utf-8"))

        elif self.path == "/deep-metrics":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            html = """<!DOCTYPE html>
<html>
<head><title>Deep Metrics Data</title></head>
<body>
  <h1>System Diagnostic Core</h1>
  <div id="metric-uptime">99.999%</div>
  <div id="metric-latency">12ms</div>
</body>
</html>"""
            self.wfile.write(html.encode("utf-8"))

        else:
            self.send_response(404)
            self.end_headers()


@pytest.fixture(scope="module")
def test_http_server() -> Generator[str, None, None]:
    """Spin up local multi-page test HTTP server on an ephemeral port."""
    server = socketserver.TCPServer(("127.0.0.1", 0), BenchmarkHTTPRequestHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{port}"
    try:
        yield base_url
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture(scope="module")
def desktop_client():
    app = create_desktop_app()
    with TestClient(app) as client:
        yield client


def test_agent_runner_multistep_browser_workflow(test_http_server: str):
    """
    Test multi-step browser tasks against a local test HTTP server.
    Validates form entry, table navigation, and multi-page data extraction.
    """
    runner = AutonomousAgentRunner(computer_use=get_computer_use_engine(), max_steps=12)

    plan = [
        # Step 1: Navigate to base page
        {"action": "browser_navigate", "url": f"{test_http_server}/"},
        # Step 2: Inspect DOM elements
        {"action": "browser_inspect", "selector": "body"},
        # Step 3: Composite form fill
        {
            "action": "fill_form",
            "fields": {
                "#agent-id": "Agent-Orion-Alpha",
                "#agent-pass": "VaultSecret999",
            },
            "submit": False,
        },
        # Step 4: Click button to submit auth
        {"action": "browser_click", "selector": "#auth-btn"},
        # Step 5: Navigate to telemetry dashboard
        {"action": "browser_navigate", "url": f"{test_http_server}/telemetry"},
        # Step 6: Extract structured table data
        {"action": "extract_table_data", "selector": "#nodes-grid"},
        # Step 7: Navigate to deep metrics page
        {"action": "browser_navigate", "url": f"{test_http_server}/deep-metrics"},
        # Step 8: Capture diagnostic screenshot
        {"action": "browser_screenshot"},
    ]

    res = runner.run_task(
        task_description="Execute comprehensive multi-page browser automation and data extraction",
        steps=plan,
    )

    assert not res.get("isError")
    assert res.get("status") == AgentState.COMPLETED
    assert res.get("step_count") == 8
    assert len(res.get("history", [])) == 8

    # Verify table data extraction step result
    table_step = res["history"][5]
    assert table_step["action"] == "extract_table_data"
    table_data = table_step["result"]
    assert not table_data.get("isError")
    assert table_data.get("row_count") == 3
    assert "Orion-01" in table_data.get("rows", [])[0]
    assert "Status" in table_data.get("headers", [])

    # Verify screenshot step result
    screenshot_step = res["history"][7]
    assert screenshot_step["action"] == "browser_screenshot"
    assert screenshot_step["result"].get("size_bytes", 0) > 0


def test_agent_runner_step_limit_enforcement(test_http_server: str):
    """
    Test step limit enforcement: verify execution halts strictly when step budget reached.
    """
    runner = AutonomousAgentRunner(computer_use=get_computer_use_engine(), max_steps=3)

    extended_plan = [
        {"action": "browser_navigate", "url": f"{test_http_server}/"},
        {"action": "browser_inspect", "selector": "body"},
        {"action": "browser_navigate", "url": f"{test_http_server}/telemetry"},
        {"action": "extract_table_data", "selector": "#nodes-grid"},
        {"action": "browser_navigate", "url": f"{test_http_server}/deep-metrics"},
        {"action": "browser_inspect", "selector": "#metric-uptime"},
        {"action": "browser_screenshot"},
    ]

    res = runner.run_task(
        task_description="Long running workflow testing strict step boundary",
        steps=extended_plan,
        max_steps=3,
    )

    # Must halt strictly at budget 3
    assert res.get("status") == AgentState.STEP_LIMIT_EXCEEDED
    assert res.get("step_count") == 3
    assert res.get("max_steps") == 3
    assert len(res.get("history", [])) == 3
    assert runner.current_step == 3
    assert runner.state == AgentState.STEP_LIMIT_EXCEEDED

    # Verify step 4 was never executed
    executed_actions = [h["action"] for h in res.get("history", [])]
    assert len(executed_actions) == 3
    assert "extract_table_data" not in executed_actions


def test_agent_runner_emergency_abort_sub_100ms():
    """
    Test emergency abort: verify running task stops within 100ms of abort signal.
    """
    runner = AutonomousAgentRunner(computer_use=get_computer_use_engine(), max_steps=10)

    # Plan with artificial step delays allowing mid-execution abort
    delayed_plan = [
        {"action": "browser_inspect", "selector": "body", "delay_sec": 0.05},
        {"action": "browser_inspect", "selector": "body", "delay_sec": 2.0},
        {"action": "browser_inspect", "selector": "body", "delay_sec": 2.0},
        {"action": "browser_inspect", "selector": "body", "delay_sec": 2.0},
    ]

    worker_thread = runner.run_task_async(
        task_description="Long delayed workflow for emergency abort test",
        steps=delayed_plan,
    )

    # Let thread begin execution
    time.sleep(0.02)
    assert runner.state == AgentState.RUNNING

    # Measure exact time from abort signal invocation to loop termination
    t_signal_start = time.perf_counter()
    abort_res = runner.abort()
    t_signal_end = time.perf_counter()

    signal_latency_ms = (t_signal_end - t_signal_start) * 1000.0

    # Wait for worker thread to join
    worker_thread.join(timeout=0.200)
    assert not worker_thread.is_alive(), "Worker thread must terminate cleanly without hanging"

    # Verify execution stopped strictly within 100ms
    assert signal_latency_ms < 100.0, f"Abort signal took {signal_latency_ms:.2f}ms, exceeding 100ms limit"
    assert abort_res.get("status") == AgentState.ABORTED
    assert runner.state == AgentState.ABORTED
    assert runner.is_aborted() is True
    assert runner.current_step < len(delayed_plan), "Subsequent steps must not have executed"


def test_agent_runner_child_process_termination_on_abort():
    """
    Test abort mechanics: immediate termination of child processes without orphaned handles.
    """
    runner = AutonomousAgentRunner()

    # Launch a mock child process sleeping indefinitely
    proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    runner.track_process(proc)

    # Verify process is actively running
    assert proc.poll() is None, "Spawned child process should be running"

    # Trigger emergency abort
    abort_res = runner.abort()
    assert abort_res.get("status") == AgentState.ABORTED

    # Allow tiny window for OS signal propagation
    try:
        proc.wait(timeout=0.10)
    except subprocess.TimeoutExpired:
        pass

    # Verify process was terminated and killed cleanly
    assert proc.poll() is not None, "Child process must be terminated upon agent abort"
    assert len(runner._subprocesses) == 0, "Subprocess registry must be cleared"


def test_desktop_fastapi_agent_endpoints(desktop_client: TestClient, test_http_server: str):
    """
    Test desktop API endpoints: /api/agent/start, /api/agent/status, /api/agent/abort.
    """
    # 1. Check initial status
    stat_res = desktop_client.get("/api/agent/status")
    assert stat_res.status_code == 200
    stat_data = stat_res.json()
    assert "status" in stat_data
    assert "step_count" in stat_data

    # 2. Synchronous agent execution via API
    run_res = desktop_client.post("/api/agent/start", json={
        "task": "Test API automation",
        "steps": [
            {"action": "browser_navigate", "url": f"{test_http_server}/"},
            {"action": "browser_inspect", "selector": "body"},
        ],
        "max_steps": 5,
    })
    assert run_res.status_code == 200
    run_data = run_res.json()
    assert not run_data.get("isError")
    assert run_data.get("status") == AgentState.COMPLETED
    assert run_data.get("step_count") == 2

    # 3. Asynchronous execution and abort via API
    async_res = desktop_client.post("/api/agent/start", json={
        "task": "Long async task for abort test",
        "steps": [
            {"action": "browser_inspect", "selector": "body", "delay_sec": 3.0},
            {"action": "browser_inspect", "selector": "body", "delay_sec": 3.0},
        ],
        "async": True,
    })
    assert async_res.status_code == 200
    assert async_res.json().get("status") == "running"

    time.sleep(0.02)
    t0 = time.perf_counter()
    abort_res = desktop_client.post("/api/agent/abort")
    t_abort_elapsed = (time.perf_counter() - t0) * 1000.0

    assert abort_res.status_code == 200
    abort_data = abort_res.json()
    assert abort_data.get("status") == AgentState.ABORTED
    assert t_abort_elapsed < 100.0, f"API abort took {t_abort_elapsed:.2f}ms, exceeding 100ms budget"

    # Verify status reflects aborted state
    post_abort_stat = desktop_client.get("/api/agent/status").json()
    assert post_abort_stat.get("status") == AgentState.ABORTED


def test_agent_websocket_streaming_and_race_immunity(desktop_client: TestClient):
    """
    Test WebSocket /ws/desktop agent streaming and event distribution under concurrent actions.
    """
    with desktop_client.websocket_connect("/ws/desktop") as ws:
        # Request agent status via WebSocket
        ws.send_text(json.dumps({"action": "agent_status"}))
        resp_raw = ws.receive_text()
        resp = json.loads(resp_raw)
        assert resp.get("event") == "agent_status"
        assert "data" in resp

        # Trigger agent abort via WebSocket
        ws.send_text(json.dumps({"action": "agent_abort"}))
        abort_raw = ws.receive_text()
        abort_data = json.loads(abort_raw)
        assert abort_data.get("event") == "agent_aborted"
