"""
Deterministic integration test suite for Hydra Desktop Session Replay,
Workspace Context Path Confinement, and Model Gateway Fallback.
Complies with AGENTS.md genome invariants: zero fake tests, real interfaces, exit code 0.
"""

import json
import os
import tempfile
from typing import Any, Dict
import pytest
from fastapi.testclient import TestClient

from hydra_cli.agent_runner import (
    AgentState,
    AutonomousAgentRunner,
    validate_session_trace,
    get_agent_runner,
    reset_agent_runner,
)
from hydra_cli.desktop import (
    create_desktop_app,
    resolve_and_complete_with_fallback,
    FALLBACK_CHAINS,
)
from hydra_cli.native_tools import NativeToolRegistry


@pytest.fixture(scope="module")
def desktop_client():
    app = create_desktop_app()
    with TestClient(app) as client:
        yield client


@pytest.fixture
def temp_workspace():
    """Create isolated temporary workspace directory for path confinement validation."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        # Populate workspace structure
        src_dir = os.path.join(tmp_dir, "src")
        os.makedirs(src_dir, exist_ok=True)
        with open(os.path.join(tmp_dir, "project.json"), "w", encoding="utf-8") as f:
            f.write('{"name": "sovereign-test", "version": "1.0.0"}')
        with open(os.path.join(src_dir, "main.py"), "w", encoding="utf-8") as f:
            f.write('def entrypoint():\n    return "operational"\n')
        yield tmp_dir


def test_json_trace_generation_and_schema_validation():
    """
    Test JSON trace generation, schema validation, and snapshot fidelity.
    """
    runner = AutonomousAgentRunner(max_steps=5)
    plan = [
        {"action": "browser_inspect", "selector": "body"},
        {"action": "browser_inspect", "selector": "body"},
        {"action": "browser_inspect", "selector": "body"},
    ]

    res = runner.run_task(task_description="Telemetry diagnostic trace workflow", steps=plan)
    assert res.get("status") == AgentState.COMPLETED
    assert res.get("step_count") == 3

    # Generate session trace
    trace = runner.export_session_trace()

    # Validate against v1.0.0 schema
    is_valid, err = validate_session_trace(trace)
    assert is_valid is True, f"Trace schema validation failed: {err}"
    assert trace["schema_version"] == "1.0.0"
    assert trace["task"] == "Telemetry diagnostic trace workflow"
    assert trace["state"] == AgentState.COMPLETED
    assert trace["step_count"] == 3
    assert len(trace["history"]) == 3
    assert trace["duration_ms"] >= 0

    # Ensure JSON serializability and roundtrip fidelity
    json_str = json.dumps(trace, indent=2)
    deserialized = json.loads(json_str)
    assert deserialized == trace


def test_snapshot_reconstruction_fidelity():
    """
    Test faithful state reconstruction from serialized execution trace.
    """
    runner = AutonomousAgentRunner(max_steps=4)
    plan = [
        {"action": "browser_inspect", "selector": "body"},
        {"action": "browser_inspect", "selector": "body"},
    ]
    runner.run_task(task_description="Snapshot reconstruction validation", steps=plan)
    trace = runner.export_session_trace()

    # Reconstruct runner instance
    reconstructed = AutonomousAgentRunner.reconstruct_from_trace(trace)
    assert reconstructed.state == AgentState.COMPLETED
    assert reconstructed.current_step == 2
    assert reconstructed.current_task == "Snapshot reconstruction validation"
    assert len(reconstructed.history) == 2
    assert reconstructed.history == trace["history"]

    # Reconstructed status snapshot matches original
    orig_status = runner.get_status()
    rec_status = reconstructed.get_status()
    assert rec_status["status"] == orig_status["status"]
    assert rec_status["step_count"] == orig_status["step_count"]
    assert rec_status["history_count"] == orig_status["history_count"]


def test_workspace_file_tools_path_confinement_parent_traversal(temp_workspace: str):
    """
    Test path confinement in workspace file tools blocking parent traversal attempts.
    """
    tools = NativeToolRegistry(cwd=temp_workspace)

    # 1. Allowed operation inside workspace
    read_ok = tools.read_file("project.json")
    assert not isinstance(read_ok, dict) or not read_ok.get("isError")
    assert "sovereign-test" in str(read_ok)

    # 2. Block parent directory traversal on read
    traversal_read = tools.read_file("../outside.txt")
    assert isinstance(traversal_read, dict)
    assert traversal_read.get("isError") is True
    assert "Security violation" in traversal_read.get("error", "")

    deep_traversal = tools.read_file("../../etc/shadow")
    assert isinstance(deep_traversal, dict)
    assert deep_traversal.get("isError") is True
    assert "Security violation" in deep_traversal.get("error", "")

    # 3. Block parent traversal on write
    traversal_write = tools.write_file("../escape.txt", "malicious payload")
    assert isinstance(traversal_write, dict)
    assert traversal_write.get("isError") is True
    assert "Security violation" in traversal_write.get("error", "")

    # 4. Block parent traversal on edit
    traversal_edit = tools.edit_file("../escape.txt", "old", "new")
    assert isinstance(traversal_edit, dict)
    assert traversal_edit.get("isError") is True
    assert "Security violation" in traversal_edit.get("error", "")

    # 5. Block parent traversal on list_dir
    traversal_list = tools.list_dir("..")
    assert isinstance(traversal_list, dict)
    assert traversal_list.get("isError") is True
    assert "Security violation" in traversal_list.get("error", "")

    # 6. Block parent traversal on grep_search
    traversal_grep = tools.grep_search("needle", path="../")
    assert isinstance(traversal_grep, dict)
    assert traversal_grep.get("isError") is True
    assert "Security violation" in traversal_grep.get("error", "")

    # 7. Block parent traversal on find_files
    traversal_find = tools.find_files("*.txt", path="../")
    assert isinstance(traversal_find, dict)
    assert traversal_find.get("isError") is True
    assert "Security violation" in traversal_find.get("error", "")


def test_workspace_file_tools_path_confinement_absolute_escape(temp_workspace: str):
    """
    Test path confinement blocking absolute escape paths outside workspace root.
    """
    tools = NativeToolRegistry(cwd=temp_workspace)

    # External absolute path pointing outside temp_workspace
    outside_dir = tempfile.gettempdir()
    outside_file = os.path.join(outside_dir, "outside_canary.txt")
    with open(outside_file, "w", encoding="utf-8") as f:
        f.write("canary token")

    try:
        abs_read = tools.read_file(outside_file)
        assert isinstance(abs_read, dict)
        assert abs_read.get("isError") is True
        assert "Security violation" in abs_read.get("error", "")

        abs_write = tools.write_file(outside_file, "overwrite")
        assert isinstance(abs_write, dict)
        assert abs_write.get("isError") is True
        assert "Security violation" in abs_write.get("error", "")
    finally:
        if os.path.exists(outside_file):
            os.remove(outside_file)


def test_gateway_provider_fallback_under_simulated_429():
    """
    Test gateway model alias resolution and fallback resilience under simulated provider 429 rate limit.
    """
    call_log = []

    def mock_completer(resolved_model: str, prompt: str) -> str:
        call_log.append(resolved_model)
        # Primary model simulates provider 429 RateLimitError
        if "sonnet" in resolved_model.lower():
            raise RuntimeError("Provider HTTP 429: rate limit exceeded on tier frontier")
        # Fallback model succeeds
        return f"Consensus synthesis from {resolved_model} for query: {prompt}"

    res = resolve_and_complete_with_fallback(
        model="sonnet 5.5",
        prompt="verify mathematical invariants",
        fallbacks=["gemini 3.8", "gpt-6.1"],
        completer=mock_completer,
    )

    assert not res.get("isError")
    assert res.get("fallback_triggered") is True
    assert res.get("model_requested") == "sonnet 5.5"
    assert res.get("model_used") == "gemini 3.8"
    assert "Consensus synthesis" in res.get("content", "")
    assert len(res.get("attempts", [])) == 1
    assert "429" in res["attempts"][0]["error"]
    assert len(call_log) == 2


def test_desktop_fastapi_gateway_and_session_endpoints(desktop_client: TestClient, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("hydra_cli.complete", lambda alias, prompt, **kw: f"Operational completion for {alias}: {prompt}")
    """
    Test desktop API endpoints: /api/gateway/complete, /api/agent/session/export, /api/agent/session/replay.
    """
    # 1. Gateway complete endpoint
    gw_res = desktop_client.post("/api/gateway/complete", json={
        "model": "sonnet 5.5",
        "prompt": "test ping",
    })
    assert gw_res.status_code == 200
    gw_data = gw_res.json()
    assert "model_requested" in gw_data
    assert "model_used" in gw_data

    # 2. Session export endpoint
    export_res = desktop_client.get("/api/agent/session/export")
    assert export_res.status_code == 200
    trace_data = export_res.json()
    assert trace_data.get("schema_version") == "1.0.0"
    assert "session_id" in trace_data
    assert "history" in trace_data

    # 3. Session replay endpoint with valid trace
    replay_res = desktop_client.post("/api/agent/session/replay", json=trace_data)
    assert replay_res.status_code == 200
    replay_data = replay_res.json()
    assert replay_data.get("status") == "reconstructed"
    assert "reconstructed_state" in replay_data

    # 4. Session replay endpoint with invalid trace rejects with 400
    bad_replay_res = desktop_client.post("/api/agent/session/replay", json={"invalid": "schema"})
    assert bad_replay_res.status_code == 400
