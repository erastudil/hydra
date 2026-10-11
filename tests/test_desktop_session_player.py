"""
Integration test suite for Hydra Desktop Session Playback Engine,
Action Tracer, and Packaging Distribution Validator.
Complies with AGENTS.md genome invariants: zero fake tests, real interfaces, exit code 0.
"""

import json
import os
import tempfile
import pytest

from desktop.session_player import (
    SessionTracePlayer,
    ActionTracer,
    create_player_from_runner,
)
from desktop.build_dist import (
    validate_asset_tree,
    validate_api_contracts,
    validate_session_playback_engine,
    generate_distribution_artifacts,
    run_full_validation,
)
from hydra_cli.agent_runner import AutonomousAgentRunner, AgentState, validate_session_trace


def test_action_tracer_recording_and_schema_export():
    """Verify ActionTracer captures real steps and produces compliant v1.0.0 trace."""
    tracer = ActionTracer(task="Automated Codebase Audit", tool_profile="workspace_only", max_steps=10)
    assert tracer.state == AgentState.IDLE
    assert len(tracer.history) == 0

    s1 = tracer.record_step("read_file", {"path": "desktop/SPEC.md"}, {"content": "# desktop specification"}, duration_ms=15.4)
    assert s1["step"] == 1
    assert s1["action"] == "read_file"

    s2 = tracer.record_step("grep_search", {"pattern": "fastapi"}, {"matches": ["fastapi application"]}, duration_ms=22.8)
    assert s2["step"] == 2

    s3 = tracer.record_step("run_command", {"command": "git status"}, {"stdout": "clean", "exit_code": 0}, snapshot="hash-snapshot-1", duration_ms=45.0)
    assert s3["step"] == 3
    assert len(tracer.snapshots) == 1

    trace = tracer.finish(final_state=AgentState.COMPLETED)
    assert tracer.state == AgentState.COMPLETED
    assert trace["step_count"] == 3
    assert trace["tool_profile"] == "workspace_only"

    # Verify against official schema validator
    is_valid, err = validate_session_trace(trace)
    assert is_valid is True, f"Schema validation failed: {err}"


def test_action_tracer_file_persistence():
    """Verify trace saving to and loading from isolated temporary file."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        target_path = os.path.join(tmp_dir, "trace.json")
        tracer = ActionTracer(task="Persistence Verification")
        tracer.record_step("mouse_click", {"x": 100, "y": 200}, {"status": "ok"})
        tracer.finish()
        saved = tracer.save_trace_file(target_path)
        assert os.path.isfile(saved)

        player = SessionTracePlayer()
        ok, err = player.load_file(saved)
        assert ok is True
        assert player.total_steps == 1
        assert player.trace["task"] == "Persistence Verification"


def test_session_trace_player_scrubbing_and_boundaries():
    """Verify forward/backward scrubbing, direct seeking, and boundary clamping."""
    tracer = ActionTracer(task="Boundary Scrubbing Flight")
    for i in range(5):
        tracer.record_step(f"action_{i}", {"index": i}, {"result": f"res_{i}"}, duration_ms=10.0 * (i + 1))
    trace = tracer.finish()

    player = SessionTracePlayer(trace)
    assert player.total_steps == 5
    assert player.cursor == 0
    assert player.is_at_start is True
    assert player.is_finished is False

    # Forward scrubbing
    step1 = player.next_step()
    assert step1["action"] == "action_0"
    assert player.cursor == 1

    step2 = player.next_step()
    assert step2["action"] == "action_1"
    assert player.cursor == 2

    # Backward scrubbing
    prev1 = player.prev_step()
    assert prev1["action"] == "action_1"
    assert player.cursor == 1

    # Direct seek
    player.seek(4)
    assert player.cursor == 4
    curr = player.get_current_step()
    assert curr["action"] == "action_4"

    # Seeking past boundaries
    player.seek(100)
    assert player.cursor == 5
    assert player.is_finished is True
    assert player.next_step() is None

    player.seek(-50)
    assert player.cursor == 0
    assert player.is_at_start is True
    assert player.prev_step() is None


def test_session_trace_player_play_all_and_summary():
    """Verify automated playback with callback and summary aggregation."""
    tracer = ActionTracer(task="Telemetry Aggregation")
    tracer.record_step("browser_navigate", {"url": "https://example.com"}, {"status": 200})
    tracer.record_step("browser_click", {"selector": "#btn"}, {"isError": True, "error": "Not found"})
    tracer.record_step("run_command", {"command": "dir"}, {"stdout": "file1\n"})
    trace = tracer.finish()

    player = SessionTracePlayer(trace)
    collected = []
    player.play_all(delay_sec=0.0, step_callback=lambda idx, step: collected.append(step["action"]))

    assert len(collected) == 3
    assert collected == ["browser_navigate", "browser_click", "run_command"]
    assert player.is_finished is True

    summary = player.export_summary()
    assert summary["total_steps"] == 3
    assert summary["error_count"] == 1
    assert summary["success_rate"] == 66.7
    assert summary["category_distribution"]["browser"] == 2
    assert summary["category_distribution"]["workspace"] == 1


def test_session_player_markdown_and_html_generation():
    """Verify generation of Markdown audit report and HTML player."""
    tracer = ActionTracer(task="Visualization Audit")
    tracer.record_step("screen_capture", {}, {"data": "base64..."})
    tracer.record_step("write_file", {"path": "out.txt"}, {"bytes": 42})
    trace = tracer.finish()

    player = SessionTracePlayer(trace)
    md = player.render_markdown_report()
    assert "# Session Replay Audit Report" in md
    assert "| `screen_capture` | os | 1 |" in md
    assert "| `write_file` | workspace | 1 |" in md

    html_code = player.render_html_player()
    assert "<!DOCTYPE html>" in html_code
    assert "Hydra Session Player" in html_code
    assert "timeline-list" in html_code
    assert "btn-play" in html_code


def test_create_player_from_runner():
    """Verify bridge between AutonomousAgentRunner and SessionTracePlayer."""
    runner = AutonomousAgentRunner(max_steps=3)
    plan = [
        {"action": "browser_inspect", "selector": "html"},
        {"action": "browser_inspect", "selector": "body"},
    ]
    runner.run_task("Runner Bridge Verification", steps=plan)
    player = create_player_from_runner(runner)

    assert player.total_steps == 2
    assert player.trace["task"] == "Runner Bridge Verification"
    assert player.trace["state"] == AgentState.COMPLETED


def test_build_dist_full_validation_gate():
    """Verify build_dist packaging validator executes with exit code 0."""
    rc = run_full_validation()
    assert rc == 0

    assets_ok, missing = validate_asset_tree()
    assert assets_ok is True, f"Missing assets: {missing}"

    api_ok, api_errs = validate_api_contracts()
    assert api_ok is True, f"API errors: {api_errs}"

    player_ok, player_errs = validate_session_playback_engine()
    assert player_ok is True, f"Playback errors: {player_errs}"

    with tempfile.TemporaryDirectory() as tmp_dist:
        artifacts = generate_distribution_artifacts(tmp_dist)
        assert os.path.isfile(artifacts["launcher"])
        assert os.path.isfile(artifacts["batch"])
