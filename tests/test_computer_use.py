"""
Deterministic verification test suite for Hydra Computer Use and Desktop Automation.
Complies with AGENTS.md genome invariants: zero fake tests, real interfaces, exit code 0.
"""

import base64
import hashlib
import os
import sys
import tempfile
import pytest

from hydra_cli.computer_use import (
    CoordinateBounds,
    OSController,
    ScreenCaptureEngine,
    PlaywrightAutomationBridge,
    ComputerUseEngine,
    get_computer_use_engine,
    reset_computer_use_engine,
)
from hydra_cli.native_tools import NativeToolRegistry

PNG_MAGIC = bytes([0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A])


def test_coordinate_bounds_clamping_and_normalization():
    bounds = CoordinateBounds(width=1920, height=1080, margin=10)

    # Boundary clamping
    cx, cy, clipped = bounds.clip(500, 500)
    assert not clipped
    assert (cx, cy) == (500, 500)

    # Clamping out-of-bounds negative coordinates
    cx, cy, clipped = bounds.clip(-100, -50)
    assert clipped
    assert (cx, cy) == (10, 10)

    # Clamping out-of-bounds positive coordinates
    cx, cy, clipped = bounds.clip(3000, 2500)
    assert clipped
    assert (cx, cy) == (1909, 1069)

    # Normalization to [0.0, 1000.0] grid
    nx, ny = bounds.normalize(960, 540)
    assert nx == 500.0
    assert ny == 500.0

    # Denormalization from [0.0, 1000.0] grid to screen pixels
    px, py = bounds.denormalize(500.0, 500.0)
    assert px == 960
    assert py == 540


def test_coordinate_bounds_fences_and_failsafe():
    bounds = CoordinateBounds(width=1920, height=1080, margin=0, enable_failsafe=True)

    # Failsafe corner (0, 0)
    safe, reason = bounds.check_safety(0, 0)
    assert not safe
    assert "failsafe" in reason.lower()

    # Safe coordinate
    safe, reason = bounds.check_safety(500, 500)
    assert safe
    assert reason is None

    # Register restricted fence
    bounds.add_fence("credentials_vault", 200, 200, 400, 400)
    assert "credentials_vault" in bounds.list_fences()

    # Target within fence
    safe, reason = bounds.check_safety(300, 300)
    assert not safe
    assert "credentials_vault" in reason

    # Remove fence
    removed = bounds.remove_fence("credentials_vault")
    assert removed
    safe, reason = bounds.check_safety(300, 300)
    assert safe


def test_os_controller_screen_and_cursor():
    bounds = CoordinateBounds(width=1920, height=1080)
    controller = OSController(bounds=bounds)

    w, h = controller.get_screen_size()
    assert w > 0 and h > 0

    cur_x, cur_y = controller.get_cursor_position()
    assert isinstance(cur_x, int) and isinstance(cur_y, int)


def test_os_controller_mouse_and_key_actions():
    bounds = CoordinateBounds(width=1920, height=1080)
    controller = OSController(bounds=bounds)

    # Mouse move
    res_move = controller.mouse_move(400, 300, smooth=False)
    assert not res_move["isError"]
    assert res_move["x"] == 400
    assert res_move["y"] == 300

    # Mouse click
    res_click = controller.mouse_click(400, 300, button="left")
    assert not res_click["isError"]
    assert res_click["x"] == 400

    # Mouse drag
    res_drag = controller.mouse_drag(100, 100, 200, 200, steps=2)
    assert not res_drag["isError"]
    assert res_drag["start"] == (100, 100)
    assert res_drag["end"] == (200, 200)

    # Mouse scroll
    res_scroll = controller.mouse_scroll(dx=0, dy=-120)
    assert not res_scroll["isError"]

    # Key press
    res_key = controller.key_press("enter")
    assert not res_key["isError"]
    assert res_key["key"] == "enter"

    # Key chord
    res_chord = controller.key_chord("ctrl+c")
    assert not res_chord["isError"]
    assert res_chord["chord"] == "ctrl+c"

    # Type text
    res_type = controller.type_text("hydra automation", delay_ms=1.0)
    assert not res_type["isError"]
    assert res_type["text_length"] == len("hydra automation")


def test_os_controller_window_management_and_tampering_guard():
    controller = OSController()

    win = controller.get_active_window()
    assert "hwnd" in win
    assert "title" in win

    windows = controller.list_windows()
    assert isinstance(windows, list)
    assert len(windows) > 0

    # Safe focus
    res_focus = controller.focus_window("Hydra Desktop Shell")
    assert not res_focus["isError"]

    # Restricted window focus tampering guard
    for target in ("1Password", "Bitwarden", "Windows Security", "Task Manager", "regedit"):
        blocked = controller.focus_window(target)
        assert blocked["isError"]
        assert "Security violation" in blocked["error"]


def test_screen_capture_engine_png_format_and_sha256():
    bounds = CoordinateBounds(width=800, height=600)
    engine = ScreenCaptureEngine(bounds=bounds)

    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        tmp_path = tmp.name

    try:
        res = engine.capture(save_path=tmp_path, as_base64=True)
        assert not res["isError"]
        assert res["format"] == "PNG"
        assert res["size_bytes"] > 0
        assert os.path.isfile(tmp_path)
        assert os.path.getsize(tmp_path) == res["size_bytes"]

        raw_bytes = base64.b64decode(res["base64"])
        # Validate PNG magic bytes header
        assert raw_bytes.startswith(PNG_MAGIC)
        # Validate SHA-256 integrity
        assert hashlib.sha256(raw_bytes).hexdigest() == res["sha256"]
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


