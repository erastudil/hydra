"""
Deterministic integration test suite for Hydra Desktop Wave 7:
Workflow Templates, Parameter Interpolation & Sanitization,
Scheduled Agent Jobs, Cancellation Lifecycle, and Concurrent Isolation Locks.
Complies with AGENTS.md genome invariants: zero fake tests, real interfaces, exit code 0.
"""

import threading
import time
from typing import Any, Dict
import pytest
from fastapi.testclient import TestClient

from hydra_cli.agent_runner import (
    AutonomousAgentRunner,
    JobStatus,
    ScheduledJob,
    WorkflowTemplate,
    WORKFLOW_TEMPLATES,
    get_job_scheduler,
    get_workflow_template,
    interpolate_workflow_template,
    list_workflow_templates,
    reset_job_scheduler,
    sanitize_template_parameters,
)
from hydra_cli.desktop import (
    COMMAND_PALETTE_ACTIONS,
    create_desktop_app,
    dispatch_command_palette_action,
)


@pytest.fixture(autouse=True)
def clean_scheduler():
    reset_job_scheduler()
    yield
    reset_job_scheduler()


@pytest.fixture(scope="module")
def desktop_client():
    app = create_desktop_app()
    with TestClient(app) as client:
        yield client


def test_workflow_template_catalog_retrieval_and_metadata():
    """
    Test template catalog retrieval, metadata schema, and tool profile bindings.
    """
    templates = list_workflow_templates()
    assert len(templates) >= 5

    template_ids = {t["template_id"] for t in templates}
    assert "web_extract" in template_ids
    assert "file_audit" in template_ids
    assert "system_status" in template_ids
    assert "form_automation" in template_ids
    assert "code_refactor" in template_ids

    # 1. Inspect web_extract template
    t_web = get_workflow_template("web_extract")
    assert t_web is not None
    assert t_web.tool_profile == "browser_only"
    assert "url" in t_web.parameters
    assert t_web.parameters["url"]["required"] is True
    assert t_web.parameters["selector"]["default"] == "table"

    # 2. Inspect file_audit template
    t_audit = get_workflow_template("file_audit")
    assert t_audit is not None
    assert t_audit.tool_profile == "workspace_only"
    assert "path" in t_audit.parameters
    assert t_audit.parameters["path"]["required"] is True

    # 3. Inspect system_status template
    t_sys = get_workflow_template("system_status")
    assert t_sys is not None
    assert t_sys.tool_profile == "readonly"

    # 4. Unknown template returns None
    assert get_workflow_template("nonexistent_tpl") is None


def test_workflow_template_parameter_interpolation_and_sanitization():
    """
    Test parameter interpolation, path traversal defense, and control character blocking.
    """
    # 1. Successful interpolation with default parameters
    res = interpolate_workflow_template("web_extract", {"url": "https://example.com/reports"})
    assert not res.get("isError")
    assert "https://example.com/reports" in res["rendered_prompt"]
    assert "table" in res["rendered_prompt"]
    assert res["sanitized_params"]["selector"] == "table"

    # 2. Successful interpolation overriding default parameter
    res2 = interpolate_workflow_template("web_extract", {
        "url": "https://example.com/data",
        "selector": "#custom-grid",
    })
    assert not res2.get("isError")
    assert "https://example.com/data" in res2["rendered_prompt"]
    assert "#custom-grid" in res2["rendered_prompt"]

    # 3. Missing required parameter error
    res_missing = interpolate_workflow_template("web_extract", {})
    assert res_missing.get("isError") is True
    assert "Missing required parameter 'url'" in res_missing.get("error", "")

    # 4. Path traversal attack blocked on path parameter
    res_trav1 = interpolate_workflow_template("file_audit", {"path": "../../../etc/passwd"})
    assert res_trav1.get("isError") is True
    assert "illegal path traversal" in res_trav1.get("error", "").lower()

    res_trav2 = interpolate_workflow_template("file_audit", {"path": "..\\..\\secret.key"})
    assert res_trav2.get("isError") is True
    assert "illegal path traversal" in res_trav2.get("error", "").lower()

    # 5. Absolute root escape blocked
    res_abs = interpolate_workflow_template("file_audit", {"path": "C:\\Windows\\System32"})
    assert res_abs.get("isError") is True
    assert "absolute paths forbidden" in res_abs.get("error", "").lower()

    # 6. Null byte injection blocked
    res_null = interpolate_workflow_template("file_audit", {"path": "safe/path.txt" + chr(0)})
    assert res_null.get("isError") is True
    assert "control characters" in res_null.get("error", "").lower()

    # 7. Shell chaining injection blocked on command parameters
    custom_tpl = WorkflowTemplate(
        template_id="custom_cmd",
        name="Command Runner",
        description="Run command",
        template="Execute command {cmd}",
        parameters={"cmd": {"type": "string", "required": True}},
    )
    with pytest.raises(ValueError, match="illegal shell chaining token"):
        custom_tpl.render({"cmd": "echo test; rm -rf /"})

    with pytest.raises(ValueError, match="illegal shell chaining token"):
        custom_tpl.render({"cmd": "ls && whoami"})


