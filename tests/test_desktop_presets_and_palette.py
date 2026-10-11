"""
Deterministic integration test suite for Hydra Desktop Tool Preset Profiles,
Command Palette Modal, and Markdown Execution Report Export.
Complies with AGENTS.md genome invariants: zero fake tests, real interfaces, exit code 0.
"""

import json
import pytest
from fastapi.testclient import TestClient

from hydra_cli.agent_runner import (
    AutonomousAgentRunner,
    TOOL_PROFILES,
    is_tool_allowed_under_profile,
    get_agent_runner,
    reset_agent_runner,
)
from hydra_cli.desktop import (
    create_desktop_app,
    get_desktop_html,
    COMMAND_PALETTE_ACTIONS,
    dispatch_command_palette_action,
)


@pytest.fixture(scope="module")
def desktop_client():
    app = create_desktop_app()
    with TestClient(app) as client:
        yield client


def test_tool_profiles_catalog_structure():
    """Verify tool preset profiles exist and declare required constraints."""
    required_profiles = ["full_automation", "browser_only", "workspace_only", "minimal"]
    for prof in required_profiles:
        assert prof in TOOL_PROFILES, f"Missing required tool profile: {prof}"
        spec = TOOL_PROFILES[prof]
        assert "description" in spec
        assert "allowed_categories" in spec
        assert isinstance(spec["allowed_categories"], list)

    # Aliases
    assert "full" in TOOL_PROFILES
    assert "readonly" in TOOL_PROFILES


def test_agent_runner_dynamic_profile_switching():
    """Test dynamic switching of tool profiles and state reflection."""
    runner = AutonomousAgentRunner(tool_profile="full_automation")
    assert runner.get_tool_profile() == "full_automation"

    events = []
    runner.subscribe(lambda e: events.append(e))

    res = runner.set_tool_profile("browser_only")
    assert not res.get("isError")
    assert res.get("profile") == "browser_only"
    assert runner.get_tool_profile() == "browser_only"

    # Profile changed event broadcast
    assert any(e.get("event") == "profile_changed" and e.get("data", {}).get("profile") == "browser_only" for e in events)

    # Invalid profile rejected
    bad_res = runner.set_tool_profile("non_existent_profile")
    assert bad_res.get("isError") is True
    assert runner.get_tool_profile() == "browser_only"

    # Minimal profile switch
    min_res = runner.set_tool_profile("minimal")
    assert not min_res.get("isError")
    assert runner.get_tool_profile() == "minimal"


def test_tool_filtering_under_active_profiles():
    """Test strict filtering and enforcement of actions under different profiles."""
    runner = AutonomousAgentRunner()

    # 1. Full automation allows browser and workspace
    runner.set_tool_profile("full_automation")
    ok_b, _ = is_tool_allowed_under_profile("browser_navigate", "full_automation")
    ok_w, _ = is_tool_allowed_under_profile("read_file", "full_automation")
    ok_o, _ = is_tool_allowed_under_profile("mouse_click", "full_automation")
    assert ok_b is True and ok_w is True and ok_o is True

    # 2. Browser only allows browser actions, rejects workspace and OS mutations
    runner.set_tool_profile("browser_only")
    ok_b, _ = is_tool_allowed_under_profile("browser_navigate", "browser_only")
    assert ok_b is True
    ok_w, err_w = is_tool_allowed_under_profile("write_file", "browser_only")
    assert ok_w is False
    assert "forbidden under profile 'browser_only'" in err_w
    ok_o, err_o = is_tool_allowed_under_profile("mouse_click", "browser_only")
    assert ok_o is False

    # Execution step rejection under browser_only
    step_res = runner.execute_step("write_file", path="test.txt", content="payload")
    assert step_res.get("isError") is True
    assert step_res.get("profile_violation") is True
    assert "forbidden" in step_res.get("error", "")

    # 3. Workspace only allows workspace actions, rejects browser navigation
    runner.set_tool_profile("workspace_only")
    ok_w, _ = is_tool_allowed_under_profile("read_file", "workspace_only")
    assert ok_w is True
    ok_b, err_b = is_tool_allowed_under_profile("browser_navigate", "workspace_only")
    assert ok_b is False
    assert "forbidden under profile 'workspace_only'" in err_b

    step_res_b = runner.execute_step("browser_navigate", url="http://localhost")
    assert step_res_b.get("isError") is True
    assert step_res_b.get("profile_violation") is True

    # 4. Minimal profile permits inspection and read-only, rejects mutations
    runner.set_tool_profile("minimal")
    ok_read, _ = is_tool_allowed_under_profile("read_file", "minimal")
    assert ok_read is True
    ok_scr, _ = is_tool_allowed_under_profile("screen_capture", "minimal")
    assert ok_scr is True
    ok_write, err_m = is_tool_allowed_under_profile("write_file", "minimal")
    assert ok_write is False
    assert "forbidden" in err_m


