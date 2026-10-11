"""
Deterministic end-to-end integration and security fuzzing test suite for Hydra Desktop and Computer Use.
Complies with AGENTS.md genome invariants: zero fake tests, real interfaces, exit code 0.
"""

import base64
import concurrent.futures
import hashlib
import math
import os
import sys
import threading
import time
import pytest
from fastapi.testclient import TestClient

from hydra_cli.desktop import create_desktop_app, get_desktop_html
from hydra_cli.computer_use import (
    CoordinateBounds,
    OSController,
    PlaywrightAutomationBridge,
    ComputerUseEngine,
    get_computer_use_engine,
    reset_computer_use_engine,
)

PNG_MAGIC = bytes([0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A])


@pytest.fixture(scope="module")
def desktop_client():
    app = create_desktop_app()
    with TestClient(app) as client:
        yield client


def test_e2e_playwright_browser_session_via_fastapi_endpoints(desktop_client):
    """Execute real headless Playwright session through FastAPI desktop endpoint."""
    html_page = (
        "data:text/html,"
        "<!DOCTYPE html><html><head><title>Hydra Automation Lab</title></head>"
        "<body style='height: 2000px; margin: 0; padding: 20px; font-family: sans-serif;'>"
        "<h1>Automated Command Deck</h1>"
        "<form id='config-form'>"
        "  <input id='deck-name' name='deck_name' type='text' value='' />"
        "  <input id='deck-auth' name='deck_auth' type='password' value='' />"
        "  <button id='submit-btn' type='button' onclick='document.title=\"Configured\"'>Apply</button>"
        "</form>"
        "<table id='telemetry-grid' border='1'>"
        "  <thead><tr><th>Node</th><th>Metric</th><th>Status</th></tr></thead>"
        "  <tbody>"
        "    <tr><td>Alpha</td><td>42.5</td><td>Active</td></tr>"
        "    <tr><td>Beta</td><td>99.1</td><td>Syncing</td></tr>"
        "    <tr><td>Gamma</td><td>12.0</td><td>Idle</td></tr>"
        "  </tbody>"
        "</table>"
        "<div style='margin-top: 1200px;' id='footer-target'>Deep Viewport Element</div>"
        "</body></html>"
    )

    # 1. Navigate via browser action endpoint
    nav_res = desktop_client.post("/api/browser/action", json={"action": "browser_navigate", "url": html_page})
    assert nav_res.status_code == 200
    nav_data = nav_res.json()
    assert not nav_data.get("isError")
    assert nav_data.get("title") == "Hydra Automation Lab"

    # 2. Inspect DOM interactive elements
    dom_res = desktop_client.post("/api/browser/action", json={"action": "browser_inspect", "selector": "body"})
    assert dom_res.status_code == 200
    dom_data = dom_res.json()
    assert not dom_data.get("isError")
    assert dom_data.get("count", 0) >= 3

    # 3. Fill form composite primitive
    fill_res = desktop_client.post("/api/browser/action", json={
        "action": "fill_form",
        "fields": {
            "#deck-name": "Sovereign Orion",
            "#deck-auth": "SuperSecretToken99",
        },
        "submit": True,
    })
    assert fill_res.status_code == 200
    fill_data = fill_res.json()
    assert not fill_data.get("isError")
    assert fill_data.get("count") == 2

    # 4. Extract table data composite primitive
    table_res = desktop_client.post("/api/browser/action", json={
        "action": "extract_table_data",
        "selector": "#telemetry-grid",
    })
    assert table_res.status_code == 200
    table_data = table_res.json()
    assert not table_data.get("isError")
    assert table_data.get("row_count") == 3
    assert "Alpha" in table_data.get("rows", [])[0]
    assert "Status" in table_data.get("headers", [])

    # 5. Scroll until visible composite primitive
    scroll_res = desktop_client.post("/api/browser/action", json={
        "action": "scroll_until_visible",
        "selector": "#footer-target",
        "max_scrolls": 5,
        "scroll_step": 400,
    })
    assert scroll_res.status_code == 200
    scroll_data = scroll_res.json()
    assert not scroll_data.get("isError")
    assert scroll_data.get("visible") is True

    # 6. Capture page screenshot and verify PNG bytes and SHA-256
    screen_res = desktop_client.post("/api/browser/action", json={"action": "browser_screenshot"})
    assert screen_res.status_code == 200
    screen_data = screen_res.json()
    assert not screen_data.get("isError")
    raw_img = base64.b64decode(screen_data.get("base64", ""))
    assert raw_img.startswith(PNG_MAGIC)
    assert hashlib.sha256(raw_img).hexdigest() == screen_data.get("sha256")

    # 7. Error containment on non-existent element
    bad_res = desktop_client.post("/api/browser/action", json={
        "action": "extract_table_data",
        "selector": "#non-existent-table",
    })
    assert bad_res.status_code == 200
    bad_data = bad_res.json()
    assert bad_data.get("isError") is True
    assert "not found" in bad_data.get("error", "").lower()


