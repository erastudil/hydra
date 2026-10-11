"""
Swarm session summary logger for Hydra CLI and Desktop.
Records multi-head dispatches, tool executions, specialist findings, and consensus roadmaps.
Emits human-readable Markdown writeups and structured JSON session archives.
Adheres to AGENTS.md genome invariants: zero copula P018, deterministic verification gates.
"""

from __future__ import annotations

import collections
import dataclasses
import datetime
import json
import logging
import os
import re
import threading
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple, Union

from hydra_cli.config import hydra_home

logger = logging.getLogger("hydra.session_logger")


@dataclasses.dataclass
class SwarmHeadDispatch:
    """Descriptor for specialist head dispatch initiation."""
    role: str
    title: str
    model: str
    provider: Optional[str] = None
    agent_id: str = ""
    dispatched_at: str = ""

    def to_dict(self) -> Dict[str, Any]:
        """Serialize dispatch descriptor into dictionary representation."""
        return dataclasses.asdict(self)


@dataclasses.dataclass
class SwarmToolExecution:
    """Record of discrete tool execution performed during head execution."""
    role: str
    tool_name: str
    arguments: Dict[str, Any]
    result_summary: str
    duration_ms: float
    status: str
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Serialize tool execution record into dictionary representation."""
        return dataclasses.asdict(self)


@dataclasses.dataclass
class SwarmHeadSummary:
    """Consolidated execution report for a single specialist or review head."""
    role: str
    title: str
    model: str
    content: str
    duration_sec: float
    served_model: Optional[str] = None
    provider: Optional[str] = None
    cost_usd: Optional[float] = None
    status: str = "ok"
    exit_code: int = 0
    error: Optional[str] = None
    tool_executions: List[SwarmToolExecution] = dataclasses.field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize head summary into dictionary representation."""
        data = dataclasses.asdict(self)
        data["tool_executions"] = [t.to_dict() if hasattr(t, "to_dict") else t for t in self.tool_executions]
        return data


@dataclasses.dataclass
class SwarmSessionSummary:
    """Full structured execution record of a multi-head swarm run."""
    session_id: str
    task: str
    swarm_name: str
    created_at: str
    completed_at: str
    total_duration_sec: float
    total_cost_usd: float
    exit_status: int
    heads: List[SwarmHeadSummary]
    synthesizer_output: Optional[str] = None
    consensus_verdict: str = "consensus reached"

    def to_dict(self) -> Dict[str, Any]:
        """Serialize session summary into dictionary representation."""
        data = dataclasses.asdict(self)
        data["heads"] = [h.to_dict() if hasattr(h, "to_dict") else h for h in self.heads]
        return data


