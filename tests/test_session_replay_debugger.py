"""
Unit tests for Hydra Desktop Session Time-Travel Replay Debugger Subsystem.
Verifies step-by-step state playback, reverse stepping, conditional breakpoint evaluation,
local variable inspection, and multi-frame state differential audits.
Complies with AGENTS.md genome invariants: zero fake tests, real interfaces, exit code 0.
"""

from __future__ import annotations

import time
import pytest
from desktop.session_replay_debugger import (
    Breakpoint,
    BreakpointHit,
    BreakpointType,
    ReplayFrame,
    SessionReplayDebugger,
    get_replay_debugger,
    reset_replay_debugger,
)


def make_sample_frames():
    return [
        ReplayFrame(
            step_index=0,
            timestamp=1000.0,
            event_name="INIT",
            variables={"token_count": 0, "status": "idle"},
        ),
        ReplayFrame(
            step_index=1,
            timestamp=1001.0,
            event_name="PLAN",
            variables={"token_count": 12, "status": "planning", "agent": "architect"},
        ),
        ReplayFrame(
            step_index=2,
            timestamp=1002.0,
            event_name="EXECUTE",
            variables={"token_count": 45, "status": "executing", "agent": "coder"},
        ),
        ReplayFrame(
            step_index=3,
            timestamp=1003.0,
            event_name="AUDIT",
            variables={"token_count": 80, "status": "auditing", "agent": "auditor", "passed": True},
        ),
    ]


def test_frame_navigation_forward_and_backward():
    debugger = SessionReplayDebugger()
    frames = make_sample_frames()
    debugger.load_frames(frames)

    assert debugger.total_frames == 4
    assert debugger.current_step_index == 0
    assert debugger.current_frame().event_name == "INIT"

    # Step forward
    f1 = debugger.step_forward()
    assert f1 is not None
    assert f1.step_index == 1
    assert debugger.current_step_index == 1

    debugger.step_forward()  # at step 2
    f3 = debugger.step_forward()  # at step 3
    assert f3.step_index == 3

    # End of session
    assert debugger.step_forward() is None
    assert debugger.current_step_index == 3

    # Step backward
    f_back = debugger.step_backward()
    assert f_back is not None
    assert f_back.step_index == 2
    assert debugger.current_step_index == 2

    # Jump to step
    f_jump = debugger.jump_to_step(1)
    assert f_jump.step_index == 1
    assert debugger.current_step_index == 1


def test_breakpoint_evaluation_and_continue():
    debugger = SessionReplayDebugger()
    frames = make_sample_frames()
    debugger.load_frames(frames)

    # Set breakpoint on status == "executing"
    bp = debugger.set_breakpoint(
        btype=BreakpointType.VARIABLE_VALUE,
        target="status",
        expected_value="executing",
    )
    assert bp.hit_count == 0

    frame, hit = debugger.continue_execution()
    assert hit is not None
    assert hit.step_index == 2
    assert frame.step_index == 2
    assert bp.hit_count == 1
    assert debugger.current_step_index == 2

    # Remove breakpoint
    assert debugger.remove_breakpoint(bp.breakpoint_id) is True
    assert debugger.remove_breakpoint(bp.breakpoint_id) is False


def test_condition_fn_breakpoint():
    debugger = SessionReplayDebugger()
    frames = make_sample_frames()
    debugger.load_frames(frames)

    # Break when token_count > 50
    debugger.set_breakpoint(
        btype=BreakpointType.VARIABLE_CONDITION,
        target="token_threshold",
        condition_fn=lambda vars: vars.get("token_count", 0) > 50,
    )

    frame, hit = debugger.continue_execution()
    assert hit is not None
    assert hit.step_index == 3
    assert frame.variables["token_count"] == 80


def test_variable_inspection_and_history():
    debugger = SessionReplayDebugger()
    frames = make_sample_frames()
    debugger.load_frames(frames)

    # Inspect variables at step 0
    vars_0 = debugger.inspect_variables()
    assert vars_0["token_count"] == 0
    assert debugger.get_variable("token_count") == 0

    # History of token_count mutations
    history = debugger.get_variable_history("token_count")
    assert len(history) == 4
    assert history == [(0, 0), (1, 12), (2, 45), (3, 80)]


def test_diff_frames():
    debugger = SessionReplayDebugger()
    frames = make_sample_frames()
    debugger.load_frames(frames)

    diff = debugger.diff_frames(step_a=1, step_b=2)
    assert diff["step_a"] == 1
    assert diff["step_b"] == 2
    # Modified: agent (architect -> coder), token_count (12 -> 45), status (planning -> executing)
    assert diff["modified"]["agent"]["before"] == "architect"
    assert diff["modified"]["agent"]["after"] == "coder"

    # Step 1 to Step 3: passed was added
    diff_13 = debugger.diff_frames(step_a=1, step_b=3)
    assert "passed" in diff_13["added"]
    assert diff_13["added"]["passed"] is True


def test_singleton_debugger_and_reset():
    d1 = get_replay_debugger()
    d2 = get_replay_debugger()
    assert d1 is d2

    d3 = reset_replay_debugger()
    assert d3 is not d1
    assert get_replay_debugger() is d3