def test_e2e_composite_primitives_direct_execution():
    """Verify composite primitives on ComputerUseEngine directly."""
    engine = ComputerUseEngine()
    try:
        html = (
            "data:text/html,"
            "<html><head><title>Composite Test</title></head><body>"
            "<div id='drag-src' style='width:50px;height:50px;background:red;'>Source</div>"
            "<div id='drag-tgt' style='width:50px;height:50px;background:blue;'>Target</div>"
            "<form id='order-form'>"
            "  <input id='item-qty' type='text' value='' />"
            "  <input id='item-sku' type='text' value='' />"
            "</form>"
            "<table id='matrix'>"
            "  <tr><th>K</th><th>V</th></tr>"
            "  <tr><td>baud</td><td>115200</td></tr>"
            "</table>"
            "</body></html>"
        )
        engine.dispatch("browser_navigate", url=html)

        # Form fill dispatch
        fill_res = engine.dispatch("fill_form", fields={"#item-qty": "50", "#item-sku": "HYD-77"})
        assert not fill_res["isError"]
        assert fill_res["count"] == 2

        # Table extraction dispatch
        tbl_res = engine.dispatch("extract_table_data", selector="#matrix")
        assert not tbl_res["isError"]
        assert tbl_res["row_count"] >= 1

        # Drag and drop dispatch (DOM selectors)
        drag_dom = engine.dispatch("safe_drag_and_drop", source_selector="#drag-src", target_selector="#drag-tgt")
        assert not drag_dom["isError"]
        assert drag_dom["dropped"] is True

        # Drag and drop dispatch (Screen coordinates)
        drag_coord = engine.dispatch("safe_drag_and_drop", from_x=200, from_y=200, to_x=350, to_y=350, steps=2)
        assert not drag_coord["isError"]

        # Window pattern query dispatch
        win_pat = engine.dispatch("find_window", pattern="Hydra.*")
        assert not win_pat["isError"]
    finally:
        engine.browser.close()


def test_coordinate_normalization_across_variable_viewports():
    """Verify coordinate normalization and denormalization across standard resolution viewports."""
    viewports = [
        (1920, 1080),  # Full HD 1080p
        (2560, 1440),  # QHD 1440p
        (3840, 2160),  # 4K UHD
        (1280, 720),   # HD 720p
        (800, 600),    # SVGA
        (1024, 768),   # XGA
    ]

    for width, height in viewports:
        bounds = CoordinateBounds(width=width, height=height, margin=0)

        # Top-left corner
        nx, ny = bounds.normalize(0, 0)
        assert (nx, ny) == (0.0, 0.0)
        px, py = bounds.denormalize(0.0, 0.0)
        assert (px, py) == (0, 0)

        # Bottom-right corner
        nx, ny = bounds.normalize(width, height)
        assert (nx, ny) == (1000.0, 1000.0)
        px, py = bounds.denormalize(1000.0, 1000.0)
        assert (px, py) == (width - 1, height - 1)

        # Dead center
        mid_x, mid_y = width // 2, height // 2
        nx, ny = bounds.normalize(mid_x, mid_y)
        assert abs(nx - 500.0) <= 1.0
        assert abs(ny - 500.0) <= 1.0
        px, py = bounds.denormalize(500.0, 500.0)
        assert abs(px - mid_x) <= 2
        assert abs(py - mid_y) <= 2