class SwarmSessionLogger:
    """
    Session summary logger capturing swarm dispatches, tools, outputs, and exit status.
    Generates human-readable Markdown writeups and persists session records to disk.
    """

    def __init__(self, sessions_dir: Optional[str] = None) -> None:
        self._lock = threading.RLock()
        self._sessions_dir = os.path.abspath(sessions_dir or os.path.join(hydra_home(), "sessions"))
        self._active_sessions: Dict[str, Dict[str, Any]] = {}
        self._completed_sessions: Dict[str, SwarmSessionSummary] = {}
        self._ensure_sessions_dir()

    def _ensure_sessions_dir(self) -> None:
        """Ensure target directory exists for writing logs."""
        try:
            os.makedirs(self._sessions_dir, exist_ok=True)
        except OSError as exc:
            logger.warning(f"Could not create sessions directory: {exc}")

    @property
    def sessions_directory(self) -> str:
        """Return configured base directory for session persistence."""
        return self._sessions_dir

    def start_session(
        self,
        task: str,
        swarm_name: str = "swarm",
        session_id: Optional[str] = None,
    ) -> str:
        """Initialize tracking for a new swarm execution session."""
        sid = session_id or f"swarm_{uuid.uuid4().hex[:12]}"
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        with self._lock:
            self._active_sessions[sid] = {
                "session_id": sid,
                "task": task,
                "swarm_name": swarm_name,
                "created_at": now,
                "dispatches": [],
                "tool_executions": collections.defaultdict(list),
                "heads": {},
                "synthesizer_output": None,
                "start_time": time.time(),
            }
        return sid

    def record_dispatch(
        self,
        session_id: str,
        role: str,
        title: str,
        model: str,
        provider: Optional[str] = None,
        agent_id: Optional[str] = None,
    ) -> None:
        """Record dispatch event for a specialist head."""
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        dispatch = SwarmHeadDispatch(
            role=role,
            title=title,
            model=model,
            provider=provider,
            agent_id=agent_id or f"{role}_{uuid.uuid4().hex[:6]}",
            dispatched_at=now,
        )
        with self._lock:
            session = self._active_sessions.get(session_id)
            if session:
                session["dispatches"].append(dispatch)

    def record_tool_call(
        self,
        session_id: str,
        role: str,
        tool_name: str,
        arguments: Dict[str, Any],
        result: Any,
        duration_ms: float = 0.0,
        is_error: bool = False,
    ) -> None:
        """Record individual tool execution invoked by an active head."""
        summary_text = str(result)
        if len(summary_text) > 200:
            summary_text = summary_text[:197] + "..."
        execution = SwarmToolExecution(
            role=role,
            tool_name=tool_name,
            arguments=dict(arguments),
            result_summary=summary_text,
            duration_ms=round(duration_ms, 2),
            status="error" if is_error else "ok",
            error=str(result) if is_error else None,
        )
        with self._lock:
            session = self._active_sessions.get(session_id)
            if session:
                session["tool_executions"][role].append(execution)

    def record_head_completion(
        self,
        session_id: str,
        role: str,
        title: str,
        model: str,
        content: str,
        duration_sec: float,
        provider: Optional[str] = None,
        served_model: Optional[str] = None,
        cost: Optional[float] = None,
        error: Optional[str] = None,
        tool_executions: Optional[List[Dict[str, Any]]] = None,
    ) -> None:
        """Record completion results and output content for a head."""
        with self._lock:
            session = self._active_sessions.get(session_id)
            tools = []
            if session:
                tools = list(session["tool_executions"].get(role, []))
            if tool_executions:
                for t in tool_executions:
                    if isinstance(t, SwarmToolExecution):
                        tools.append(t)
                    elif isinstance(t, dict):
                        tools.append(
                            SwarmToolExecution(
                                role=role,
                                tool_name=t.get("tool_name", "unknown"),
                                arguments=t.get("arguments", {}),
                                result_summary=str(t.get("result", ""))[:200],
                                duration_ms=float(t.get("duration_ms", 0.0)),
                                status=t.get("status", "ok"),
                                error=t.get("error"),
                            )
                        )
            summary = SwarmHeadSummary(
                role=role,
                title=title,
                model=model,
                content=content,
                duration_sec=round(duration_sec, 2),
                served_model=served_model or model,
                provider=provider or "cheaperinference",
                cost_usd=cost,
                status="failed" if error else "ok",
                exit_code=1 if error else 0,
                error=error,
                tool_executions=tools,
            )
            if session:
                session["heads"][role] = summary

    def record_synthesis(
        self,
        session_id: str,
        synthesis_content: str,
        duration_sec: float,
        model: str = "synthesizer",
    ) -> None:
        """Record unified synthesis consensus output."""
        with self._lock:
            session = self._active_sessions.get(session_id)
            if session:
                session["synthesizer_output"] = synthesis_content
                self.record_head_completion(
                    session_id=session_id,
                    role="synthesizer",
                    title="Synthesizer",
                    model=model,
                    content=synthesis_content,
                    duration_sec=duration_sec,
                    provider="frontier",
                )

    def finalize_session(
        self,
        session_id: str,
        exit_status: Optional[int] = None,
    ) -> SwarmSessionSummary:
        """Finalize swarm execution tracking and generate session summary."""
        with self._lock:
            session = self._active_sessions.get(session_id)
            if not session:
                raise KeyError(f"Unknown session identifier: {session_id}")

            now = datetime.datetime.now(datetime.timezone.utc).isoformat()
            duration = time.time() - session["start_time"]
            heads_list = list(session["heads"].values())

            total_cost = sum(h.cost_usd for h in heads_list if h.cost_usd is not None)
            any_failure = any(h.exit_code != 0 for h in heads_list)

            calculated_status = 1 if any_failure else 0
            final_status = exit_status if exit_status is not None else calculated_status

            verdict = "consensus verified" if final_status == 0 else "degraded or failed execution"
            summary = SwarmSessionSummary(
                session_id=session_id,
                task=session["task"],
                swarm_name=session["swarm_name"],
                created_at=session["created_at"],
                completed_at=now,
                total_duration_sec=round(duration, 2),
                total_cost_usd=round(total_cost, 6),
                exit_status=final_status,
                heads=heads_list,
                synthesizer_output=session.get("synthesizer_output"),
                consensus_verdict=verdict,
            )
            self._completed_sessions[session_id] = summary
            return summary

    def format_readable_summary(self, session_id: str) -> str:
        """Generate formatted Markdown writeup for human inspection."""
        with self._lock:
            summary = self._completed_sessions.get(session_id)
            if not summary and session_id in self._active_sessions:
                summary = self.finalize_session(session_id)
            if not summary:
                raise KeyError(f"Session summary unavailable for: {session_id}")

        border = "=" * 72
        status_label = "PASSING (EXIT CODE 0)" if summary.exit_status == 0 else f"FAILED (EXIT CODE {summary.exit_status})"

        lines = [
            "# Hydra Swarm Session Summary",
            "",
            f"**Session ID**: `{summary.session_id}`",
            f"**Swarm Mode**: `{summary.swarm_name}`",
            f"**Created At**: `{summary.created_at}`",
            f"**Completed At**: `{summary.completed_at}`",
            f"**Execution Duration**: `{summary.total_duration_sec:.2f}s`",
            f"**Total Cost**: `${summary.total_cost_usd:.4f}`",
            f"**Verification Status**: **{status_label}**",
            "",
            "## Task Specification",
            f"> {summary.task}",
            "",
            "## Specialist Head Summary",
            "| Head / Role | Model | Provider | Duration | Cost | Status |",
            "| :--- | :--- | :--- | :--- | :--- | :--- |",
        ]

        for head in summary.heads:
            cost_str = f"${head.cost_usd:.4f}" if head.cost_usd is not None else "$0.0000"
            stat = "[PASS]" if head.exit_code == 0 else "[FAIL]"
            lines.append(
                f"| **{head.title}** ({head.role}) | `{head.model}` | {head.provider or 'cheaperinference'} | {head.duration_sec:.2f}s | {cost_str} | {stat} |"
            )

        # Tool executions section
        has_tools = any(h.tool_executions for h in summary.heads)
        if has_tools:
            lines.extend([
                "",
                "## Tool Executions and Machine Control",
            ])
            for head in summary.heads:
                if head.tool_executions:
                    lines.append(f"### {head.title} Actions")
                    for t in head.tool_executions:
                        arg_preview = json.dumps(t.arguments, default=str)
                        if len(arg_preview) > 60:
                            arg_preview = arg_preview[:57] + "..."
                        lines.append(
                            f"- `{t.tool_name}({arg_preview})` -> {t.result_summary} ({t.duration_ms:.1f}ms) [{t.status.upper()}]"
                        )

        # Head outputs section
        lines.extend([
            "",
            "## Specialist Findings",
        ])
        for head in summary.heads:
            lines.extend([
                f"### {head.title} ({head.role.upper()})",
                f"*Model: {head.model} via {head.provider}*",
                "",
                head.content if head.content else f"[Error: {head.error}]",
                "",
            ])

        # Synthesizer consensus
        if summary.synthesizer_output:
            lines.extend([
                "## Consensus Synthesis Roadmap",
                summary.synthesizer_output,
                "",
            ])

        # Final verification footer
        lines.extend([
            "## Verification State",
            f"exit code : {summary.exit_status}",
            f"consensus status : {summary.consensus_verdict}",
            "",
        ])

        return "\n".join(lines)

    def write_summary_to_disk(
        self,
        session_id: str,
        directory: Optional[str] = None,
    ) -> Tuple[str, str]:
        """Write Markdown writeup and JSON record to disk."""
        target_dir = os.path.abspath(directory or self._sessions_dir)
        os.makedirs(target_dir, exist_ok=True)

        with self._lock:
            summary = self._completed_sessions.get(session_id)
            if not summary and session_id in self._active_sessions:
                summary = self.finalize_session(session_id)
            if not summary:
                raise KeyError(f"Session record not found for: {session_id}")

        md_content = self.format_readable_summary(session_id)
        json_data = summary.to_dict()

        md_path = os.path.join(target_dir, f"{session_id}_summary.md")
        json_path = os.path.join(target_dir, f"{session_id}_summary.json")

        with open(md_path, "w", encoding="utf-8") as f:
            f.write(md_content)

        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(json_data, f, indent=2, ensure_ascii=False)
            f.write("\n")

        return md_path, json_path

    def load_session_summary(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Load stored JSON session summary from disk."""
        json_path = os.path.join(self._sessions_dir, f"{session_id}_summary.json")
        if not os.path.isfile(json_path):
            return None
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as exc:
            logger.warning(f"Could not read session summary {json_path}: {exc}")
            return None

    def list_all_session_summaries(self) -> List[Dict[str, Any]]:
        """List summary descriptors for all stored sessions."""
        summaries: List[Dict[str, Any]] = []
        if not os.path.isdir(self._sessions_dir):
            return summaries

        for name in sorted(os.listdir(self._sessions_dir)):
            if name.endswith("_summary.json"):
                fpath = os.path.join(self._sessions_dir, name)
                try:
                    with open(fpath, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        summaries.append({
                            "session_id": data.get("session_id"),
                            "task": data.get("task"),
                            "swarm_name": data.get("swarm_name"),
                            "created_at": data.get("created_at"),
                            "duration_sec": data.get("total_duration_sec"),
                            "exit_status": data.get("exit_status"),
                            "file_path": fpath,
                        })
                except Exception:
                    continue
        return summaries


_DEFAULT_SESSION_LOGGER: Optional[SwarmSessionLogger] = None
_DEFAULT_LOGGER_LOCK = threading.RLock()


def get_swarm_session_logger(sessions_dir: Optional[str] = None) -> SwarmSessionLogger:
    """Acquire global thread-safe SwarmSessionLogger instance."""
    global _DEFAULT_SESSION_LOGGER
    with _DEFAULT_LOGGER_LOCK:
        if _DEFAULT_SESSION_LOGGER is None:
            _DEFAULT_SESSION_LOGGER = SwarmSessionLogger(sessions_dir=sessions_dir)
        return _DEFAULT_SESSION_LOGGER


def reset_swarm_session_logger() -> SwarmSessionLogger:
    """Reset global SwarmSessionLogger instance."""
    global _DEFAULT_SESSION_LOGGER
    with _DEFAULT_LOGGER_LOCK:
        _DEFAULT_SESSION_LOGGER = SwarmSessionLogger()
        return _DEFAULT_SESSION_LOGGER
