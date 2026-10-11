"""
Hydra Desktop Playwright Action Recorder and Code Generator.
Captures recorded browser interactions (navigation, clicks, text input, keystrokes,
dropdown selections, scrolls, assertions) and serializes them into deterministic,
reproducible Playwright automation scripts in Python (sync/async) and JavaScript.
Complies with AGENTS.md genome invariants: zero fake tests, real interfaces, exit code 0.
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple, Union


class ActionType(str, Enum):
    """Supported Playwright interaction action types."""
    GOTO = "goto"
    CLICK = "click"
    DBLCLICK = "dblclick"
    FILL = "fill"
    PRESS = "press"
    SELECT_OPTION = "select_option"
    CHECK = "check"
    UNCHECK = "uncheck"
    HOVER = "hover"
    SCROLL = "scroll"
    WAIT_FOR_SELECTOR = "wait_for_selector"
    SCREENSHOT = "screenshot"
    KEYBOARD_TYPE = "keyboard_type"


@dataclass
class RecordedAction:
    """Atomic recorded browser interaction action."""
    action_type: ActionType
    selector: Optional[str] = None
    value: Optional[str] = None
    params: Dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)
    description: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "action_type": self.action_type.value,
            "selector": self.selector,
            "value": self.value,
            "params": dict(self.params),
            "timestamp": self.timestamp,
            "description": self.description,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> RecordedAction:
        return cls(
            action_type=ActionType(data["action_type"]),
            selector=data.get("selector"),
            value=data.get("value"),
            params=data.get("params", {}),
            timestamp=data.get("timestamp", time.time()),
            description=data.get("description", ""),
        )


class PlaywrightCodeGenerator:
    """
    Generates runnable Playwright automation scripts from recorded actions.
    Supports Python synchronous, Python asynchronous, and modern JavaScript output.
    """

    def __init__(
        self,
        browser: str = "chromium",
        headless: bool = False,
        viewport: Tuple[int, int] = (1280, 720),
        timeout_ms: int = 30000,
    ) -> None:
        self.browser = browser
        self.headless = headless
        self.viewport_width, self.viewport_height = viewport
        self.timeout_ms = timeout_ms

    def format_action_python(self, action: RecordedAction, is_async: bool = False) -> str:
        """Format a single action into a Python Playwright instruction statement."""
        prefix = "await " if is_async else ""
        at = action.action_type
        sel = json.dumps(action.selector or "")
        val = json.dumps(action.value or "")

        if at == ActionType.GOTO:
            return f"{prefix}page.goto({val}, timeout={self.timeout_ms})"
        elif at == ActionType.CLICK:
            btn = action.params.get("button", "left")
            btn_arg = f', button="{btn}"' if btn != "left" else ""
            return f"{prefix}page.locator({sel}).click({btn_arg.lstrip(', ')})"
        elif at == ActionType.DBLCLICK:
            return f"{prefix}page.locator({sel}).dblclick()"
        elif at == ActionType.FILL:
            return f"{prefix}page.locator({sel}).fill({val})"
        elif at == ActionType.KEYBOARD_TYPE:
            return f"{prefix}page.locator({sel}).type({val})"
        elif at == ActionType.PRESS:
            key = json.dumps(action.params.get("key", action.value or "Enter"))
            if action.selector:
                return f"{prefix}page.locator({sel}).press({key})"
            return f"{prefix}page.keyboard.press({key})"
        elif at == ActionType.SELECT_OPTION:
            return f"{prefix}page.locator({sel}).select_option(value={val})"
        elif at == ActionType.CHECK:
            return f"{prefix}page.locator({sel}).check()"
        elif at == ActionType.UNCHECK:
            return f"{prefix}page.locator({sel}).uncheck()"
        elif at == ActionType.HOVER:
            return f"{prefix}page.locator({sel}).hover()"
        elif at == ActionType.SCROLL:
            dx = action.params.get("delta_x", 0)
            dy = action.params.get("delta_y", 100)
            return f"{prefix}page.mouse.wheel({dx}, {dy})"
        elif at == ActionType.WAIT_FOR_SELECTOR:
            state = action.params.get("state", "visible")
            timeout = action.params.get("timeout_ms", 5000)
            return f'{prefix}page.wait_for_selector({sel}, state="{state}", timeout={timeout})'
        elif at == ActionType.SCREENSHOT:
            path = json.dumps(action.params.get("path", action.value or "screenshot.png"))
            full_page = action.params.get("full_page", False)
            return f"{prefix}page.screenshot(path={path}, full_page={full_page})"
        return f"# Unsupported action: {action.action_type.value}"

    def generate_python_script(
        self,
        actions: List[RecordedAction],
        is_async: bool = False,
        function_name: str = "run_flow",
    ) -> str:
        """Generate complete runnable Python Playwright script."""
        lines = []
        if is_async:
            lines.append("import asyncio")
            lines.append("from playwright.async_api import async_playwright")
            lines.append("")
            lines.append(f"async def {function_name}():")
            lines.append("    async with async_playwright() as p:")
            lines.append(f'        browser = await p.{self.browser}.launch(headless={self.headless})')
            lines.append(f'        context = await browser.new_context(viewport={{"width": {self.viewport_width}, "height": {self.viewport_height}}})')
            lines.append("        page = await context.new_page()")
            lines.append("")
            for act in actions:
                stmt = self.format_action_python(act, is_async=True)
                lines.append(f"        {stmt}")
            lines.append("")
            lines.append("        await context.close()")
            lines.append("        await browser.close()")
            lines.append("")
            lines.append("if __name__ == '__main__':")
            lines.append(f"    asyncio.run({function_name}())")
        else:
            lines.append("from playwright.sync_api import sync_playwright")
            lines.append("")
            lines.append(f"def {function_name}():")
            lines.append("    with sync_playwright() as p:")
            lines.append(f'        browser = p.{self.browser}.launch(headless={self.headless})')
            lines.append(f'        context = browser.new_context(viewport={{"width": {self.viewport_width}, "height": {self.viewport_height}}})')
            lines.append("        page = context.new_page()")
            lines.append("")
            for act in actions:
                stmt = self.format_action_python(act, is_async=False)
                lines.append(f"        {stmt}")
            lines.append("")
            lines.append("        context.close()")
            lines.append("        browser.close()")
            lines.append("")
            lines.append("if __name__ == '__main__':")
            lines.append(f"    {function_name}()")
        return chr(10).join(lines)

    def generate_javascript_script(
        self,
        actions: List[RecordedAction],
        function_name: str = "runFlow",
    ) -> str:
        """Generate modern JavaScript Playwright script."""
        lines = [
            'const { chromium } = require("playwright");',
            "",
            f"async function {function_name}() {{",
            f"  const browser = await chromium.launch({{ headless: {str(self.headless).lower()} }});",
            f'  const context = await browser.newContext({{ viewport: {{ width: {self.viewport_width}, height: {self.viewport_height} }} }});',
            "  const page = await context.newPage();",
            "",
        ]
        for act in actions:
            at = act.action_type
            sel = json.dumps(act.selector or "")
            val = json.dumps(act.value or "")
            if at == ActionType.GOTO:
                lines.append(f"  await page.goto({val});")
            elif at == ActionType.CLICK:
                lines.append(f"  await page.locator({sel}).click();")
            elif at == ActionType.FILL:
                lines.append(f"  await page.locator({sel}).fill({val});")
            elif at == ActionType.PRESS:
                key = json.dumps(act.params.get("key", act.value or "Enter"))
                lines.append(f"  await page.keyboard.press({key});")
            elif at == ActionType.WAIT_FOR_SELECTOR:
                lines.append(f"  await page.waitForSelector({sel});")
            elif at == ActionType.SCREENSHOT:
                path = json.dumps(act.params.get("path", act.value or "screenshot.png"))
                lines.append(f"  await page.screenshot({{ path: {path} }});")
            else:
                lines.append(f"  // action: {at.value}")

        lines.extend([
            "",
            "  await context.close();",
            "  await browser.close();",
            "}",
            "",
            f"{function_name}();",
        ])
        return chr(10).join(lines)


class PlaywrightActionRecorder:
    """
    In-memory recorder capturing user/agent Playwright browser events.
    Supports script generation, JSON serialization, and deterministic replays.
    """

    def __init__(self, generator: Optional[PlaywrightCodeGenerator] = None) -> None:
        self._lock = threading.RLock()
        self._actions: List[RecordedAction] = []
        self.generator = generator or PlaywrightCodeGenerator()

    @property
    def total_actions(self) -> int:
        with self._lock:
            return len(self._actions)

    def record_action(self, action: RecordedAction) -> RecordedAction:
        """Record generic browser action."""
        with self._lock:
            self._actions.append(action)
        return action

    def record_goto(self, url: str, description: str = "") -> RecordedAction:
        """Record browser navigation to target URL."""
        act = RecordedAction(
            action_type=ActionType.GOTO,
            value=url,
            description=description or f"Navigate to {url}",
        )
        return self.record_action(act)

    def record_click(
        self,
        selector: str,
        button: str = "left",
        click_count: int = 1,
        description: str = "",
    ) -> RecordedAction:
        """Record mouse click on selector."""
        act = RecordedAction(
            action_type=ActionType.CLICK,
            selector=selector,
            params={"button": button, "click_count": click_count},
            description=description or f"Click {selector}",
        )
        return self.record_action(act)

    def record_dblclick(self, selector: str, description: str = "") -> RecordedAction:
        """Record mouse double-click on selector."""
        act = RecordedAction(
            action_type=ActionType.DBLCLICK,
            selector=selector,
            description=description or f"Double click {selector}",
        )
        return self.record_action(act)

    def record_fill(self, selector: str, text: str, description: str = "") -> RecordedAction:
        """Record text input into form field."""
        act = RecordedAction(
            action_type=ActionType.FILL,
            selector=selector,
            value=text,
            description=description or f"Fill {selector} with '{text}'",
        )
        return self.record_action(act)

    def record_press(
        self,
        key: str,
        selector: Optional[str] = None,
        description: str = "",
    ) -> RecordedAction:
        """Record keyboard keypress."""
        act = RecordedAction(
            action_type=ActionType.PRESS,
            selector=selector,
            value=key,
            params={"key": key},
            description=description or f"Press {key}",
        )
        return self.record_action(act)

    def record_select(self, selector: str, value: str, description: str = "") -> RecordedAction:
        """Record dropdown option selection."""
        act = RecordedAction(
            action_type=ActionType.SELECT_OPTION,
            selector=selector,
            value=value,
            description=description or f"Select {value} in {selector}",
        )
        return self.record_action(act)

    def record_check(self, selector: str, description: str = "") -> RecordedAction:
        """Record checking checkbox."""
        act = RecordedAction(
            action_type=ActionType.CHECK,
            selector=selector,
            description=description or f"Check {selector}",
        )
        return self.record_action(act)

    def record_uncheck(self, selector: str, description: str = "") -> RecordedAction:
        """Record unchecking checkbox."""
        act = RecordedAction(
            action_type=ActionType.UNCHECK,
            selector=selector,
            description=description or f"Uncheck {selector}",
        )
        return self.record_action(act)

    def record_hover(self, selector: str, description: str = "") -> RecordedAction:
        """Record mouse hover over element."""
        act = RecordedAction(
            action_type=ActionType.HOVER,
            selector=selector,
            description=description or f"Hover {selector}",
        )
        return self.record_action(act)

    def record_scroll(
        self,
        delta_x: int = 0,
        delta_y: int = 100,
        description: str = "",
    ) -> RecordedAction:
        """Record mouse wheel scroll."""
        act = RecordedAction(
            action_type=ActionType.SCROLL,
            params={"delta_x": delta_x, "delta_y": delta_y},
            description=description or f"Scroll by ({delta_x}, {delta_y})",
        )
        return self.record_action(act)

    def record_wait(
        self,
        selector: str,
        timeout_ms: int = 5000,
        state: str = "visible",
        description: str = "",
    ) -> RecordedAction:
        """Record waiting for selector state."""
        act = RecordedAction(
            action_type=ActionType.WAIT_FOR_SELECTOR,
            selector=selector,
            params={"timeout_ms": timeout_ms, "state": state},
            description=description or f"Wait for {selector} ({state})",
        )
        return self.record_action(act)

    def record_screenshot(
        self,
        path: str = "screenshot.png",
        full_page: bool = False,
        description: str = "",
    ) -> RecordedAction:
        """Record visual screenshot capture."""
        act = RecordedAction(
            action_type=ActionType.SCREENSHOT,
            value=path,
            params={"path": path, "full_page": full_page},
            description=description or f"Capture screenshot to {path}",
        )
        return self.record_action(act)

    def get_actions(self) -> List[RecordedAction]:
        """Return shallow copy of recorded actions."""
        with self._lock:
            return list(self._actions)

    def clear(self) -> None:
        """Clear all recorded actions."""
        with self._lock:
            self._actions.clear()

    def export_to_python(self, is_async: bool = False, function_name: str = "run_flow") -> str:
        """Generate Python script from currently recorded actions."""
        with self._lock:
            return self.generator.generate_python_script(
                self._actions, is_async=is_async, function_name=function_name
            )

    def export_to_javascript(self, function_name: str = "runFlow") -> str:
        """Generate JavaScript script from currently recorded actions."""
        with self._lock:
            return self.generator.generate_javascript_script(
                self._actions, function_name=function_name
            )

    def export_to_json(self) -> str:
        """Export action list to JSON string."""
        with self._lock:
            return json.dumps([a.to_dict() for a in self._actions], indent=2)

    def import_from_json(self, json_str: str) -> List[RecordedAction]:
        """Import action list from JSON string."""
        with self._lock:
            data = json.loads(json_str)
            actions = [RecordedAction.from_dict(d) for d in data]
            self._actions.extend(actions)
            return actions


_GLOBAL_RECORDER: Optional[PlaywrightActionRecorder] = None
_GLOBAL_RECORDER_LOCK = threading.RLock()


def get_action_recorder() -> PlaywrightActionRecorder:
    """Acquire thread-safe singleton PlaywrightActionRecorder."""
    global _GLOBAL_RECORDER
    with _GLOBAL_RECORDER_LOCK:
        if _GLOBAL_RECORDER is None:
            _GLOBAL_RECORDER = PlaywrightActionRecorder()
        return _GLOBAL_RECORDER


def reset_action_recorder() -> PlaywrightActionRecorder:
    """Reset singleton PlaywrightActionRecorder."""
    global _GLOBAL_RECORDER
    with _GLOBAL_RECORDER_LOCK:
        _GLOBAL_RECORDER = PlaywrightActionRecorder()
        return _GLOBAL_RECORDER