def test_playwright_automation_navigation_dom_and_pdf():
    bridge = PlaywrightAutomationBridge(headless=True)
    try:
        html = (
            "data:text/html,"
            "<!DOCTYPE html><html><head><title>Hydra Test Desk</title></head>"
            "<body>"
            "<h1>Control Deck</h1>"
            "<input id='target-field' type='text' value='' />"
            "<button id='action-btn' onclick='document.title=\"Clicked!\"'>Execute</button>"
            "</body></html>"
        )
        nav = bridge.navigate(html)
        assert not nav["isError"]
        assert nav["title"] == "Hydra Test Desk"

        dom = bridge.inspect_dom("body")
        assert not dom["isError"]
        assert dom["count"] >= 2
        tags = [el["tag"] for el in dom["elements"]]
        assert "button" in tags
        assert "input" in tags

        type_res = bridge.type_element("#target-field", "sovereign command")
        assert not type_res["isError"]
        assert type_res["typed_length"] == len("sovereign command")

        click_res = bridge.click_element("#action-btn")
        assert not click_res["isError"]

        screenshot = bridge.capture_screenshot()
        assert not screenshot["isError"]
        assert screenshot["size_bytes"] > 0
        img_bytes = base64.b64decode(screenshot["base64"])
        assert img_bytes.startswith(PNG_MAGIC)

        pdf_res = bridge.print_pdf()
        assert not pdf_res["isError"]
        assert pdf_res["size_bytes"] > 0

        logs = bridge.get_network_logs()
        assert not logs["isError"]
        assert isinstance(logs["logs"], list)
    finally:
        bridge.close()


def test_playwright_automation_ssrf_blocking():
    bridge = PlaywrightAutomationBridge(headless=True)
    try:
        blocked_targets = (
            "http://169.254.169.254/latest/meta-data/",
            "http://metadata.google.internal/computeMetadata/v1/",
            "http://169.254.1.1/secret",
            "http://instance-data/latest/",
        )
        for url in blocked_targets:
            res = bridge.navigate(url)
            assert res["isError"]
            assert "Security violation" in res["error"]
    finally:
        bridge.close()


def test_computer_use_engine_unified_dispatch_and_dangerous_command_guard():
    engine = ComputerUseEngine()
    try:
        # Screen capture dispatch
        res_cap = engine.dispatch("screen_capture")
        assert not res_cap["isError"]
        assert res_cap["format"] == "PNG"

        # Normalization and denormalization dispatch
        norm = engine.dispatch("normalize_coordinates", x=960, y=540)
        assert not norm["isError"]
        assert norm["norm_x"] == 500.0

        denorm = engine.dispatch("denormalize_coordinates", norm_x=500.0, norm_y=500.0)
        assert not denorm["isError"]
        assert denorm["pixel_x"] == 960

        # Safe typing dispatch
        safe_type = engine.dispatch("type_text", text="git status")
        assert not safe_type["isError"]

        # Dangerous command typing guards
        for bad_cmd in (
            "rm -rf /",
            "rm -fr *",
            "del /f /s /q *",
            "Remove-Item -Recurse -Force C:\\",
        ):
            blocked = engine.dispatch("type_text", text=bad_cmd)
            assert blocked["isError"]
            assert "Security violation" in blocked["error"]

        # Fence management dispatch
        fence_add = engine.dispatch("add_fence", label="quarantine", x1=50, y1=50, x2=150, y2=150)
        assert not fence_add["isError"]
        assert "quarantine" in fence_add["fences"]

        fence_rem = engine.dispatch("remove_fence", label="quarantine")
        assert not fence_rem["isError"]
        assert fence_rem["removed"]

        # Telemetry
        telem = engine.get_telemetry()
        assert telem["actions_executed"] > 0
    finally:
        engine.browser.close()


def test_native_tool_registry_computer_use_tool_catalog():
    registry = NativeToolRegistry()
    registered_tools = (
        "computer_screen_capture",
        "computer_mouse_click",
        "computer_mouse_move",
        "computer_mouse_drag",
        "computer_mouse_scroll",
        "computer_key_press",
        "computer_key_chord",
        "computer_type_text",
        "computer_window_action",
        "computer_dispatch_action",
        "browser_pdf_print",
        "browser_network_inspect",
    )
    for tool_name in registered_tools:
        assert registry.has_tool(tool_name), f"Missing registered tool: {tool_name}"

    openai_tools = registry.get_openai_tools()
    tool_names = [t["function"]["name"] for t in openai_tools]
    expected_openai_tools = (
        "browser_action",
        "computer_screen_capture",
        "computer_mouse_click",
        "computer_mouse_move",
        "computer_mouse_drag",
        "computer_type_text",
        "computer_key_press",
        "computer_window_action",
        "browser_pdf_print",
        "browser_network_inspect",
    )
    for tool_name in expected_openai_tools:
        assert tool_name in tool_names, f"Missing OpenAI tool schema: {tool_name}"