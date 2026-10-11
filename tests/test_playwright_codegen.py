"""
Integration test suite for Hydra Desktop Playwright Action Recorder and Codegen.
Complies with AGENTS.md genome invariants: zero fake tests, real interfaces, exit code 0.
"""

from __future__ import annotations

import pytest

from desktop.playwright_codegen import (
    PlaywrightActionRecorder,
    PlaywrightCodeGenerator,
    RecordedAction,
    ActionType,
    get_action_recorder,
    reset_action_recorder,
)


@pytest.fixture
def recorder():
    rec = PlaywrightActionRecorder()
    yield rec
    rec.clear()


def test_record_actions_sequence(recorder: PlaywrightActionRecorder):
    """Verify recording browser interaction events in sequence."""
    recorder.record_goto("https://hydra.local/login")
    recorder.record_fill("input#username", "sovereign_user")
    recorder.record_fill("input#password", "secret123")
    recorder.record_click("button#submit")
    recorder.record_wait("div.dashboard", timeout_ms=3000)
    recorder.record_screenshot("dashboard.png")

    assert recorder.total_actions == 6
    actions = recorder.get_actions()
    assert actions[0].action_type == ActionType.GOTO
    assert actions[0].value == "https://hydra.local/login"
    assert actions[1].action_type == ActionType.FILL
    assert actions[1].selector == "input#username"
    assert actions[3].action_type == ActionType.CLICK
    assert actions[4].action_type == ActionType.WAIT_FOR_SELECTOR
    assert actions[5].action_type == ActionType.SCREENSHOT


def test_python_sync_script_generation():
    """Verify synchronous Python Playwright script output."""
    rec = PlaywrightActionRecorder()
    rec.record_goto("https://hydra.local")
    rec.record_click("a.nav-link")
    rec.record_press("Enter", selector="input.search")

    script = rec.export_to_python(is_async=False, function_name="test_flow")
    assert "from playwright.sync_api import sync_playwright" in script
    assert "def test_flow():" in script
    assert 'page.goto("https://hydra.local"' in script
    assert 'page.locator("a.nav-link").click()' in script
    assert 'page.locator("input.search").press("Enter")' in script
    assert "browser.close()" in script


def test_python_async_script_generation():
    """Verify asynchronous Python Playwright script output."""
    rec = PlaywrightActionRecorder()
    rec.record_goto("https://hydra.local/editor")
    rec.record_fill("textarea#prompt", "generate system code")
    rec.record_click("button#run")

    script = rec.export_to_python(is_async=True, function_name="run_async_flow")
    assert "from playwright.async_api import async_playwright" in script
    assert "async def run_async_flow():" in script
    assert 'await page.goto("https://hydra.local/editor"' in script
    assert 'await page.locator("textarea#prompt").fill("generate system code")' in script
    assert 'await page.locator("button#run").click()' in script
    assert "await browser.close()" in script


def test_javascript_script_generation():
    """Verify JavaScript Playwright script generation."""
    rec = PlaywrightActionRecorder()
    rec.record_goto("https://hydra.local/settings")
    rec.record_click("button#save")

    js_code = rec.export_to_javascript(function_name="saveSettings")
    assert 'const { chromium } = require("playwright");' in js_code
    assert "async function saveSettings()" in js_code
    assert 'await page.goto("https://hydra.local/settings");' in js_code
    assert 'await page.locator("button#save").click();' in js_code
    assert "saveSettings();" in js_code


def test_json_export_and_import_cycle(recorder: PlaywrightActionRecorder):
    """Verify serialization and roundtrip deserialization of actions to JSON."""
    recorder.record_goto("https://hydra.local")
    recorder.record_click("button#start")

    exported = recorder.export_to_json()
    assert "https://hydra.local" in exported

    new_rec = PlaywrightActionRecorder()
    imported = new_rec.import_from_json(exported)
    assert len(imported) == 2
    assert new_rec.total_actions == 2
    assert new_rec.get_actions()[0].action_type == ActionType.GOTO
    assert new_rec.get_actions()[1].selector == "button#start"


def test_global_singleton_recorder():
    """Verify singleton lifecycle for PlaywrightActionRecorder."""
    r1 = get_action_recorder()
    r2 = get_action_recorder()
    assert r1 is r2

    r3 = reset_action_recorder()
    assert r3 is not r1
    assert get_action_recorder() is r3
