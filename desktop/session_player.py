"""
Hydra Desktop Session Playback Engine and Action Tracer.
Provides interactive step-by-step trace replay, timeline analytics,
standalone HTML playback visualizer, and live action tracing.
Complies with AGENTS.md genome invariants: zero copula P018, zero stubs, deterministic verification.
"""

from __future__ import annotations

import collections
import datetime
import html
import json
import logging
import os
import time
import uuid
from typing import Any, Callable, Dict, List, Optional, Tuple, Sequence, Union

from hydra_cli.agent_runner import (
    AgentState,
    validate_session_trace,
    AutonomousAgentRunner,
    TOOL_PROFILES,
)

logger = logging.getLogger("hydra.desktop.session_player")


class SessionTracePlayer:
    """
    Deterministic step-by-step playback engine for Hydra agent session traces.
    Enables forward/backward scrubbing, snapshot inspection, state reconstruction,
    and timeline telemetry analysis.
    """

    def __init__(self, trace: Optional[Dict[str, Any]] = None):
        self._trace: Dict[str, Any] = {}
        self._history: List[Dict[str, Any]] = []
        self._snapshots: List[Dict[str, Any]] = []
        self._cursor: int = 0
        self._is_playing: bool = False
        self._playback_speed: float = 1.0

        if trace is not None:
            self.load_trace(trace)

    @property
    def trace(self) -> Dict[str, Any]:
        return self._trace

    @property
    def history(self) -> List[Dict[str, Any]]:
        return self._history

    @property
    def cursor(self) -> int:
        return self._cursor

    @property
    def total_steps(self) -> int:
        return len(self._history)

    @property
    def is_finished(self) -> bool:
        return self._cursor >= len(self._history)

    @property
    def is_at_start(self) -> bool:
        return self._cursor == 0

    def load_trace(self, trace: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
        """Validate and load session trace into playback buffer."""
        is_valid, err = validate_session_trace(trace)
        if not is_valid:
            raise ValueError(f"Invalid session trace: {err}")

        self._trace = dict(trace)
        self._history = list(trace.get("history", []))
        self._snapshots = list(trace.get("snapshots", []))
        self._cursor = 0
        self._is_playing = False
        return True, None

    def load_file(self, file_path: str) -> Tuple[bool, Optional[str]]:
        """Load session trace from JSON or JSONL file on disk."""
        if not os.path.isfile(file_path):
            raise FileNotFoundError(f"Session trace file not found: {file_path}")

        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read().strip()

        if not content:
            raise ValueError(f"Trace file is empty: {file_path}")

        try:
            data = json.loads(content)
        except json.JSONDecodeError:
            # Attempt parsing multi-line JSONL format by aggregating history lines
            lines = content.splitlines()
            records = []
            for line in lines:
                if line.strip():
                    records.append(json.loads(line.strip()))
            data = {
                "schema_version": "1.0.0",
                "session_id": f"trace-jsonl-{int(time.time())}",
                "task": "Aggregated JSONL trace",
                "state": AgentState.COMPLETED,
                "step_count": len(records),
                "history": records,
                "duration_ms": 0.0,
            }

        return self.load_trace(data)

    def reset(self) -> None:
        """Reset playback position to initial state (step 0)."""
        self._cursor = 0
        self._is_playing = False

    def seek(self, step_index: int) -> Optional[Dict[str, Any]]:
        """Seek directly to designated step index with boundary clamping."""
        if not self._history:
            self._cursor = 0
            return None

        clamped = max(0, min(step_index, len(self._history)))
        self._cursor = clamped
        return self.get_current_step()

    def next_step(self) -> Optional[Dict[str, Any]]:
        """Advance playback cursor by one step and return step payload."""
        if self._cursor >= len(self._history):
            return None
        step_payload = self._history[self._cursor]
        self._cursor += 1
        return step_payload

    def prev_step(self) -> Optional[Dict[str, Any]]:
        """Retract playback cursor by one step and return step payload."""
        if self._cursor <= 0:
            return None
        self._cursor -= 1
        return self._history[self._cursor]

    def get_current_step(self) -> Optional[Dict[str, Any]]:
        """Retrieve current step record at cursor position without mutating cursor."""
        if not self._history:
            return None
        idx = max(0, min(self._cursor, len(self._history) - 1))
        return self._history[idx]

    def get_progress(self) -> Dict[str, Any]:
        """Compute structured progress indicators for UI display."""
        total = len(self._history)
        percent = 100.0 if total == 0 else round((self._cursor / total) * 100.0, 1)
        return {
            "cursor": self._cursor,
            "total_steps": total,
            "percent": percent,
            "is_finished": self.is_finished,
            "is_at_start": self.is_at_start,
            "session_id": self._trace.get("session_id", "unknown"),
            "task": self._trace.get("task", ""),
            "state": self._trace.get("state", AgentState.IDLE),
        }

    def play_all(
        self,
        delay_sec: float = 0.0,
        step_callback: Optional[Callable[[int, Dict[str, Any]], None]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Execute deterministic automated playback through all remaining steps.
        Optional callback invokes per-step subscriber with index and payload.
        """
        played_steps = []
        self._is_playing = True
        try:
            while not self.is_finished:
                idx = self._cursor
                step = self.next_step()
                if step is not None:
                    played_steps.append(step)
                    if step_callback:
                        step_callback(idx, step)
                    if delay_sec > 0:
                        time.sleep(delay_sec / max(0.1, self._playback_speed))
        finally:
            self._is_playing = False
        return played_steps

    def export_summary(self) -> Dict[str, Any]:
        """Compile comprehensive timeline metrics and operational statistics."""
        action_counts: Dict[str, int] = collections.defaultdict(int)
        category_counts: Dict[str, int] = collections.defaultdict(int)
        errors_count = 0
        timeline = []

        for idx, entry in enumerate(self._history):
            action_name = entry.get("action", entry.get("type", "unknown"))
            action_counts[action_name] += 1

            # Categorize action
            if action_name.startswith("browser_"):
                category_counts["browser"] += 1
            elif action_name.startswith("mouse_") or action_name.startswith("key_") or action_name in ("type_text", "hotkey", "screen_capture"):
                category_counts["os"] += 1
            elif action_name in ("read_file", "write_file", "edit_file", "list_dir", "grep_search", "run_command"):
                category_counts["workspace"] += 1
            else:
                category_counts["generic"] += 1

            res = entry.get("result")
            is_err = False
            if isinstance(res, dict) and res.get("isError"):
                is_err = True
                errors_count += 1
            elif entry.get("status") == "failed" or entry.get("error"):
                is_err = True
                errors_count += 1

            timeline.append({
                "step_index": idx + 1,
                "action": action_name,
                "status": "error" if is_err else "success",
                "timestamp": entry.get("timestamp"),
                "duration_ms": entry.get("duration_ms", 0.0),
            })

        return {
            "session_id": self._trace.get("session_id", ""),
            "task": self._trace.get("task", ""),
            "final_state": self._trace.get("state", ""),
            "tool_profile": self._trace.get("tool_profile", "full"),
            "total_steps": len(self._history),
            "error_count": errors_count,
            "success_rate": round(100.0 * (len(self._history) - errors_count) / max(1, len(self._history)), 1),
            "duration_ms": self._trace.get("duration_ms", 0.0),
            "action_distribution": dict(action_counts),
            "category_distribution": dict(category_counts),
            "timeline": timeline,
        }

    def render_markdown_report(self) -> str:
        """Render complete Markdown audit summary report from loaded trace."""
        summary = self.export_summary()
        lines = [
            f"# Session Replay Audit Report: {summary['session_id']}",
            "",
            f"- **Task**: {summary['task']}",
            f"- **Final State**: `{summary['final_state']}`",
            f"- **Total Steps**: {summary['total_steps']}",
            f"- **Success Rate**: {summary['success_rate']}% ({summary['total_steps'] - summary['error_count']} success, {summary['error_count']} error)",
            f"- **Total Duration**: {summary['duration_ms']} ms",
            f"- **Tool Profile**: `{summary['tool_profile']}`",
            "",
            "## Action Distribution",
            "",
            "| Action | Category | Count |",
            "| :--- | :--- | :--- |",
        ]

        for act, cnt in sorted(summary["action_distribution"].items(), key=lambda x: x[1], reverse=True):
            cat = "browser" if act.startswith("browser_") else "os" if act.startswith(("mouse_", "key_")) or act in ("type_text", "hotkey", "screen_capture") else "workspace"
            lines.append(f"| `{act}` | {cat} | {cnt} |")

        lines.extend([
            "",
            "## Execution Timeline",
            "",
            "| Step | Action | Status | Duration (ms) |",
            "| :--- | :--- | :--- | :--- |",
        ])

        for t in summary["timeline"]:
            status_badge = "PASS" if t["status"] == "success" else "FAIL"
            lines.append(f"| {t['step_index']} | `{t['action']}` | {status_badge} | {t['duration_ms']} |")

        return "\n".join(lines) + "\n"

    def render_html_player(self) -> str:
        """Render self-contained HTML5 session player interface."""
        summary = self.export_summary()
        escaped_json = html.escape(json.dumps(self._trace))
        task_title = html.escape(summary["task"] or "Hydra Execution Replay")
        session_id = html.escape(summary["session_id"] or "unspecified")

        return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Hydra Session Player: {session_id}</title>
  <style>
    :root {{
      --bg: #090d16;
      --card-bg: #111827;
      --border: #1f2937;
      --accent: #3b82f6;
      --text: #f3f4f6;
      --text-muted: #9ca3af;
      --success: #10b981;
      --error: #ef4444;
    }}
    * {{ box-sizing: border-box; margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, monospace; }}
    body {{ background: var(--bg); color: var(--text); padding: 24px; }}
    .header {{ margin-bottom: 24px; padding-bottom: 16px; border-bottom: 1px solid var(--border); }}
    .header h1 {{ font-size: 20px; font-weight: 600; color: #fff; }}
    .header .meta {{ font-size: 13px; color: var(--text-muted); margin-top: 6px; }}
    .player-grid {{ display: grid; grid-template-columns: 320px 1fr; gap: 20px; }}
    .timeline-card {{ background: var(--card-bg); border: 1px solid var(--border); border-radius: 8px; padding: 16px; height: 680px; overflow-y: auto; }}
    .timeline-item {{ padding: 10px 12px; margin-bottom: 8px; border-radius: 6px; border: 1px solid var(--border); cursor: pointer; transition: all 0.15s ease; font-size: 13px; }}
    .timeline-item:hover {{ border-color: var(--accent); }}
    .timeline-item.active {{ background: #1e3a8a; border-color: var(--accent); color: #fff; }}
    .badge {{ display: inline-block; padding: 2px 6px; border-radius: 4px; font-size: 11px; text-transform: uppercase; }}
    .badge-success {{ background: rgba(16, 185, 129, 0.2); color: var(--success); }}
    .badge-error {{ background: rgba(239, 68, 68, 0.2); color: var(--error); }}
    .inspector-card {{ background: var(--card-bg); border: 1px solid var(--border); border-radius: 8px; padding: 20px; display: flex; flex-direction: column; gap: 16px; }}
    .controls {{ display: flex; align-items: center; gap: 12px; padding-bottom: 16px; border-bottom: 1px solid var(--border); }}
    .btn {{ background: #1f2937; border: 1px solid #374151; color: #fff; padding: 8px 16px; border-radius: 6px; cursor: pointer; font-size: 13px; }}
    .btn:hover {{ background: #374151; }}
    .btn-primary {{ background: var(--accent); border-color: var(--accent); }}
    .btn-primary:hover {{ background: #2563eb; }}
    .scrubber {{ flex-grow: 1; }}
    .detail-view {{ background: #0b0f19; border: 1px solid var(--border); border-radius: 6px; padding: 16px; flex-grow: 1; overflow: auto; font-family: monospace; font-size: 12px; line-height: 1.5; white-space: pre-wrap; }}
  </style>
</head>
<body>
  <div class="header">
    <h1>Session Replay: {task_title}</h1>
    <div class="meta">Session: {session_id} | State: {summary['final_state']} | Steps: {summary['total_steps']} | Success: {summary['success_rate']}%</div>
  </div>
  <div class="player-grid">
    <div class="timeline-card" id="timeline-list"></div>
    <div class="inspector-card">
      <div class="controls">
        <button class="btn" id="btn-prev">&larr; Prev</button>
        <button class="btn btn-primary" id="btn-play">Play</button>
        <button class="btn" id="btn-next">Next &rarr;</button>
        <button class="btn" id="btn-reset">Reset</button>
        <input type="range" class="scrubber" id="scrubber" min="0" max="{max(0, summary['total_steps'] - 1)}" value="0">
        <span id="step-indicator" style="font-size: 13px; min-width: 60px;">0 / {summary['total_steps']}</span>
      </div>
      <div class="detail-view" id="step-detail">Select a step to inspect payload and execution telemetry.</div>
    </div>
  </div>
  <script>
    const traceData = JSON.parse(document.getElementById('raw-trace').textContent);
    const history = traceData.history || [];
    let currentIndex = 0;
    let isPlaying = false;
    let playTimer = null;

    const timelineEl = document.getElementById('timeline-list');
    const detailEl = document.getElementById('step-detail');
    const scrubberEl = document.getElementById('scrubber');
    const indicatorEl = document.getElementById('step-indicator');
    const btnPlay = document.getElementById('btn-play');

    function renderTimeline() {{
      timelineEl.innerHTML = '';
      history.forEach((step, idx) => {{
        const div = document.createElement('div');
        div.className = `timeline-item ${{idx === currentIndex ? 'active' : ''}}`;
        const action = step.action || step.type || 'unknown';
        const isError = (step.result && step.result.isError) || step.error;
        div.innerHTML = `<div style="display:flex; justify-content:space-between; align-items:center;">
          <span><strong>#${{idx + 1}}</strong> ${{action}}</span>
          <span class="badge ${{isError ? 'badge-error' : 'badge-success'}}">${{isError ? 'FAIL' : 'OK'}}</span>
        </div>`;
        div.onclick = () => selectStep(idx);
        timelineEl.appendChild(div);
      }});
    }}

    function selectStep(idx) {{
      if (idx < 0 || idx >= history.length) return;
      currentIndex = idx;
      scrubberEl.value = idx;
      indicatorEl.textContent = `${{idx + 1}} / ${{history.length}}`;
      renderTimeline();
      detailEl.textContent = JSON.stringify(history[idx], null, 2);
    }}

    document.getElementById('btn-next').onclick = () => {{
      if (currentIndex < history.length - 1) selectStep(currentIndex + 1);
    }};
    document.getElementById('btn-prev').onclick = () => {{
      if (currentIndex > 0) selectStep(currentIndex - 1);
    }};
    document.getElementById('btn-reset').onclick = () => selectStep(0);
    scrubberEl.oninput = (e) => selectStep(parseInt(e.target.value, 10));

    btnPlay.onclick = () => {{
      if (isPlaying) {{
        clearInterval(playTimer);
        isPlaying = false;
        btnPlay.textContent = 'Play';
      }} else {{
        isPlaying = true;
        btnPlay.textContent = 'Pause';
        playTimer = setInterval(() => {{
          if (currentIndex < history.length - 1) {{
            selectStep(currentIndex + 1);
          }} else {{
            clearInterval(playTimer);
            isPlaying = false;
            btnPlay.textContent = 'Play';
          }}
        }}, 600);
      }}
    }};

    renderTimeline();
    if (history.length > 0) selectStep(0);
  </script>
  <script type="application/json" id="raw-trace">{escaped_json}</script>
</body>
</html>
"""


class ActionTracer:
    """
    Live real-time action tracer and session recorder.
    Intercepts and logs actions, parameters, outputs, latencies, and snapshots
    into a spec-compliant v1.0.0 session trace artifact.
    """

    def __init__(self, task: str = "", tool_profile: str = "full", max_steps: int = 25):
        self.session_id: str = f"trace-{int(time.time())}-{uuid.uuid4().hex[:6]}"
        self.task: str = task
        self.tool_profile: str = tool_profile
        self.max_steps: int = max_steps
        self.state: str = AgentState.IDLE
        self.history: List[Dict[str, Any]] = []
        self.snapshots: List[Dict[str, Any]] = []
        self.start_time: float = time.time()
        self.stop_time: Optional[float] = None
        self._is_active: bool = True

    def record_step(
        self,
        action: str,
        params: Optional[Dict[str, Any]] = None,
        result: Any = None,
        snapshot: Optional[str] = None,
        duration_ms: float = 0.0,
    ) -> Dict[str, Any]:
        """Record discrete operational step into active session ledger."""
        step_index = len(self.history) + 1
        entry = {
            "step": step_index,
            "action": action,
            "params": params or {},
            "result": result,
            "duration_ms": round(duration_ms, 2),
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }
        self.history.append(entry)
        if snapshot:
            self.snapshots.append({"step": step_index, "snapshot": snapshot})
        return entry

    def finish(self, final_state: str = AgentState.COMPLETED) -> Dict[str, Any]:
        """Mark session trace complete and compute execution boundaries."""
        self.state = final_state
        self.stop_time = time.time()
        self._is_active = False
        return self.export_trace()

    def export_trace(self) -> Dict[str, Any]:
        """Produce schema-compliant v1.0.0 session trace dictionary."""
        end_t = self.stop_time or time.time()
        duration_ms = round((end_t - self.start_time) * 1000.0, 2)

        trace = {
            "schema_version": "1.0.0",
            "session_id": self.session_id,
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "task": self.task,
            "state": self.state,
            "tool_profile": self.tool_profile,
            "step_count": len(self.history),
            "max_steps": self.max_steps,
            "duration_ms": duration_ms,
            "start_time": self.start_time,
            "stop_time": self.stop_time,
            "is_aborted": (self.state == AgentState.ABORTED),
            "history": list(self.history),
            "snapshots": list(self.snapshots),
        }
        return trace

    def save_trace_file(self, target_path: str) -> str:
        """Write session trace payload to designated file path on disk."""
        target = os.path.abspath(target_path)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        trace_data = self.export_trace()
        with open(target, "w", encoding="utf-8") as f:
            json.dump(trace_data, f, indent=2)
        return target


def create_player_from_runner(runner: AutonomousAgentRunner) -> SessionTracePlayer:
    """Instantiate a SessionTracePlayer directly from an active or finished agent runner."""
    trace = runner.export_session_trace()
    return SessionTracePlayer(trace)
