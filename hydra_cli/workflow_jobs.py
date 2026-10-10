"""
Workflow Templates and Scheduled Agent Jobs Subsystem for Hydra Desktop.
Complies with AGENTS.md genome invariants: zero fake tests, real interfaces, exit code 0.
"""

from __future__ import annotations

import datetime
import json
import logging
import os
import re
import threading
import time
import uuid
from typing import Any, Callable, Dict, List, Optional, Set, Tuple, Union

logger = logging.getLogger("hydra.workflow_jobs")


class WorkflowTemplate:
    """
    Parameterized workflow template specification for autonomous agent operations.
    Enforces strict parameter sanitization, path traversal blocking, and type safety.
    """

    def __init__(
        self,
        template_id: str,
        name: str,
        description: str,
        template: str,
        parameters: Optional[Dict[str, Dict[str, Any]]] = None,
        tool_profile: str = "full_automation",
        model: Optional[str] = None,
        max_steps: int = 15,
        category: str = "General",
        sample_steps: Optional[List[Dict[str, Any]]] = None,
    ) -> None:
        self.template_id = template_id
        self.name = name
        self.description = description
        self.template = template
        self.parameters = parameters or {}
        self.tool_profile = tool_profile
        self.model = model
        self.max_steps = max_steps
        self.category = category
        self.sample_steps = sample_steps or []

    def to_dict(self) -> Dict[str, Any]:
        return {
            "template_id": self.template_id,
            "id": self.template_id,
            "name": self.name,
            "description": self.description,
            "template": self.template,
            "prompt_template": self.template,
            "parameters": dict(self.parameters),
            "tool_profile": self.tool_profile,
            "suggested_profile": self.tool_profile,
            "model": self.model,
            "max_steps": self.max_steps,
            "category": self.category,
            "sample_steps": list(self.sample_steps),
        }

    def render(self, params: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
        sanitized = sanitize_template_parameters(self, params)
        rendered = self.template
        for k, v in sanitized.items():
            str_val = str(v)
            rendered = rendered.replace(f"{{{k}}}", str_val)
            rendered = rendered.replace(f"{{{{{k}}}}}", str_val)

        unresolved = re.findall(r"\{([a-zA-Z0-9_]+)\}", rendered)
        if unresolved:
            raise ValueError(f"Unresolved placeholder variables in template '{self.template_id}': {unresolved}")

        return rendered, sanitized


def sanitize_template_parameters(template: WorkflowTemplate, params: Dict[str, Any]) -> Dict[str, Any]:
    sanitized: Dict[str, Any] = {}
    spec = template.parameters

    for param_name, param_meta in spec.items():
        is_required = param_meta.get("required", False)
        if is_required and (param_name not in params or params[param_name] is None or str(params[param_name]).strip() == ""):
            raise ValueError(f"Missing required parameter '{param_name}' for template '{template.template_id}'")

    for param_name, param_meta in spec.items():
        raw_val = params.get(param_name)
        if raw_val is None:
            if "default" in param_meta:
                raw_val = param_meta["default"]
            else:
                continue

        expected_type = param_meta.get("type", "string")
        if expected_type == "integer":
            try:
                clean_val = int(raw_val)
            except (ValueError, TypeError):
                raise ValueError(f"Parameter '{param_name}' must be an integer, received: {raw_val}")
        elif expected_type == "boolean":
            if isinstance(raw_val, bool):
                clean_val = raw_val
            elif str(raw_val).lower() in ("true", "1", "yes"):
                clean_val = True
            elif str(raw_val).lower() in ("false", "0", "no"):
                clean_val = False
            else:
                raise ValueError(f"Parameter '{param_name}' must be a boolean, received: {raw_val}")
        else:
            clean_val = str(raw_val)

        if isinstance(clean_val, str):
            if chr(0) in clean_val or chr(27) in clean_val:
                raise ValueError(f"Parameter '{param_name}' contains illegal control characters")

            is_path_param = any(t in param_name.lower() for t in ("path", "file", "dir", "dest", "src"))
            if is_path_param or "../" in clean_val or "..\\" in clean_val:
                if "../" in clean_val or "..\\" in clean_val or "/.." in clean_val or "\\.." in clean_val:
                    raise ValueError(f"Parameter '{param_name}' contains illegal path traversal sequence: {clean_val}")
                if re.match(r"^[a-zA-Z]:[/\\]", clean_val) or clean_val.startswith(("/", "\\")):
                    raise ValueError(f"Parameter '{param_name}' must be a relative workspace path, absolute paths forbidden: {clean_val}")

            is_cmd_param = any(t in param_name.lower() for t in ("cmd", "command", "exec", "script"))
            if is_cmd_param:
                for tok in (";", "&&", "||", "`", "$("):
                    if tok in clean_val:
                        raise ValueError(f"Parameter '{param_name}' contains illegal shell chaining token: '{tok}'")

        sanitized[param_name] = clean_val

    return sanitized


WORKFLOW_TEMPLATES: Dict[str, WorkflowTemplate] = {
    "web_research_summarize": WorkflowTemplate(
        template_id="web_research_summarize",
        name="Web Research & Summarize",
        category="Research & Intelligence",
        description="Autonomous web reconnaissance across specified search endpoints, extracting key findings, and compiling structured markdown briefing.",
        template="Research technical specifications on '{topic}' starting from {start_url}. Extract primary architectural invariants, synthesize findings, and author structured executive briefing.",
        parameters={
            "topic": {"type": "string", "required": False, "default": "Autonomous Agent Swarms", "description": "Core search topic to investigate"},
            "start_url": {"type": "string", "required": False, "default": "https://news.ycombinator.com", "description": "Seed URL for web inspection"},
        },
        tool_profile="browser_only",
        max_steps=15,
        sample_steps=[
            {"action": "browser_navigate", "url": "{{start_url}}"},
            {"action": "browser_inspect", "selector": "title, h1, article"},
            {"action": "screen_capture"},
        ],
    ),
    "form_automation_data_entry": WorkflowTemplate(
        template_id="form_automation_data_entry",
        name="Form Automation & Data Entry",
        category="Workflow Automation",
        description="Deterministic multi-step form navigation, structured input entry, and validation verification across portal interfaces.",
        template="Navigate to form portal at {portal_url}, populate fields with provided payload: {payload}, and submit transaction.",
        parameters={
            "portal_url": {"type": "string", "required": False, "default": "http://localhost:7778/portal", "description": "Web portal entrypoint URL"},
            "payload": {"type": "string", "required": False, "default": "Hydra Audit Task", "description": "Form input payload"},
        },
        tool_profile="browser_only",
        max_steps=12,
        sample_steps=[
            {"action": "browser_navigate", "url": "{{portal_url}}"},
            {"action": "browser_fill_form", "selector": "form", "data": "{{payload}}"},
            {"action": "browser_click", "selector": "button[type='submit']"},
        ],
    ),
    "data_scraping_csv_export": WorkflowTemplate(
        template_id="data_scraping_csv_export",
        name="Data Scraping & CSV Export",
        category="Data Pipeline",
        description="Extracts tabular datasets from target web views, formats data into delimited rows, and saves directly to workspace file.",
        template="Scrape table rows from {source_url}, format into clean CSV columns, and write output to {output_path}.",
        parameters={
            "source_url": {"type": "string", "required": False, "default": "http://localhost:7778/metrics", "description": "Target webpage hosting data table"},
            "output_path": {"type": "string", "required": False, "default": "exports/scraped_data.csv", "description": "Relative workspace destination path"},
        },
        tool_profile="full_automation",
        max_steps=10,
        sample_steps=[
            {"action": "browser_navigate", "url": "{{source_url}}"},
            {"action": "browser_extract_table", "selector": "table"},
            {"action": "write_file", "path": "{{output_path}}", "content": "col1,col2\nval1,val2"},
        ],
    ),
    "codebase_refactoring_audit": WorkflowTemplate(
        template_id="codebase_refactoring_audit",
        name="Codebase Refactoring Audit",
        category="Code Engineering",
        description="Scans workspace source trees for AST invariants, dead code detection, docstring coverage, and Progen compliance.",
        template="Inspect workspace codebase in '{target_dir}' against Progen invariants, dead code, and test coverage; write report to {report_path}.",
        parameters={
            "target_dir": {"type": "string", "required": False, "default": ".", "description": "Source root directory to inspect"},
            "report_path": {"type": "string", "required": False, "default": "reports/refactor_audit.md", "description": "Markdown audit report file"},
        },
        tool_profile="workspace_only",
        max_steps=15,
        sample_steps=[
            {"action": "list_dir", "path": "{{target_dir}}"},
            {"action": "find_files", "pattern": "*.py"},
            {"action": "read_file", "path": "AGENTS.md"},
        ],
    ),
    "web_extract": WorkflowTemplate(
        template_id="web_extract",
        name="Web Table Extractor",
        description="Navigate to target web page and extract tabular information",
        template="Navigate to {url} and extract table data matching selector {selector}.",
        parameters={
            "url": {"type": "string", "required": True, "description": "Target webpage URL"},
            "selector": {"type": "string", "required": False, "default": "table", "description": "CSS selector for table"},
        },
        tool_profile="browser_only",
        max_steps=10,
    ),
    "file_audit": WorkflowTemplate(
        template_id="file_audit",
        name="Workspace File Security Audit",
        description="Inspect local workspace files for sensitive patterns and security policy",
        template="Inspect file {path} and audit for security policy compliance with rule {rule}.",
        parameters={
            "path": {"type": "string", "required": True, "description": "Relative file path in workspace"},
            "rule": {"type": "string", "required": False, "default": "P018", "description": "Invariant rule name"},
        },
        tool_profile="workspace_only",
        max_steps=8,
    ),
    "system_status": WorkflowTemplate(
        template_id="system_status",
        name="System Environment Diagnostic",
        description="Check active windows and capture display snapshot",
        template="Inspect active system window matching pattern {pattern} and capture screen verification.",
        parameters={
            "pattern": {"type": "string", "required": False, "default": ".*", "description": "Window title regex pattern"},
        },
        tool_profile="readonly",
        max_steps=5,
    ),
    "form_automation": WorkflowTemplate(
        template_id="form_automation",
        name="Web Form Auto-Filler",
        description="Navigate to URL and fill input form fields",
        template="Navigate to {url} and populate form input fields: {fields}.",
        parameters={
            "url": {"type": "string", "required": True, "description": "Target form URL"},
            "fields": {"type": "string", "required": True, "description": "Form fields description"},
        },
        tool_profile="browser_only",
        max_steps=10,
    ),
    "code_refactor": WorkflowTemplate(
        template_id="code_refactor",
        name="Code Refactor Runner",
        description="Analyze workspace code file and apply automated refactoring",
        template="Read workspace file {path} and implement refactoring instruction: {instruction}.",
        parameters={
            "path": {"type": "string", "required": True, "description": "Target workspace code file"},
            "instruction": {"type": "string", "required": True, "description": "Refactoring instructions"},
        },
        tool_profile="workspace_only",
        max_steps=12,
    ),
}


def get_workflow_template(template_id: str) -> Optional[WorkflowTemplate]:
    return WORKFLOW_TEMPLATES.get(template_id)


def list_workflow_templates() -> List[Dict[str, Any]]:
    return [t.to_dict() for t in WORKFLOW_TEMPLATES.values()]


def register_workflow_template(template: WorkflowTemplate) -> None:
    WORKFLOW_TEMPLATES[template.template_id] = template


def interpolate_workflow_template(template_id: str, params: Dict[str, Any]) -> Dict[str, Any]:
    template = get_workflow_template(template_id)
    if template is None:
        return {"isError": True, "error": f"Unknown workflow template: '{template_id}'"}
    try:
        rendered, sanitized = template.render(params)
        return {
            "isError": False,
            "template_id": template_id,
            "name": template.name,
            "rendered_prompt": rendered,
            "sanitized_params": sanitized,
            "tool_profile": template.tool_profile,
            "max_steps": template.max_steps,
            "model": template.model,
            "task_description": rendered,
            "parameters": sanitized,
            "steps": template.sample_steps,
        }
    except Exception as exc:
        return {"isError": True, "error": str(exc), "template_id": template_id}


instantiate_workflow_template = interpolate_workflow_template


class JobStatus:
    SCHEDULED = "scheduled"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"


def parse_cron_to_interval(cron_expr: str) -> float:
    expr = cron_expr.strip()
    if expr.startswith("@every"):
        parts = expr.split()
        if len(parts) >= 2:
            val = parts[1].lower()
            if val.endswith("s"):
                return max(1.0, float(val[:-1]))
            elif val.endswith("m"):
                return max(1.0, float(val[:-1]) * 60.0)
            elif val.endswith("h"):
                return max(1.0, float(val[:-1]) * 3600.0)
            elif val.endswith("d"):
                return max(1.0, float(val[:-1]) * 86400.0)
    elif expr == "@hourly":
        return 3600.0
    elif expr == "@daily":
        return 86400.0
    parts = expr.split()
    if len(parts) >= 5:
        first = parts[0]
        if first.startswith("*/"):
            try:
                mins = int(first[2:])
                return max(1.0, float(mins * 60))
            except ValueError:
                pass
        elif first == "*":
            return 60.0
    return 60.0


class ScheduledJob:
    """
    Scheduled autonomous agent task instance with isolation locks and timer lifecycle.
    Guarantees recurring tasks cannot execute concurrently if previous run is still active.
    """

    def __init__(
        self,
        job_id: str,
        name: str,
        task: str,
        template_id: Optional[str] = None,
        params: Optional[Dict[str, Any]] = None,
        interval_sec: Optional[float] = None,
        delay_sec: float = 0.0,
        recurring: bool = False,
        max_runs: Optional[int] = None,
        tool_profile: str = "full_automation",
        model: Optional[str] = None,
        max_steps: int = 15,
        steps: Optional[List[Dict[str, Any]]] = None,
        interval_seconds: Optional[float] = None,
        cron_expression: Optional[str] = None,
        task_description: Optional[str] = None,
    ) -> None:
        self.job_id = job_id
        self.name = name
        self.task = task or task_description or ""
        self.task_description = self.task
        self.template_id = template_id
        self.params = params or {}
        
        eff_interval = interval_seconds if interval_seconds is not None else interval_sec
        if cron_expression:
            self.interval_sec = parse_cron_to_interval(cron_expression)
        elif eff_interval is not None:
            self.interval_sec = float(eff_interval)
        else:
            self.interval_sec = None
        self.interval_seconds = self.interval_sec or 60.0
        self.cron_expression = cron_expression

        self.delay_sec = delay_sec
        self.recurring = recurring or (eff_interval is not None)
        self.max_runs = max_runs
        self.tool_profile = tool_profile
        self.model = model
        self.max_steps = max_steps
        self.steps = steps or []

        self.status = JobStatus.SCHEDULED
        self.run_count: int = 0
        self.created_at: float = time.time()
        self.last_run_at: Optional[float] = None
        self.last_run: Optional[str] = None
        self.next_run_at: Optional[float] = None
        self.next_run_ts: Optional[float] = None
        self.last_result: Optional[Dict[str, Any]] = None
        self.last_error: Optional[str] = None
        self.is_running: bool = False
        self.history: List[Dict[str, Any]] = []

        self._isolation_lock = threading.Lock()
        self._timer_lock = threading.Lock()
        self._timer: Optional[threading.Timer] = None
        self._async_task: Optional[Any] = None
        self._cancelled_event = threading.Event()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "job_id": self.job_id,
            "name": self.name,
            "task": self.task,
            "task_description": self.task,
            "template_id": self.template_id,
            "params": dict(self.params),
            "interval_sec": self.interval_sec,
            "interval_seconds": self.interval_seconds,
            "cron_expression": self.cron_expression,
            "delay_sec": self.delay_sec,
            "recurring": self.recurring,
            "max_runs": self.max_runs,
            "tool_profile": self.tool_profile,
            "model": self.model,
            "max_steps": self.max_steps,
            "status": self.status,
            "run_count": self.run_count,
            "created_at": self.created_at,
            "last_run_at": self.last_run_at,
            "last_run": self.last_run,
            "next_run_at": self.next_run_at,
            "next_run_ts": self.next_run_ts,
            "is_running": self.is_running,
            "last_error": self.last_error,
            "history_count": len(self.history),
        }

    def arm(self, delay: Optional[float] = None) -> None:
        if self._cancelled_event.is_set():
            return
        d = delay if delay is not None else self.delay_sec
        with self._timer_lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
            self.next_run_at = time.time() + d
            self.next_run_ts = self.next_run_at
            self._timer = threading.Timer(d, self._timer_tick)
            self._timer.daemon = True
            self._timer.start()

    def _timer_tick(self) -> None:
        with self._timer_lock:
            self._timer = None
        if self._cancelled_event.is_set() or self.status == JobStatus.PAUSED:
            return
        self.run()

    def cancel(self) -> None:
        self._cancelled_event.set()
        self.status = JobStatus.CANCELLED
        self.next_run_at = None
        self.next_run_ts = None
        with self._timer_lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
        if self._async_task is not None and hasattr(self._async_task, "cancel"):
            try:
                self._async_task.cancel()
            except Exception:
                pass
            self._async_task = None

    def pause(self) -> None:
        self.status = JobStatus.PAUSED
        with self._timer_lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None

    def resume(self) -> None:
        if self.status == JobStatus.PAUSED and not self._cancelled_event.is_set():
            self.status = JobStatus.SCHEDULED
            self.arm(self.interval_sec or 1.0)

    def run(self, runner: Optional[Any] = None) -> Dict[str, Any]:
        if self._cancelled_event.is_set():
            return {
                "isError": True,
                "error": f"Job '{self.name}' ({self.job_id}) is cancelled",
                "status": JobStatus.CANCELLED,
            }

        acquired = self._isolation_lock.acquire(blocking=False)
        if not acquired:
            err = f"Job '{self.name}' ({self.job_id}) is already actively executing. Concurrent run blocked by isolation lock."
            logger.warning(err)
            if self.recurring and not self._cancelled_event.is_set():
                if self.max_runs is None or self.run_count < self.max_runs:
                    self.arm(self.interval_sec or 60.0)
            skip_record = {
                "execution_id": f"exec-skip-{uuid.uuid4().hex[:8]}",
                "job_id": self.job_id,
                "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "status": "skipped",
                "reason": err,
                "duration_sec": 0.0,
            }
            self.history.append(skip_record)
            return {
                "isError": True,
                "error": err,
                "concurrent_blocked": True,
                "skipped": True,
                "status": self.status,
            }

        t0 = time.perf_counter()
        started_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
        exec_id = f"exec-{uuid.uuid4().hex[:8]}"

        try:
            self.is_running = True
            self.status = JobStatus.RUNNING
            self.last_run_at = time.time()
            self.last_run = started_at

            from hydra_cli.agent_runner import get_agent_runner, AutonomousAgentRunner
            active_runner = runner or AutonomousAgentRunner(tool_profile=self.tool_profile, max_steps=self.max_steps)

            res = active_runner.run_task(
                task_description=self.task,
                steps=self.steps if self.steps else None,
                max_steps=self.max_steps,
            )

            duration_sec = time.perf_counter() - t0
            self.run_count += 1
            self.last_result = res
            if res.get("isError"):
                self.last_error = res.get("error")

            exec_record = {
                "execution_id": exec_id,
                "job_id": self.job_id,
                "timestamp": started_at,
                "duration_sec": round(duration_sec, 3),
                "status": "success" if not res.get("isError") else "error",
                "result_status": res.get("status"),
                "step_count": res.get("step_count", 0),
                "error": res.get("error"),
            }
            self.history.append(exec_record)

            if self.recurring and not self._cancelled_event.is_set():
                if self.max_runs is None or self.run_count < self.max_runs:
                    self.status = JobStatus.SCHEDULED
                    self.arm(self.interval_sec or 60.0)
                else:
                    self.status = JobStatus.COMPLETED
                    self.next_run_at = None
                    self.next_run_ts = None
            else:
                self.status = JobStatus.COMPLETED
                self.next_run_at = None
                self.next_run_ts = None

            return res
        except Exception as exc:
            duration_sec = time.perf_counter() - t0
            self.status = JobStatus.FAILED
            self.last_error = str(exc)
            exec_record = {
                "execution_id": exec_id,
                "job_id": self.job_id,
                "timestamp": started_at,
                "duration_sec": round(duration_sec, 3),
                "status": "error",
                "error": str(exc),
            }
            self.history.append(exec_record)
            return {"isError": True, "error": str(exc), "status": JobStatus.FAILED}
        finally:
            self.is_running = False
            self._isolation_lock.release()

    def execute_now(self) -> Dict[str, Any]:
        return self.run()


