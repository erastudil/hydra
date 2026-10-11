"""
Hydra Desktop Session Time-Travel Replay Debugger Subsystem.
Provides step-by-step state playback, reverse stepping, conditional breakpoint evaluation,
local variable inspection, and multi-frame state differential audits.
Complies with AGENTS.md genome invariants: zero fake tests, real interfaces, exit code 0.
"""

from __future__ import annotations

import copy
import threading
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Sequence, Set, Tuple, Union


class BreakpointType(str, Enum):
    """Supported debugger breakpoint trigger modes."""
    STEP_INDEX = "step_index"
    VARIABLE_VALUE = "variable_value"
    VARIABLE_CONDITION = "variable_condition"
    EVENT_TYPE = "event_type"


@dataclass
class Breakpoint:
    """Configured breakpoint evaluation predicate."""
    breakpoint_id: str
    btype: BreakpointType
    target: Any
    expected_value: Any = None
    condition_fn: Optional[Callable[[Dict[str, Any]], bool]] = None
    enabled: bool = True
    hit_count: int = 0

    def evaluate(self, step_idx: int, event_name: str, variables: Dict[str, Any]) -> bool:
        """Evaluate whether current execution frame satisfies breakpoint conditions."""
        if not self.enabled:
            return False

        if self.btype == BreakpointType.STEP_INDEX:
            return step_idx == self.target

        if self.btype == BreakpointType.EVENT_TYPE:
            return event_name == self.target

        if self.btype == BreakpointType.VARIABLE_VALUE:
            var_name = str(self.target)
            return var_name in variables and variables[var_name] == self.expected_value

        if self.btype == BreakpointType.VARIABLE_CONDITION and self.condition_fn is not None:
            try:
                return bool(self.condition_fn(variables))
            except Exception:
                return False

        return False

    def to_dict(self) -> Dict[str, Any]:
        """Serialize breakpoint metadata to dictionary representation."""
        return {
            "breakpoint_id": self.breakpoint_id,
            "btype": self.btype.value,
            "target": str(self.target),
            "expected_value": self.expected_value,
            "enabled": self.enabled,
            "hit_count": self.hit_count,
        }


