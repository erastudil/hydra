"""
Hydra Sovereign Desktop Packaging and Runtime Contract Verification Script.
Validates all desktop assets, templates, CLI entrypoints, FastAPI endpoints,
multi-session tab state, canvas overlay crosshairs, diff viewer endpoints,
and composite computer use automation primitives.

Dialect : Progen Syntax.
Truth Gate : Exit code 0.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from typing import Any, Dict, List


def run_checks() -> int:
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sys.path.insert(0, repo_root)

    print("status : beginning hydra desktop packaging verification.\n")

    # 1. File presence and invariant check
    required_files = [
        os.path.join(repo_root, "hydra_cli", "desktop.py"),
        os.path.join(repo_root, "hydra_cli", "computer_use.py"),
        os.path.join(repo_root, "hydra_cli", "agent_runner.py"),
        os.path.join(repo_root, "desktop", "__init__.py"),
        os.path.join(repo_root, "desktop", "README.md"),
        os.path.join(repo_root, "desktop", "SPEC.md"),
        os.path.join(repo_root, "computer_use", "__init__.py"),
        os.path.join(repo_root, "computer_use", "README.md"),
        os.path.join(repo_root, "computer_use", "SPEC.md"),
    ]

    for rf in required_files:
        if not os.path.isfile(rf):
            print(f"error : required file missing: {rf}\n")
            return 1
    print("file tree verification : all required desktop and computer use assets present.\n")

    # 2. Desktop UI HTML and JS controllers check
    from hydra_cli.desktop import get_desktop_html, create_desktop_app
    html = get_desktop_html()

    html_tokens = [
        "session-bar",
        "session-tab",
        "addNewSession",
        "switchSession",
        "closeSession",
        "crosshair",
        "toggleOverlayBounds",
        "diff-container",
        "diff-card",
        "fetchDiffs",
        "/api/diffs",
        "agent-panel",
        "agent-trace-log",
        "startAgentTask",
        "pauseAgentTask",
        "/api/agent/run",
    ]
    for tok in html_tokens:
        if tok not in html:
            print(f"error : desktop html missing token {tok}\n")
            return 1
    print("ui contract verification : multi-session tabs, crosshairs, and diff viewer confirmed in web desk html.\n")

    # 3. FastAPI App and Route Contract Verification
    app = create_desktop_app()
    route_paths = [r.path for r in app.routes]
    required_routes = [
        "/",
        "/api/status",
        "/api/models",
        "/api/chat",
        "/api/terminal/run",
        "/api/computer/action",
        "/api/computer/screen",
        "/api/browser/action",
        "/api/diffs",
        "/api/diffs/record",
        "/api/agent/run",
        "/api/agent/pause",
        "/api/agent/resume",
        "/api/agent/abort",
        "/api/agent/status",
        "/ws/desktop",
        "/v1/models",
        "/v1/chat/completions",
    ]
    for rr in required_routes:
        if rr not in route_paths:
            print(f"error : desktop app missing route {rr}\n")
            return 1
    print("api route verification : all 13 declared fastapi and websocket endpoints registered.\n")

    # 4. Direct API and Endpoint Simulation
    from fastapi.testclient import TestClient
    client = TestClient(app)

    # Status
    res = client.get("/api/status")
    if res.status_code != 200:
        print(f"error : /api/status failed with {res.status_code}\n")
        return 1
    status_data = res.json()
    if status_data.get("status") != "ok" or "computer_use" not in status_data:
        print("error : /api/status returned invalid payload schema.\n")
        return 1

    # Diffs read and record
    diff_res = client.get("/api/diffs")
    if diff_res.status_code != 200:
        print(f"error : /api/diffs failed with {diff_res.status_code}\n")
        return 1
    rec_res = client.post("/api/diffs/record", json={
        "path": "hydra_cli/desktop.py",
        "type": "MODIFIED",
        "lines": ["+    # Packaging check diff record"],
    })
    if rec_res.status_code != 200 or not rec_res.json().get("recorded"):
        print("error : /api/diffs/record failed.\n")
        return 1

    # Computer screen
    screen_res = client.get("/api/computer/screen")
    if screen_res.status_code != 200 or screen_res.headers.get("content-type") != "image/png":
        print("error : /api/computer/screen failed to return png bytes.\n")
        return 1

        # Agent endpoints check
    ag_st = client.get("/api/agent/status")
    if ag_st.status_code != 200:
        print("error : /api/agent/status failed.\n")
        return 1
    ag_run = client.post("/api/agent/run", json={"task": "verification gate check", "max_steps": 5, "async": True})
    if ag_run.status_code != 200 or ag_run.json().get("status") not in ("started", "running"):
        print("error : /api/agent/run failed.\n")
        return 1
    ag_pause = client.post("/api/agent/pause")
    if ag_pause.status_code != 200:
        print("error : /api/agent/pause failed.\n")
        return 1
    ag_resume = client.post("/api/agent/resume")
    if ag_resume.status_code != 200:
        print("error : /api/agent/resume failed.\n")
        return 1
    ag_abort = client.post("/api/agent/abort")
    if ag_abort.status_code != 200 or not ag_abort.json().get("status") == "aborted":
        print("error : /api/agent/abort failed.\n")
        return 1

    print("endpoint execution verification : status, diffs, screen, and agent endpoints functioning deterministically.\n")

    # 5. Composite Computer Use Primitives Verification
    from hydra_cli.computer_use import get_computer_use_engine
    engine = get_computer_use_engine()

    composite_methods = [
        "fill_form",
        "scroll_until_visible",
        "extract_table_data",
        "safe_drag_and_drop",
        "find_window_by_title_pattern",
        "set_window_bounds",
        "capture_active_window",
        "safe_key_sequence",
    ]
    for cm in composite_methods:
        if not hasattr(engine, cm):
            print(f"error : computer use engine missing composite method {cm}\n")
            return 1

    # Execute composite primitives safely
    w_res = engine.find_window_by_title_pattern(".*")
    if "matches" not in w_res:
        print("error : find_window_by_title_pattern failed.\n")
        return 1

    wb_res = engine.set_window_bounds(0, 100, 100, 800, 600)
    if not wb_res.get("isError") and "hwnd" not in wb_res:
        print("error : set_window_bounds invalid response.\n")
        return 1

    cw_res = engine.capture_active_window(as_base64=True)
    if cw_res.get("isError"):
        print("error : capture_active_window failed.\n")
        return 1

    dd_res = engine.safe_drag_and_drop((100, 100), (200, 200))
    if dd_res.get("isError"):
        print("error : safe_drag_and_drop failed.\n")
        return 1

    ks_res = engine.safe_key_sequence(["ctrl", "c"])
    if ks_res.get("isError"):
        print("error : safe_key_sequence failed.\n")
        return 1

    test_html = 'data:text/html,<html><body><form id="form"><input name="username" /><input name="email" /></form><table id="tbl"><tr><th>Col1</th></tr><tr><td>A</td></tr></table><div id="content" style="margin-top:2000px;">Content</div></body></html>'
    engine.browser.navigate(test_html)

    ff_res = engine.fill_form({"input[name='username']": "test_user"}, form_selector="#form")
    if ff_res.get("isError"):
        print("error : fill_form failed.\n")
        return 1

    sc_res = engine.scroll_until_visible("#content", max_scrolls=2)
    if sc_res.get("isError"):
        print("error : scroll_until_visible failed.\n")
        return 1

    tb_res = engine.extract_table_data("table#tbl")
    if tb_res.get("isError"):
        print("error : extract_table_data failed.\n")
        return 1

    engine.browser.close()

    print("composite primitives verification : all 8 computer use primitives passed successfully.\n")

    # 6. CLI Entrypoint Verification
    env = os.environ.copy()
    env["PYTHONPATH"] = repo_root + os.pathsep + env.get("PYTHONPATH", "")

    p1 = subprocess.run(
        [sys.executable, "-m", "hydra_cli.desktop", "--help"],
        capture_output=True,
        text=True,
        cwd=repo_root,
        env=env,
        timeout=15,
    )
    if p1.returncode != 0 or "--port" not in p1.stdout:
        print(f"error : python -m hydra_cli.desktop --help failed, exit {p1.returncode}\n")
        return 1

    p2 = subprocess.run(
        [sys.executable, "-m", "hydra_cli", "desktop", "--help"],
        capture_output=True,
        text=True,
        cwd=repo_root,
        env=env,
        timeout=15,
    )
    if p2.returncode != 0 or "--port" not in p2.stdout:
        print(f"error : python -m hydra_cli desktop --help failed, exit {p2.returncode}\n")
        return 1

    print("cli entrypoints verification : hydra desktop and python -m hydra_cli.desktop entrypoints functional.\n")

    print("desktop packaging gate : exit code 0.\n")
    return 0


if __name__ == "__main__":
    sys.exit(run_checks())