class AgentJobScheduler:
    """Central scheduler managing scheduled and recurring agent jobs."""

    def __init__(self) -> None:
        self._jobs: Dict[str, ScheduledJob] = {}
        self._lock = threading.RLock()

    def schedule_job(
        self,
        name: str,
        task: Optional[str] = None,
        template_id: Optional[str] = None,
        params: Optional[Dict[str, Any]] = None,
        interval_sec: Optional[float] = None,
        delay_sec: float = 0.0,
        recurring: bool = False,
        max_runs: Optional[int] = None,
        tool_profile: str = "full_automation",
        model: Optional[str] = None,
        max_steps: int = 15,
        steps: Optional[List[Dict[str, Any]]] = None,
        job_id: Optional[str] = None,
        auto_arm: bool = True,
        interval_seconds: Optional[float] = None,
        cron_expression: Optional[str] = None,
        task_description: Optional[str] = None,
    ) -> ScheduledJob:
        resolved_task = task or task_description or ""
        resolved_profile = tool_profile

        if template_id:
            interp = interpolate_workflow_template(template_id, params or {})
            if interp.get("isError"):
                raise ValueError(interp.get("error"))
            resolved_task = interp["rendered_prompt"]
            if interp.get("tool_profile"):
                resolved_profile = interp["tool_profile"]
            if interp.get("max_steps"):
                max_steps = interp["max_steps"]

        if not resolved_task and not steps:
            raise ValueError("Scheduled job requires either a task description, template_id, or explicit steps")

        jid = job_id or f"job-{int(time.time())}-{uuid.uuid4().hex[:6]}"
        job = ScheduledJob(
            job_id=jid,
            name=name,
            task=resolved_task,
            template_id=template_id,
            params=params,
            interval_sec=interval_sec,
            delay_sec=delay_sec,
            recurring=recurring,
            max_runs=max_runs,
            tool_profile=resolved_profile,
            model=model,
            max_steps=max_steps,
            steps=steps,
            interval_seconds=interval_seconds,
            cron_expression=cron_expression,
        )

        with self._lock:
            self._jobs[jid] = job

        if auto_arm:
            job.arm()

        return job

    def get_job(self, job_id: str) -> Optional[ScheduledJob]:
        with self._lock:
            return self._jobs.get(job_id)

    def list_jobs(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [j.to_dict() for j in self._jobs.values()]

    def get_jobs(self) -> List[Dict[str, Any]]:
        return self.list_jobs()

    def get_job_history(self, job_id: Optional[str] = None) -> List[Dict[str, Any]]:
        with self._lock:
            if job_id:
                job = self._jobs.get(job_id)
                return list(job.history) if job else []
            all_h = []
            for j in self._jobs.values():
                all_h.extend(j.history)
            all_h.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
            return all_h

    def cancel_job(self, job_id: str) -> bool:
        with self._lock:
            job = self._jobs.get(job_id)
            if job:
                job.cancel()
                return True
            return False

    def pause_job(self, job_id: str) -> bool:
        with self._lock:
            job = self._jobs.get(job_id)
            if job:
                job.pause()
                return True
            return False

    def resume_job(self, job_id: str) -> bool:
        with self._lock:
            job = self._jobs.get(job_id)
            if job:
                job.resume()
                return True
            return False

    def run_job_now(self, job_id: str, runner: Optional[Any] = None) -> Dict[str, Any]:
        job = self.get_job(job_id)
        if not job:
            return {"isError": True, "error": f"Job '{job_id}' not found"}
        res = job.run(runner=runner)
        if isinstance(res, dict):
            res["job_id"] = job_id
        return res

    def tick(self, now: Optional[float] = None) -> List[Dict[str, Any]]:
        current_time = now if now is not None else time.time()
        triggered = []
        with self._lock:
            active = [j for j in self._jobs.values() if j.status == JobStatus.SCHEDULED and j.next_run_at and current_time >= j.next_run_at]
        for job in active:
            triggered.append(job.run())
        return triggered

    def stop_all(self) -> None:
        with self._lock:
            for job in list(self._jobs.values()):
                job.cancel()

    def reset(self) -> None:
        self.stop_all()
        with self._lock:
            self._jobs.clear()

    def clear(self) -> None:
        self.reset()


ScheduledJobRunner = AgentJobScheduler

_GLOBAL_JOB_SCHEDULER: Optional[AgentJobScheduler] = None
_SCHEDULER_LOCK = threading.RLock()


def get_job_scheduler() -> AgentJobScheduler:
    global _GLOBAL_JOB_SCHEDULER
    with _SCHEDULER_LOCK:
        if _GLOBAL_JOB_SCHEDULER is None:
            _GLOBAL_JOB_SCHEDULER = AgentJobScheduler()
        return _GLOBAL_JOB_SCHEDULER


def reset_job_scheduler() -> None:
    global _GLOBAL_JOB_SCHEDULER
    with _SCHEDULER_LOCK:
        if _GLOBAL_JOB_SCHEDULER is not None:
            _GLOBAL_JOB_SCHEDULER.reset()
            _GLOBAL_JOB_SCHEDULER = None


get_scheduled_job_runner = get_job_scheduler
reset_scheduled_job_runner = reset_job_scheduler