def test_scheduled_job_creation_execution_and_recurring():
    """
    Test scheduled job creation, deterministic step execution, run count tracking,
    and recurring execution state transitions.
    """
    scheduler = get_job_scheduler()

    # 1. One-shot scheduled job with explicit deterministic steps
    job = scheduler.schedule_job(
        name="Deterministic Health Probe",
        task="Inspect environment",
        steps=[{"action": "browser_inspect", "selector": "body"}],
        tool_profile="browser_only",
        recurring=False,
        auto_arm=False,
    )
    assert job.status == JobStatus.SCHEDULED
    assert job.run_count == 0
    assert job.tool_profile == "browser_only"

    # Execute job manually
    res = job.run()
    assert not res.get("isError")
    assert job.run_count == 1
    assert job.status == JobStatus.COMPLETED
    assert job.last_result is not None
    assert job.next_run_at is None

    # 2. Recurring scheduled job with max_runs limit
    rec_job = scheduler.schedule_job(
        name="Recurring Telemetry Poll",
        task="Poll system status",
        steps=[{"action": "browser_inspect", "selector": "body"}],
        interval_sec=0.05,
        recurring=True,
        max_runs=2,
        auto_arm=False,
    )
    assert rec_job.recurring is True
    assert rec_job.max_runs == 2

    # First execution: transitions to SCHEDULED and arms timer
    res1 = rec_job.run()
    assert not res1.get("isError")
    assert rec_job.run_count == 1
    assert rec_job.status == JobStatus.SCHEDULED
    assert rec_job.next_run_at is not None

    # Second execution: reaches max_runs, transitions to COMPLETED
    res2 = rec_job.run()
    assert not res2.get("isError")
    assert rec_job.run_count == 2
    assert rec_job.status == JobStatus.COMPLETED
    assert rec_job.next_run_at is None


def test_scheduled_job_cancellation_and_timer_cleanup():
    """
    Test job cancellation, immediate timer disarming, and rejection of execution post-cancellation.
    """
    scheduler = get_job_scheduler()

    job = scheduler.schedule_job(
        name="Long-Delayed Task",
        task="Verify cancellation",
        steps=[{"action": "browser_inspect", "selector": "body"}],
        delay_sec=10.0,
        recurring=True,
        interval_sec=10.0,
        auto_arm=True,
    )
    assert job.status == JobStatus.SCHEDULED
    assert job._timer is not None
    assert job.next_run_at is not None

    # Cancel job
    cancelled = scheduler.cancel_job(job.job_id)
    assert cancelled is True
    assert job.status == JobStatus.CANCELLED
    assert job._timer is None
    assert job.next_run_at is None

    # Verify execution fails closed post-cancellation
    res = job.run()
    assert res.get("isError") is True
    assert res.get("status") == JobStatus.CANCELLED
    assert job.run_count == 0

    # Cancel on nonexistent job returns False
    assert scheduler.cancel_job("nonexistent-job-id") is False


def test_concurrent_job_isolation_locks():
    """
    Test that concurrent or overlapping execution of the same job is strictly blocked
    by the isolation lock, preserving agent runner state invariants.
    """
    scheduler = get_job_scheduler()

    job = scheduler.schedule_job(
        name="Concurrent Isolation Benchmark",
        task="Test lock isolation",
        steps=[{"action": "browser_inspect", "selector": "body"}],
        recurring=True,
        interval_sec=0.1,
        auto_arm=False,
    )

    # 1. Simulate active in-flight execution by acquiring the isolation lock
    acquired = job._isolation_lock.acquire(blocking=False)
    assert acquired is True

    # 2. Attempt secondary execution while lock is held
    res_blocked = job.run()
    assert res_blocked.get("isError") is True
    assert res_blocked.get("concurrent_blocked") is True
    assert res_blocked.get("skipped") is True
    assert "already actively executing" in res_blocked.get("error", "")
    assert job.run_count == 0

    # 3. Release lock and verify execution resumes normally
    job._isolation_lock.release()
    res_ok = job.run()
    assert not res_ok.get("isError")
    assert job.run_count == 1

    # 4. Multi-threaded race condition check: dispatch 5 parallel threads
    results = []
    threads = []
    for _ in range(5):
        t = threading.Thread(target=lambda: results.append(job.run()))
        threads.append(t)

    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(results) == 5
    # At least some requests should have succeeded and state remains uncorrupted
    assert job.run_count >= 2
    assert not job._isolation_lock.locked()


