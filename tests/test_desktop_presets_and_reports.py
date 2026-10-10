"""
Deterministic integration test suite for Hydra Desktop Tool Preset Profiles,
Command Palette Action Dispatch, and Markdown Execution Reports.
Complies with AGENTS.md genome invariants: zero fake tests, real interfaces, exit code 0.
"""

import json
from typing import Any, Dict
import pytest
from fastapi.testclient import TestClient

from hydra_cli.agent_runner import (
    AgentState,
    AutonomousAgentRunner,
    TOOL_PROFILES,
    is_tool_allowed_under_profile,
    generate_markdown_report,
    get_agent_runner,
    reset_agent_runner,
)
from hydra_cli.desktop import (
    COMMAND_PALETTE_ACTIONS,
    create_desktop_app,
    dispatch_command_palette_action,
)


@pytest.fixture(scope="module")
def desktop_client():
    app = create_desktop_app()
    with TestClient(app) as client:
        yield client


def test_tool_preset_profile_filtering_across_four_presets():
    """
    Test tool profile filtering across all 4 presets:
    full, browser_only, workspace_only, and readonly.
    """
    runner = AutonomousAgentRunner(max_steps=5, tool_profile="full")

    # 1. Full preset profile allows all categories
    assert runner.tool_profile == "full"
    allowed_os, _ = is_tool_allowed_under_profile("mouse_click", "full")
    allowed_br, _ = is_tool_allowed_under_profile("browser_inspect", "full")
    allowed_ws, _ = is_tool_allowed_under_profile("read_file", "full")
    assert allowed_os is True
    assert allowed_br is True
    assert allowed_ws is True

    # 2. Browser-only preset strictly blocks OS controller and workspace file operations
    runner.set_tool_profile("browser_only")
    assert runner.tool_profile == "browser_only"

    # Browser tools permitted
    res_br = runner.execute_step("browser_inspect", selector="body")
    assert not res_br.get("profile_violation")

    # OS tools forbidden
    res_os = runner.execute_step("mouse_click", x=100, y=100)
    assert res_os.get("isError") is True
    assert res_os.get("profile_violation") is True
    assert "forbidden under profile 'browser_only'" in res_os.get("error", "")

    # Workspace tools forbidden
    res_ws = runner.execute_step("read_file", path="README.md")
    assert res_ws.get("isError") is True
    assert res_ws.get("profile_violation") is True
    assert "forbidden under profile 'browser_only'" in res_ws.get("error", "")

    # 3. Workspace-only preset strictly blocks browser and OS controller operations
    runner.set_tool_profile("workspace_only")
    assert runner.tool_profile == "workspace_only"

    # Workspace tools permitted
    res_ws_ok = runner.execute_step("read_file", path="README.md")
    assert not res_ws_ok.get("profile_violation")

    # Browser tools forbidden
    res_br_fail = runner.execute_step("browser_click", selector="button")
    assert res_br_fail.get("isError") is True
    assert res_br_fail.get("profile_violation") is True
    assert "forbidden under profile 'workspace_only'" in res_br_fail.get("error", "")

    # OS tools forbidden
    res_os_fail = runner.execute_step("key_press", key="enter")
    assert res_os_fail.get("isError") is True
    assert res_os_fail.get("profile_violation") is True
    assert "forbidden under profile 'workspace_only'" in res_os_fail.get("error", "")

    # 4. Readonly preset permits inspection and strictly blocks state mutations
    runner.set_tool_profile("readonly")
    assert runner.tool_profile == "readonly"

    # Inspection actions permitted
    assert not runner.execute_step("read_file", path="README.md").get("profile_violation")
    assert not runner.execute_step("list_dir", path=".").get("profile_violation")
    assert not runner.execute_step("browser_inspect", selector="body").get("profile_violation")

    # Mutating workspace actions forbidden
    res_write = runner.execute_step("write_file", path="out.txt", content="payload")
    assert res_write.get("isError") is True
    assert res_write.get("profile_violation") is True
    assert "forbidden under profile 'readonly'" in res_write.get("error", "")

    # Mutating OS actions forbidden
    res_click = runner.execute_step("mouse_click", x=200, y=200)
    assert res_click.get("isError") is True
    assert res_click.get("profile_violation") is True
    assert "forbidden under profile 'readonly'" in res_click.get("error", "")


