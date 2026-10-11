"""
Integration test suite for Hydra Desktop Session Playback Engine and Action Tracer.
Complies with AGENTS.md genome invariants: zero fake tests, real interfaces, exit code 0.
"""

from __future__ import annotations

import json
import os
import tempfile
import pytest

from desktop.session_player import (
    SessionTracePlayer,
    ActionTracer,
    create_player_from_runner,
)
from hydra_cli.agent_runner import (
    AutonomousAgentRunner,
    AgentState,
    validate_session_trace,
)


def test_action_tracer_lifecycle_and_schema_validation():
    """Verify ActionTracer captures discrete steps and exports compliant v1.0.0 traces."""
    tracer = ActionTracer(task="Flight telemetry audit", tool_profile="full", max_steps=12)
    assert tracer.state == AgentState.IDLE
    assert len(tracer.history) == 0

    step1 = tracer.record_step(
        action="browser_navigate",
        params={"url": "https://example.org"},
        result={"status": 200},
        duration_ms=35.5,
    )
    assert step1["step"] == 1
    assert step1["action"] == "browser_navigate"
    assert step1["duration_ms"] == 35.5

    step2 = tracer.record_step(
        action="read_file",
        params={"path": "desktop/SPEC.md"},
        result={"content": "spec payload"},
        snapshot="sha256-snapshot-alpha",
        duration_ms=12.2,
    )
    assert step2["step"] == 2
    assert len(tracer.snapshots) == 1

    trace = tracer.finish(final_state=AgentState.COMPLETED)
    assert tracer.state == AgentState.COMPLETED
    assert trace["schema_version"] == "1.0.0"
    assert trace["step_count"] == 2
    assert trace["duration_ms"] >= 0.0

    is_valid, err = validate_session_trace(trace)
    assert is_valid is True, f"Schema validation failure: {err}"