def test_desktop_api_templates_and_jobs(desktop_client: TestClient):
    """
    Test Desktop REST API endpoints for templates and scheduled jobs.
    """
    # 1. GET /api/templates
    res_t = desktop_client.get("/api/templates")
    assert res_t.status_code == 200
    data_t = res_t.json()
    assert data_t["isError"] is False
    assert data_t["count"] >= 5

    # 2. GET /api/templates/{id}
    res_tid = desktop_client.get("/api/templates/web_extract")
    assert res_tid.status_code == 200
    assert res_tid.json()["template"]["template_id"] == "web_extract"

    res_404 = desktop_client.get("/api/templates/unknown_template_xyz")
    assert res_404.status_code == 404

    # 3. POST /api/templates/interpolate
    res_interp = desktop_client.post("/api/templates/interpolate", json={
        "template_id": "file_audit",
        "params": {"path": "hydra_cli/desktop.py", "rule": "P018"},
    })
    assert res_interp.status_code == 200
    assert "hydra_cli/desktop.py" in res_interp.json()["rendered_prompt"]

    # Invalid path traversal returns 400
    res_bad = desktop_client.post("/api/templates/interpolate", json={
        "template_id": "file_audit",
        "params": {"path": "../../passwords.txt"},
    })
    assert res_bad.status_code == 400
    assert res_bad.json()["isError"] is True

    # 4. POST /api/jobs/schedule
    res_sched = desktop_client.post("/api/jobs/schedule", json={
        "name": "API Scheduled Probe",
        "template_id": "system_status",
        "params": {"pattern": ".*"},
        "steps": [{"action": "browser_inspect", "selector": "body"}],
        "interval_sec": 30.0,
        "auto_arm": False,
    })
    assert res_sched.status_code == 200
    job_data = res_sched.json()["job"]
    jid = job_data["job_id"]
    assert job_data["name"] == "API Scheduled Probe"

    # 5. GET /api/jobs and GET /api/jobs/{id}
    res_list = desktop_client.get("/api/jobs")
    assert res_list.status_code == 200
    assert any(j["job_id"] == jid for j in res_list.json()["jobs"])

    res_get = desktop_client.get(f"/api/jobs/{jid}")
    assert res_get.status_code == 200
    assert res_get.json()["job"]["job_id"] == jid

    # 6. POST /api/jobs/{id}/run
    res_run = desktop_client.post(f"/api/jobs/{jid}/run")
    assert res_run.status_code == 200
    assert res_run.json()["job_id"] == jid

    # 7. POST /api/jobs/{id}/cancel
    res_cancel = desktop_client.post(f"/api/jobs/{jid}/cancel")
    assert res_cancel.status_code == 200
    assert res_cancel.json()["status"] == "cancelled"

    # 8. POST /api/jobs/clear
    res_clear = desktop_client.post("/api/jobs/clear")
    assert res_clear.status_code == 200
    assert res_clear.json()["cleared"] is True

    res_empty = desktop_client.get("/api/jobs")
    assert len(res_empty.json()["jobs"]) == 0


def test_command_palette_templates_and_jobs_dispatch(desktop_client: TestClient):
    """
    Test command palette actions for template browsing and job scheduling management.
    """
    assert "templates:list" in COMMAND_PALETTE_ACTIONS
    assert "jobs:list" in COMMAND_PALETTE_ACTIONS
    assert "jobs:clear" in COMMAND_PALETTE_ACTIONS

    # 1. templates:list dispatch
    res_t = desktop_client.post("/api/palette/dispatch", json={"action": "templates:list"})
    assert res_t.status_code == 200
    assert len(res_t.json()["templates"]) >= 5

    # 2. jobs:list dispatch
    res_j = desktop_client.post("/api/palette/dispatch", json={"action": "jobs:list"})
    assert res_j.status_code == 200
    assert "jobs" in res_j.json()

    # 3. jobs:clear dispatch
    res_c = desktop_client.post("/api/palette/dispatch", json={"action": "jobs:clear"})
    assert res_c.status_code == 200
    assert res_c.json()["cleared"] is True