def test_markdown_report_structure_and_metrics():
    """
    Test markdown audit report generator syntax, action count accuracy, duration math,
    and structured tool result representation.
    """
    runner = AutonomousAgentRunner(max_steps=5, tool_profile="full")
    plan = [
        {"action": "browser_inspect", "selector": "body"},
        {"action": "browser_inspect", "selector": "body"},
    ]
    runner.run_task(task_description="Telemetry Audit Benchmark", steps=plan)
    trace = runner.export_session_trace()

    # Generate markdown report
    md = generate_markdown_report(trace)

    # 1. Markdown syntax headers
    assert "# Hydra Agent Execution Report" in md
    assert "## Session Overview" in md
    assert "## Execution Trace Breakdown" in md
    assert "## Step Details" in md
    assert "## Verification Gate" in md

    # 2. Accurate metadata representation
    assert f"`{trace['session_id']}`" in md
    assert "Telemetry Audit Benchmark" in md
    assert "`completed`" in md
    assert "`full`" in md

    # 3. Action count and duration math
    assert f"**Total Actions Executed**: {len(trace['history'])}" in md
    assert "Duration" in md
    assert "Success Rate" in md

    # 4. Table structure
    assert "| Step | Action | Status | Duration (s) | Details |" in md
    assert "| `browser_inspect` | SUCCESS |" in md

    # 5. Method on runner instance matches
    runner_md = runner.generate_markdown_report()
    assert "# Hydra Agent Execution Report" in runner_md
    assert "Telemetry Audit Benchmark" in runner_md
    assert "| `browser_inspect` | SUCCESS |" in runner_md


def test_markdown_export_api_endpoints(desktop_client: TestClient):
    """
    Test Desktop API /api/agent/report/markdown and /api/session/report.md endpoints.
    """
    # 1. Standard markdown GET
    res = desktop_client.get("/api/agent/report/markdown")
    assert res.status_code == 200
    assert "text/markdown" in res.headers.get("content-type", "")
    assert "# Hydra Agent Execution Report" in res.text

    # 2. Download markdown GET with attachment disposition
    res_dl = desktop_client.get("/api/agent/report/markdown?download=true")
    assert res_dl.status_code == 200
    disposition = res_dl.headers.get("content-disposition", "")
    assert "attachment" in disposition
    assert ".md" in disposition

    # 3. Alias /api/session/report.md
    res_alias = desktop_client.get("/api/session/report.md")
    assert res_alias.status_code == 200
    assert "# Hydra Agent Execution Report" in res_alias.text


def test_agent_profiles_api_endpoints(desktop_client: TestClient):
    """
    Test Desktop API /api/agent/profiles GET and /api/agent/profile POST endpoints.
    """
    # 1. Get profile catalog
    res_list = desktop_client.get("/api/agent/profiles")
    assert res_list.status_code == 200
    data = res_list.json()
    assert "profiles" in data
    assert "full" in data["profiles"]
    assert "browser_only" in data["profiles"]
    assert "workspace_only" in data["profiles"]
    assert "readonly" in data["profiles"]

    # 2. Update profile via POST
    res_set = desktop_client.post("/api/agent/profile", json={"profile": "browser_only"})
    assert res_set.status_code == 200
    assert res_set.json().get("profile") == "browser_only"

    # 3. Verify active profile changed
    res_check = desktop_client.get("/api/agent/profiles")
    assert res_check.json().get("current_profile") == "browser_only"

    # Reset back to full
    desktop_client.post("/api/agent/profile", json={"profile": "full"})


def test_command_palette_action_catalog_and_dispatch(desktop_client: TestClient):
    """
    Test Desktop API /api/palette/actions and /api/palette/dispatch mappings.
    """
    # 1. Catalog retrieval
    cat_res = desktop_client.get("/api/palette/actions")
    assert cat_res.status_code == 200
    cat_data = cat_res.json()
    assert cat_data.get("count") >= 10
    catalog = cat_data.get("catalog", {})
    assert "agent:pause" in catalog
    assert "agent:resume" in catalog
    assert "agent:abort" in catalog
    assert "agent:export_markdown" in catalog
    assert "agent:set_profile" in catalog
    assert "token:summary" in catalog

    # 2. Dispatch profile configuration through palette
    disp_prof = desktop_client.post("/api/palette/dispatch", json={
        "action": "agent:set_profile",
        "params": {"profile": "readonly"}
    })
    assert disp_prof.status_code == 200
    assert disp_prof.json().get("profile") == "readonly"

    # 3. Dispatch token summary through palette
    disp_tok = desktop_client.post("/api/palette/dispatch", json={"action": "token:summary"})
    assert disp_tok.status_code == 200
    assert "summary" in disp_tok.json()

    # 4. Dispatch markdown report through palette
    disp_md = desktop_client.post("/api/palette/dispatch", json={"action": "agent:export_markdown"})
    assert disp_md.status_code == 200
    assert "# Hydra Agent Execution Report" in disp_md.json().get("markdown", "")

    # 5. Invalid palette action returns 400
    bad_disp = desktop_client.post("/api/palette/dispatch", json={"action": "unregistered:action"})
    assert bad_disp.status_code == 400
    assert bad_disp.json().get("isError") is True

    # Reset profile back to full
    desktop_client.post("/api/palette/dispatch", json={"action": "agent:set_profile", "params": {"profile": "full"}})