@dataclass
class ReplayFrame:
    """Point-in-time session execution snapshot frame."""
    step_index: int
    timestamp: float
    event_name: str
    variables: Dict[str, Any] = field(default_factory=dict)
    action_type: str = ""
    action_payload: Dict[str, Any] = field(default_factory=dict)
    output: Any = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize snapshot frame to dictionary."""
        return {
            "step_index": self.step_index,
            "timestamp": self.timestamp,
            "event_name": self.event_name,
            "variables": copy.deepcopy(self.variables),
            "action_type": self.action_type,
            "action_payload": dict(self.action_payload),
            "output": self.output,
            "metadata": dict(self.metadata),
        }


@dataclass
class BreakpointHit:
    """Record of execution halt caused by satisfied breakpoint."""
    breakpoint: Breakpoint
    step_index: int
    frame: ReplayFrame
    reason: str

    def to_dict(self) -> Dict[str, Any]:
        """Serialize hit record to dictionary."""
        return {
            "breakpoint_id": self.breakpoint.breakpoint_id,
            "step_index": self.step_index,
            "reason": self.reason,
            "frame": self.frame.to_dict(),
        }


class SessionReplayDebugger:
    """
    Session time-travel replay debugger engine.
    Maintains ordered sequence of historical session execution frames,
    executes forward stepping, reverse stepping, jump-to-step navigations,
    breakpoint trip evaluations, and variable history inspections.
    """

    def __init__(self) -> None:
        self._frames: List[ReplayFrame] = []
        self._cursor: int = -1
        self._breakpoints: Dict[str, Breakpoint] = {}
        self._lock = threading.RLock()

    @property
    def total_frames(self) -> int:
        """Total count of loaded historical frames."""
        with self._lock:
            return len(self._frames)

    @property
    def current_step_index(self) -> int:
        """Step index of active cursor position."""
        with self._lock:
            if 0 <= self._cursor < len(self._frames):
                return self._frames[self._cursor].step_index
            return -1

    def load_frames(self, frames: Sequence[ReplayFrame]) -> None:
        """Load and index sequence of session execution frames."""
        with self._lock:
            self._frames = list(frames)
            self._cursor = 0 if self._frames else -1

    def current_frame(self) -> Optional[ReplayFrame]:
        """Retrieve snapshot frame at current cursor position."""
        with self._lock:
            if 0 <= self._cursor < len(self._frames):
                return self._frames[self._cursor]
            return None

    def step_forward(self) -> Optional[ReplayFrame]:
        """Advance cursor forward by one execution frame."""
        with self._lock:
            if self._cursor + 1 < len(self._frames):
                self._cursor += 1
                return self._frames[self._cursor]
            return None

    def step_backward(self) -> Optional[ReplayFrame]:
        """Rewind cursor backward by one execution frame."""
        with self._lock:
            if self._cursor > 0:
                self._cursor -= 1
                return self._frames[self._cursor]
            return None

    def jump_to_step(self, step_index: int) -> ReplayFrame:
        """Navigate cursor directly to specified step index."""
        with self._lock:
            for idx, frame in enumerate(self._frames):
                if frame.step_index == step_index:
                    self._cursor = idx
                    return frame
            raise IndexError(f"Step index {step_index} not found in loaded replay frames")

    def continue_execution(self) -> Tuple[Optional[ReplayFrame], Optional[BreakpointHit]]:
        """
        Execute execution playback forward until breakpoint matches or final frame reached.
        """
        with self._lock:
            while self._cursor + 1 < len(self._frames):
                self._cursor += 1
                curr = self._frames[self._cursor]

                # Check all active breakpoints
                for bp in self._breakpoints.values():
                    if bp.evaluate(curr.step_index, curr.event_name, curr.variables):
                        bp.hit_count += 1
                        hit = BreakpointHit(
                            breakpoint=bp,
                            step_index=curr.step_index,
                            frame=curr,
                            reason=f"Hit breakpoint {bp.breakpoint_id} ({bp.btype.value})",
                        )
                        return curr, hit

            return self.current_frame(), None

    def run_to_completion(self) -> Optional[ReplayFrame]:
        """Fast-forward cursor to final frame in recorded session."""
        with self._lock:
            if self._frames:
                self._cursor = len(self._frames) - 1
                return self._frames[self._cursor]
            return None

    def set_breakpoint(
        self,
        btype: BreakpointType,
        target: Any,
        expected_value: Any = None,
        condition_fn: Optional[Callable[[Dict[str, Any]], bool]] = None,
    ) -> Breakpoint:
        """Register new execution breakpoint."""
        with self._lock:
            bid = f"bp_{uuid.uuid4().hex[:8]}"
            bp = Breakpoint(
                breakpoint_id=bid,
                btype=btype,
                target=target,
                expected_value=expected_value,
                condition_fn=condition_fn,
            )
            self._breakpoints[bid] = bp
            return bp

    def remove_breakpoint(self, breakpoint_id: str) -> bool:
        """Remove breakpoint by identifier."""
        with self._lock:
            return self._breakpoints.pop(breakpoint_id, None) is not None

    def list_breakpoints(self) -> List[Dict[str, Any]]:
        """List metadata for all active breakpoints."""
        with self._lock:
            return [b.to_dict() for b in self._breakpoints.values()]

    def inspect_variables(self) -> Dict[str, Any]:
        """Retrieve state variables at current execution position."""
        with self._lock:
            frame = self.current_frame()
            return dict(frame.variables) if frame else {}

    def get_variable(self, var_name: str) -> Any:
        """Retrieve single variable value at current execution position."""
        with self._lock:
            vars_dict = self.inspect_variables()
            return vars_dict.get(var_name)

    def get_variable_history(self, var_name: str) -> List[Tuple[int, Any]]:
        """Trace chronological value mutation history for named variable."""
        with self._lock:
            history: List[Tuple[int, Any]] = []
            for frame in self._frames:
                if var_name in frame.variables:
                    history.append((frame.step_index, frame.variables[var_name]))
            return history

    def diff_frames(self, step_a: int, step_b: int) -> Dict[str, Any]:
        """Compute state variable differential between two frames."""
        with self._lock:
            frame_a = next((f for f in self._frames if f.step_index == step_a), None)
            frame_b = next((f for f in self._frames if f.step_index == step_b), None)

            if not frame_a or not frame_b:
                raise ValueError("Target frames for diff not found in session")

            vars_a = frame_a.variables
            vars_b = frame_b.variables

            keys_a = set(vars_a.keys())
            keys_b = set(vars_b.keys())

            added = {k: vars_b[k] for k in (keys_b - keys_a)}
            removed = {k: vars_a[k] for k in (keys_a - keys_b)}
            modified = {
                k: {"before": vars_a[k], "after": vars_b[k]}
                for k in (keys_a & keys_b)
                if vars_a[k] != vars_b[k]
            }

            return {
                "step_a": step_a,
                "step_b": step_b,
                "added": added,
                "removed": removed,
                "modified": modified,
            }

    def reset(self) -> None:
        """Clear loaded session frames and active breakpoints."""
        with self._lock:
            self._frames.clear()
            self._cursor = -1
            self._breakpoints.clear()


_GLOBAL_REPLAY_DEBUGGER: Optional[SessionReplayDebugger] = None
_GLOBAL_DEBUGGER_LOCK = threading.RLock()


def get_replay_debugger() -> SessionReplayDebugger:
    """Acquire thread-safe singleton SessionReplayDebugger."""
    global _GLOBAL_REPLAY_DEBUGGER
    with _GLOBAL_DEBUGGER_LOCK:
        if _GLOBAL_REPLAY_DEBUGGER is None:
            _GLOBAL_REPLAY_DEBUGGER = SessionReplayDebugger()
        return _GLOBAL_REPLAY_DEBUGGER


def reset_replay_debugger() -> SessionReplayDebugger:
    """Reset singleton SessionReplayDebugger."""
    global _GLOBAL_REPLAY_DEBUGGER
    with _GLOBAL_DEBUGGER_LOCK:
        _GLOBAL_REPLAY_DEBUGGER = SessionReplayDebugger()
        return _GLOBAL_REPLAY_DEBUGGER