def test_markdown_report_generation():
    """Test generating comprehensive Markdown execution report from agent trace."""
    runner = AutonomousAgentRunner(max_steps=5, tool_profile="full_automation")
    steps = [
        {"action": "browser_inspect", "selector": "body"},
        {"action": "window_action", "sub_action": "active"},
    ]
    runner.run_task("Telemetry inspection workflow", steps=steps)

    report = runner.export_markdown_report()
    assert isinstance(report, str)
    assert "# Hydra" in report
    assert "Session Overview" in report
    assert "Telemetry inspection workflow" in report
    assert "completed" in report.lower()
    assert "full_automation" in report
    assert "Step Chronology" in report or "Execution Trace Breakdown" in report
    assert "browser_inspect" in report
    assert "window_action" in report


def test_desktop_profile_and_markdown_endpoints(desktop_client: TestClient):
    """Test FastAPI endpoints for tool profiles, markdown report, and command palette."""
    # 1. GET /api/agent/profiles
    res_prof = desktop_client.get("/api/agent/profiles")
    assert res_prof.status_code == 200
    pdata = res_prof.json()
    assert "profiles" in pdata
    assert "full_automation" in pdata["profiles"]
    assert "browser_only" in pdata["profiles"]
    assert "workspace_only" in pdata["profiles"]
    assert "minimal" in pdata["profiles"]

    # 2. POST /api/agent/profile
    res_set = desktop_client.post("/api/agent/profile", json={"profile": "workspace_only"})
    assert res_set.status_code == 200
    assert res_set.json().get("profile") == "workspace_only"

    # Reset
    desktop_client.post("/api/agent/profile", json={"profile": "full_automation"})

    # 3. GET /api/agent/export/markdown
    res_md = desktop_client.get("/api/agent/export/markdown")
    assert res_md.status_code == 200
    assert "text/markdown" in res_md.headers.get("content-type", "")
    assert "# Hydra" in res_md.text

    # 4. GET /api/agent/export/markdown?download=true
    res_dl = desktop_client.get("/api/agent/export/markdown?download=true")
    assert res_dl.status_code == 200
    assert "attachment" in res_dl.headers.get("content-disposition", "")
    assert "agent-session-report-" in res_dl.headers.get("content-disposition", "")

    # 5. GET /api/palette/actions
    res_palette = desktop_client.get("/api/palette/actions")
    assert res_palette.status_code == 200
    palette_data = res_palette.json()
    assert palette_data.get("count", 0) > 0
    assert "actions" in palette_data

    # 6. POST /api/palette/dispatch
    res_disp = desktop_client.post("/api/palette/dispatch", json={"action": "token:summary"})
    assert res_disp.status_code == 200
    assert res_disp.json().get("dispatched") == "token:summary"


def test_websocket_profile_updates(desktop_client: TestClient):
    """Test WebSocket bi-directional profile query and update handling."""
    with desktop_client.websocket_connect("/ws/desktop") as ws:
        # Get profiles catalog
        ws.send_text(json.dumps({"action": "get_profiles"}))
        resp_cat = json.loads(ws.receive_text())
        assert resp_cat.get("event") == "profiles_catalog"
        assert "full_automation" in resp_cat.get("profiles", [])

        # Set profile
        ws.send_text(json.dumps({"action": "set_profile", "profile": "browser_only"}))
        resp_set = json.loads(ws.receive_text())
        assert resp_set.get("event") == "profile_updated"
        assert resp_set.get("data", {}).get("profile") == "browser_only"

        # Reset back
        ws.send_text(json.dumps({"action": "set_profile", "profile": "full_automation"}))
        ws.receive_text()


def test_command_palette_html_and_ui_bindings():
    """Test HTML structure includes command palette modal, hotkey bindings, and profile selector."""
    html = get_desktop_html()

    # Command palette modal elements
    assert "command-palette-modal" in html
    assert "palette-input" in html
    assert "palette-results" in html
    assert "openCommandPalette" in html
    assert "closeCommandPalette" in html
    assert "toggleCommandPalette" in html
    assert "PALETTE_ACTIONS" in html

    # Tool profile selector dropdown
    assert "agent-tool-profile" in html
    assert "full_automation" in html
    assert "browser_only" in html
    assert "workspace_only" in html
    assert "minimal" in html
    assert "updateAgentToolProfile" in html

    # Export markdown button
    assert "btn-agent-export-markdown" in html
    assert "exportAgentMarkdown" in html
    assert "/api/agent/export/markdown" in html

    # Keyboard shortcut listener
    assert "ctrlKey" in html or "metaKey" in html
    assert "key.toLowerCase() === 'k'" in html