def test_security_fuzzing_coordinate_boundary_clamping():
    """Fuzz coordinate inputs with negative, out-of-range, NaN, infinity, and invalid types."""
    bounds = CoordinateBounds(width=1920, height=1080, margin=15, enable_failsafe=True)

    fuzz_inputs = [
        (-500, -200),
        (50000, 80000),
        (float("nan"), float("nan")),
        (float("inf"), float("-inf")),
        (123.456, 987.654),
        (None, None),
        ("string", {}),
    ]

    for fx, fy in fuzz_inputs:
        # Clamping fuzz: must not throw, must return valid integers within [margin, dim - 1 - margin]
        cx, cy, clipped = bounds.clip(fx, fy)
        assert isinstance(cx, int)
        assert isinstance(cy, int)
        assert 15 <= cx <= 1920 - 1 - 15
        assert 15 <= cy <= 1080 - 1 - 15

        # Normalization fuzz: must return floats within [0.0, 1000.0]
        nx, ny = bounds.normalize(fx, fy)
        assert isinstance(nx, float)
        assert isinstance(ny, float)
        assert 0.0 <= nx <= 1000.0
        assert 0.0 <= ny <= 1000.0

        # Denormalization fuzz: must return screen coordinates within bounds
        px, py = bounds.denormalize(fx, fy)
        assert isinstance(px, int)
        assert isinstance(py, int)
        assert 15 <= px <= 1920 - 1 - 15
        assert 15 <= py <= 1080 - 1 - 15

        # Safety check fuzz: must return boolean and optional string without crashing
        safe, reason = bounds.check_safety(fx, fy)
        assert isinstance(safe, bool)


def test_security_fuzzing_obscured_ssrf_encodings():
    """Test SSRF rejection across obscured host encodings (decimal, hex, IPv6-mapped, domains)."""
    bridge = PlaywrightAutomationBridge(headless=True)
    try:
        obscured_vectors = [
            "http://169.254.169.254/latest/meta-data/",
            "http://2852039166/latest/meta-data/",
            "http://0xa9fea9fe/latest/meta-data/",
            "http://[::ffff:169.254.169.254]/meta",
            "http://[::ffff:a9fe:a9fe]/meta",
            "http://metadata.google.internal/computeMetadata/v1/",
            "http://instance-data/latest/",
            "http://%31%36%39%2e%32%35%34%2e%31%36%39%2e%32%35%34/",
        ]
        for url in obscured_vectors:
            res = bridge.navigate(url)
            assert res["isError"] is True, f"Failed to block SSRF vector: {url}"
            assert "Security violation" in res["error"], f"Incorrect error message for {url}: {res['error']}"
    finally:
        bridge.close()


def test_stress_concurrency_rapid_event_bursts_and_failsafe():
    """Execute concurrent rapid bursts against OSController verifying thread safety and failsafe trips."""
    bounds = CoordinateBounds(width=1920, height=1080, enable_failsafe=True)
    controller = OSController(bounds=bounds)

    def worker_action(thread_idx: int):
        for step in range(10):
            x = 50 + (thread_idx * 100 + step * 20) % 1800
            y = 50 + (thread_idx * 50 + step * 30) % 950
            res = controller.mouse_move(x, y, smooth=False)
            assert not res.get("isError")
            # Rapid scroll
            controller.mouse_scroll(dx=0, dy=-10)

    # Launch 8 concurrent threads firing bursts
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(worker_action, i) for i in range(8)]
        for f in concurrent.futures.as_completed(futures):
            f.result()

    # Trigger failsafe corner (0, 0)
    failsafe_res = controller.mouse_move(0, 0, smooth=False)
    assert failsafe_res["isError"] is True
    assert "failsafe" in failsafe_res["error"].lower()

    # Verify telemetry
    telemetry = bounds.get_telemetry()
    assert telemetry["total_checks"] >= 80
    assert telemetry["failsafe_trips"] >= 1


def test_desktop_ui_canvas_inspector_and_multi_session_tabs():
    """Verify web desk UI contains coordinate inspector readout and tab structure."""
    html = get_desktop_html()
    assert "coord-readout" in html
    assert "updateCoord(event)" in html
    assert "screen-preview" in html
    assert "drawer-tabs" in html
    assert "tab-screen" in html
    assert "tab-term" in html