def test_action_tracer_json_and_jsonl_persistence():
    """Verify trace serialization, file persistence, and JSONL stream parsing."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        json_path = os.path.join(tmp_dir, "trace.json")
        jsonl_path = os.path.join(tmp_dir, "stream.jsonl")

        tracer = ActionTracer(task="File persistence flight")
        tracer.record_step("mouse_click", {"x": 300, "y": 450}, {"status": "ok"}, duration_ms=5.0)
        tracer.record_step("key_tap", {"key": "enter"}, {"status": "ok"}, duration_ms=4.0)
        trace = tracer.finish()

        # Save standard JSON
        tracer.save_trace_file(json_path)
        assert os.path.isfile(json_path)

        player_json = SessionTracePlayer()
        ok, err = player_json.load_file(json_path)
        assert ok is True
        assert player_json.total_steps == 2
        assert player_json.trace["task"] == "File persistence flight"

        # Create JSONL stream file
        with open(jsonl_path, "w", encoding="utf-8") as f:
            for step in trace["history"]:
                f.write(json.dumps(step) + "\n")

        player_jsonl = SessionTracePlayer()
        ok_jsonl, err_jsonl = player_jsonl.load_file(jsonl_path)
        assert ok_jsonl is True
        assert player_jsonl.total_steps == 2
        assert player_jsonl.history[0]["action"] == "mouse_click"
        assert player_jsonl.history[1]["action"] == "key_tap"


def test_session_player_navigation_scrubbing_and_clamping():
    """Verify bidirectional scrubbing, cursor clamping, and boundary protections."""
    tracer = ActionTracer(task="Navigation Scrubber")
    for i in range(4):
        tracer.record_step(f"step_action_{i}", {"val": i}, {"out": f"ok_{i}"})
    trace = tracer.finish()

    player = SessionTracePlayer(trace)
    assert player.cursor == 0
    assert player.is_at_start is True
    assert player.is_finished is False
    assert player.prev_step() is None

    # Step forward
    s0 = player.next_step()
    assert s0["action"] == "step_action_0"
    assert player.cursor == 1
    assert player.is_at_start is False

    s1 = player.next_step()
    assert s1["action"] == "step_action_1"
    assert player.cursor == 2

    # Step backward
    b1 = player.prev_step()
    assert b1["action"] == "step_action_1"
    assert player.cursor == 1

    # Seek clamping
    player.seek(3)
    assert player.cursor == 3
    curr = player.get_current_step()
    assert curr["action"] == "step_action_3"

    # Out-of-bounds seeking
    player.seek(999)
    assert player.cursor == 4
    assert player.is_finished is True
    assert player.next_step() is None

    player.seek(-50)
    assert player.cursor == 0
    assert player.is_at_start is True


def test_session_player_automated_playback_and_callbacks():
    """Verify automated play_all loop with callback dispatch and speed adjustments."""
    tracer = ActionTracer(task="Automated Loop Flight")
    tracer.record_step("read_file", {"path": "a.txt"}, {"content": "aaa"})
    tracer.record_step("write_file", {"path": "b.txt"}, {"bytes": 10})
    tracer.record_step("run_command", {"command": "dir"}, {"exit_code": 0})
    trace = tracer.finish()

    player = SessionTracePlayer(trace)
    visited = []

    def on_step(idx: int, step_payload: dict):
        visited.append((idx, step_payload["action"]))

    played = player.play_all(delay_sec=0.0, step_callback=on_step)
    assert len(played) == 3
    assert len(visited) == 3
    assert visited[0] == (0, "read_file")
    assert visited[1] == (1, "write_file")
    assert visited[2] == (2, "run_command")
    assert player.is_finished is True


def test_session_player_telemetry_summary_and_error_tracking():
    """Verify telemetry compilation, error counting, and tool category breakdown."""
    tracer = ActionTracer(task="Telemetry Aggregation Audit")
    tracer.record_step("browser_navigate", {"url": "https://example.org"}, {"status": 200})
    tracer.record_step("mouse_click", {"x": 100, "y": 200}, {"isError": True, "error": "OutOfBounds"})
    tracer.record_step("read_file", {"path": "foo.py"}, {"content": "code"})
    tracer.record_step("custom_tool", {"arg": 1}, {"isError": True, "error": "unsupported"})
    trace = tracer.finish()

    player = SessionTracePlayer(trace)
    summary = player.export_summary()

    assert summary["total_steps"] == 4
    assert summary["error_count"] == 2
    assert summary["success_rate"] == 50.0
    assert summary["category_distribution"]["browser"] == 1
    assert summary["category_distribution"]["os"] == 1
    assert summary["category_distribution"]["workspace"] == 1
    assert summary["category_distribution"]["generic"] == 1
    assert len(summary["timeline"]) == 4


def test_session_player_markdown_and_html_generation():
    """Verify Markdown audit export and HTML5 visualizer generation."""
    tracer = ActionTracer(task="Visual Report Audit")
    tracer.record_step("browser_inspect", {"selector": "h1"}, {"text": "Hydra"})
    tracer.record_step("screen_capture", {}, {"data": "png"})
    trace = tracer.finish()

    player = SessionTracePlayer(trace)
    md = player.render_markdown_report()
    assert "# Session Replay Audit Report" in md
    assert "browser_inspect" in md
    assert "screen_capture" in md

    html_page = player.render_html_player()
    assert "<!DOCTYPE html>" in html_page
    assert "Hydra Session Player" in html_page
    assert "timeline-list" in html_page
    assert "btn-play" in html_page
    assert "browser_inspect" in html_page


def test_create_player_from_runner_bridge():
    """Verify direct instantiation of SessionTracePlayer from AutonomousAgentRunner."""
    runner = AutonomousAgentRunner(max_steps=5)
    plan = [
        {"action": "browser_inspect", "selector": "body"},
        {"action": "browser_inspect", "selector": "main"},
    ]
    runner.run_task("Bridge verification task", steps=plan)
    player = create_player_from_runner(runner)

    assert player.total_steps == 2
    assert player.trace["task"] == "Bridge verification task"
    assert player.trace["state"] == AgentState.COMPLETED
    assert player.cursor == 0
    assert player.is_at_start is True


def test_session_player_corrupt_trace_rejection():
    """Verify rejection of corrupt, empty, and invalid trace schemas."""
    player = SessionTracePlayer()

    # Empty dictionary
    with pytest.raises(ValueError):
        player.load_trace({})

    # Missing schema_version
    with pytest.raises(ValueError):
        player.load_trace({"task": "No schema", "history": []})

    # Missing file
    with pytest.raises(FileNotFoundError):
        player.load_file("non_existent_file_path_12345.json")

    # Empty file
    with tempfile.NamedTemporaryFile("w", delete=False) as tf:
        tf_name = tf.name
    try:
        with pytest.raises(ValueError):
            player.load_file(tf_name)
    finally:
        if os.path.exists(tf_name):
            os.remove(tf_name)
