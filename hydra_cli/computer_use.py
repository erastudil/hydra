"""
Sovereign Computer Use and Desktop Automation Engine for Hydra CLI.
Provides OS-level automation, coordinate boundary guards, screen capture,
and Playwright browser integration for autonomous agent loops.
"""

from __future__ import annotations

import base64
import concurrent.futures
import ctypes
import datetime
import hashlib
import io
import ipaddress
import math
import urllib.parse
import json
import os
import re
import sys
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple, Union

try:
    from PIL import Image, ImageDraw, ImageGrab
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

try:
    from playwright.sync_api import sync_playwright, Playwright, Browser, BrowserContext, Page, Error as PlaywrightError
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False
    PlaywrightError = Exception

# Windows Virtual Key Codes
VK_MAP: Dict[str, int] = {
    'backspace': 0x08, 'back': 0x08,
    'tab': 0x09,
    'clear': 0x0C,
    'enter': 0x0D, 'return': 0x0D,
    'shift': 0x10,
    'control': 0x11, 'ctrl': 0x11,
    'alt': 0x12, 'menu': 0x12,
    'pause': 0x13,
    'caps_lock': 0x14, 'capslock': 0x14,
    'escape': 0x1B, 'esc': 0x1B,
    'space': 0x20, ' ': 0x20,
    'page_up': 0x21, 'pageup': 0x21, 'prior': 0x21,
    'page_down': 0x22, 'pagedown': 0x22, 'next': 0x22,
    'end': 0x23,
    'home': 0x24,
    'left': 0x25,
    'up': 0x26,
    'right': 0x27,
    'down': 0x28,
    'select': 0x29,
    'print': 0x2A,
    'execute': 0x2B,
    'print_screen': 0x2C, 'snapshot': 0x2C, 'prtsc': 0x2C,
    'insert': 0x2D,
    'delete': 0x2E, 'del': 0x2E,
    'help': 0x2F,
    'win': 0x5B, 'windows': 0x5B, 'lwin': 0x5B, 'rwin': 0x5C,
    'numpad0': 0x60, 'numpad1': 0x61, 'numpad2': 0x62, 'numpad3': 0x63,
    'numpad4': 0x64, 'numpad5': 0x65, 'numpad6': 0x66, 'numpad7': 0x67,
    'numpad8': 0x68, 'numpad9': 0x69,
    'multiply': 0x6A, 'add': 0x6B, 'separator': 0x6C, 'subtract': 0x6D, 'decimal': 0x6E, 'divide': 0x6F,
    'f1': 0x70, 'f2': 0x71, 'f3': 0x72, 'f4': 0x73, 'f5': 0x74, 'f6': 0x75,
    'f7': 0x76, 'f8': 0x77, 'f9': 0x78, 'f10': 0x79, 'f11': 0x7A, 'f12': 0x7B,
}

MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_MIDDLEDOWN = 0x0020
MOUSEEVENTF_MIDDLEUP = 0x0040
MOUSEEVENTF_WHEEL = 0x0800
MOUSEEVENTF_ABSOLUTE = 0x8000

KEYEVENTF_KEYDOWN = 0x0000
KEYEVENTF_KEYUP = 0x0002

def _sanitize_coord(val: Any, default: float = 0.0) -> float:
    """Sanitize coordinate value against NaN, infinity, None, and conversion errors."""
    try:
        if val is None:
            return default
        f = float(val)
        if math.isnan(f) or math.isinf(f):
            return default
        return f
    except (TypeError, ValueError):
        return default


def _is_restricted_url(url: str) -> Tuple[bool, Optional[str]]:
    """Verify target URL against cloud metadata, link-local, and obscured SSRF patterns."""
    raw_url = urllib.parse.unquote(url or "").strip()
    try:
        parsed = urllib.parse.urlsplit(raw_url)
        host = (parsed.hostname or "").strip("[]").strip()
    except Exception:
        host = raw_url

    if not host:
        return False, None

    BLOCKED_DOMAINS = ("metadata.google.internal", "instance-data", "wpad")
    host_lower = host.lower()
    if any(d in host_lower for d in BLOCKED_DOMAINS):
        return True, f"Security violation: access to metadata target '{host}' blocked"

    if host_lower.startswith("169.254."):
        return True, f"Security violation: access to link-local IP '{host}' blocked"

    # Standard IP check
    try:
        ip = ipaddress.ip_address(host)
        if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
            ip = ip.ipv4_mapped
        if ip.is_link_local or str(ip).startswith("169.254."):
            return True, f"Security violation: access to link-local IP '{ip}' blocked"
    except ValueError:
        # Check integer decimal / hex / octal IP representation
        try:
            int_val = int(host, 0)
            if 0 <= int_val <= 0xFFFFFFFF:
                ip = ipaddress.IPv4Address(int_val)
                if ip.is_link_local or str(ip).startswith("169.254."):
                    return True, f"Security violation: access to obscured IP '{ip}' blocked"
        except Exception:
            pass

    return False, None

class CoordinateBounds:
    """
    Coordinate boundary guard and safety clipping engine.
    Clamps target coordinates within display dimensions and guards fenced zones.
    """

    def __init__(
        self,
        width: int = 1920,
        height: int = 1080,
        margin: int = 0,
        enable_failsafe: bool = True,
    ) -> None:
        self.width = max(100, int(width))
        self.height = max(100, int(height))
        self.margin = max(0, int(margin))
        self.enable_failsafe = bool(enable_failsafe)
        self._fences: Dict[str, Tuple[int, int, int, int]] = {}
        self._lock = threading.RLock()
        self.reset_telemetry()

    def reset_telemetry(self) -> None:
        """Reset telemetry metrics."""
        with getattr(self, "_lock", threading.RLock()):
            self._total_checks: int = 0
            self._clipped_actions: int = 0
            self._fenced_violations: int = 0
            self._failsafe_trips: int = 0

    def set_resolution(self, width: int, height: int) -> None:
        """Update display dimensions."""
        with self._lock:
            self.width = max(100, int(width))
            self.height = max(100, int(height))

    def add_fence(self, label: str, x1: int, y1: int, x2: int, y2: int) -> None:
        """Register rectangular fenced exclusion zone."""
        with self._lock:
            min_x, max_x = min(x1, x2), max(x1, x2)
            min_y, max_y = min(y1, y2), max(y1, y2)
            self._fences[label] = (min_x, min_y, max_x, max_y)

    def remove_fence(self, label: str) -> bool:
        """Remove fenced zone by label."""
        with self._lock:
            return self._fences.pop(label, None) is not None

    def list_fences(self) -> Dict[str, Tuple[int, int, int, int]]:
        """Return registered fenced zones."""
        with self._lock:
            return dict(self._fences)

    def clip(self, x: Any, y: Any) -> Tuple[int, int, bool]:
        """Clamp coordinates to active screen bounds minus margin."""
        with self._lock:
            self._total_checks += 1
            min_x = self.margin
            max_x = max(min_x, self.width - 1 - self.margin)
            min_y = self.margin
            max_y = max(min_y, self.height - 1 - self.margin)

            sx = _sanitize_coord(x, float(min_x))
            sy = _sanitize_coord(y, float(min_y))

            clamped_x = max(min_x, min(int(sx), max_x))
            clamped_y = max(min_y, min(int(sy), max_y))
            was_clipped = (clamped_x != sx) or (clamped_y != sy)
            if was_clipped:
                self._clipped_actions += 1
            return clamped_x, clamped_y, was_clipped

    def normalize(self, x: Any, y: Any) -> Tuple[float, float]:
        """Normalize pixel coordinates to [0.0, 1000.0] grid."""
        with self._lock:
            sx = _sanitize_coord(x, 0.0)
            sy = _sanitize_coord(y, 0.0)
            norm_x = round((sx / max(1.0, float(self.width))) * 1000.0, 2)
            norm_y = round((sy / max(1.0, float(self.height))) * 1000.0, 2)
            return max(0.0, min(1000.0, norm_x)), max(0.0, min(1000.0, norm_y))

    def denormalize(self, norm_x: Any, norm_y: Any) -> Tuple[int, int]:
        """Convert [0.0, 1000.0] grid coordinates to physical screen pixels."""
        with self._lock:
            snx = max(0.0, min(1000.0, _sanitize_coord(norm_x, 0.0)))
            sny = max(0.0, min(1000.0, _sanitize_coord(norm_y, 0.0)))
            px = int((snx / 1000.0) * self.width)
            py = int((sny / 1000.0) * self.height)
            cx, cy, _ = self.clip(px, py)
            return cx, cy

    def check_safety(self, x: Any, y: Any) -> Tuple[bool, Optional[str]]:
        """Verify coordinates against safety fences and failsafe triggers."""
        with self._lock:
            sx = _sanitize_coord(x, 0.0)
            sy = _sanitize_coord(y, 0.0)
            ix, iy = int(sx), int(sy)

            if self.enable_failsafe and ix <= 2 and iy <= 2:
                self._failsafe_trips += 1
                return False, "Emergency failsafe triggered: cursor targeted screen corner (0, 0)"

            for label, (fx1, fy1, fx2, fy2) in self._fences.items():
                if fx1 <= ix <= fx2 and fy1 <= iy <= fy2:
                    self._fenced_violations += 1
                    return False, f"Coordinate ({ix}, {iy}) inside restricted fence zone '{label}'"

            return True, None




    def fill_form(
        self,
        fields: Dict[str, str],
        form_selector: Optional[str] = None,
        submit: bool = False,
    ) -> Dict[str, Any]:
        """Composite action: auto-fill web form fields."""
        return self.browser.fill_form(fields=fields, form_selector=form_selector, submit=submit)

    def scroll_until_visible(
        self,
        selector: str,
        max_scrolls: int = 10,
        scroll_step: int = 400,
        timeout_ms: int = 5000,
    ) -> Dict[str, Any]:
        """Composite action: scroll page until target element visible."""
        return self.browser.scroll_until_visible(
            selector=selector, max_scrolls=max_scrolls, scroll_step=scroll_step, timeout_ms=timeout_ms
        )

    def extract_table_data(self, selector: str = "table") -> Dict[str, Any]:
        """Composite action: extract structured table dataset."""
        return self.browser.extract_table_data(selector=selector)

    def find_window_by_title_pattern(
        self, pattern: str, visible_only: bool = True
    ) -> Dict[str, Any]:
        """Composite action: find windows matching regex title pattern."""
        return self.os.find_window_by_title_pattern(pattern=pattern, visible_only=visible_only)

    def set_window_bounds(
        self, hwnd: int, x: int, y: int, width: int, height: int
    ) -> Dict[str, Any]:
        """Composite action: reposition and resize window."""
        return self.os.set_window_bounds(hwnd=hwnd, x=x, y=y, width=width, height=height)

    def safe_key_sequence(
        self, keys: Sequence[str], interval_ms: float = 50.0
    ) -> Dict[str, Any]:
        """Composite action: safely execute sequence of key presses."""
        return self.os.safe_key_sequence(keys=keys, interval_ms=interval_ms)

    def get_telemetry(self) -> Dict[str, Any]:
        """Return telemetry counters."""
        with self._lock:
            return {
                "width": self.width,
                "height": self.height,
                "margin": self.margin,
                "fences_count": len(self._fences),
                "total_checks": self._total_checks,
                "clipped_actions": self._clipped_actions,
                "fenced_violations": self._fenced_violations,
                "failsafe_trips": self._failsafe_trips,
            }

class OSController:
    """
    OS-level input automation and window manager.
    Executes native Windows user32 mouse and keyboard dispatches with fallback simulation.
    """

    def __init__(self, bounds: Optional[CoordinateBounds] = None) -> None:
        self.bounds = bounds or CoordinateBounds()
        self.is_windows = sys.platform == "win32"
        self._lock = threading.RLock()
        self._sim_cursor_x: int = self.bounds.width // 2
        self._sim_cursor_y: int = self.bounds.height // 2
        self._sim_active_window: Dict[str, Any] = {
            "hwnd": 1001,
            "title": "Hydra Desktop Shell",
            "rect": {"left": 100, "top": 100, "right": 1100, "bottom": 800},
            "pid": os.getpid(),
        }
        self._sim_windows: List[Dict[str, Any]] = [self._sim_active_window]
        self._action_log: List[Dict[str, Any]] = []
        self._init_os_handles()

    def _init_os_handles(self) -> None:
        """Initialize OS system handles."""
        if not self.is_windows:
            return
        try:
            w = ctypes.windll.user32.GetSystemMetrics(0)
            h = ctypes.windll.user32.GetSystemMetrics(1)
            if w > 0 and h > 0:
                self.bounds.set_resolution(w, h)
        except Exception:
            pass

    def get_screen_size(self) -> Tuple[int, int]:
        """Return current screen resolution."""
        if self.is_windows:
            try:
                w = ctypes.windll.user32.GetSystemMetrics(0)
                h = ctypes.windll.user32.GetSystemMetrics(1)
                if w > 0 and h > 0:
                    return int(w), int(h)
            except Exception:
                pass
        return self.bounds.width, self.bounds.height

    def get_cursor_position(self) -> Tuple[int, int]:
        """Return current cursor coordinates."""
        if self.is_windows:
            try:
                class POINT(ctypes.Structure):
                    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]
                pt = POINT()
                if ctypes.windll.user32.GetCursorPos(ctypes.byref(pt)):
                    return int(pt.x), int(pt.y)
            except Exception:
                pass
        with self._lock:
            return self._sim_cursor_x, self._sim_cursor_y

    def mouse_move(self, x: int, y: int, smooth: bool = True, steps: int = 5) -> Dict[str, Any]:
        """Move mouse cursor to coordinates with boundary clipping."""
        cx, cy, was_clipped = self.bounds.clip(x, y)
        safe, reason = self.bounds.check_safety(cx, cy)
        if not safe:
            return {"isError": True, "error": reason, "clipped": was_clipped}

        cur_x, cur_y = self.get_cursor_position()
        step_count = max(1, steps) if smooth else 1

        for i in range(1, step_count + 1):
            interp_x = int(cur_x + (cx - cur_x) * (i / step_count))
            interp_y = int(cur_y + (cy - cur_y) * (i / step_count))
            if self.is_windows:
                try:
                    ctypes.windll.user32.SetCursorPos(interp_x, interp_y)
                except Exception:
                    pass
            if smooth and step_count > 1:
                time.sleep(0.005)

        with self._lock:
            self._sim_cursor_x = cx
            self._sim_cursor_y = cy
            self._action_log.append({"action": "mouse_move", "x": cx, "y": cy, "clipped": was_clipped})

        return {"isError": False, "x": cx, "y": cy, "clipped": was_clipped}

    def mouse_click(
        self,
        x: Optional[int] = None,
        y: Optional[int] = None,
        button: str = "left",
        double: bool = False,
    ) -> Dict[str, Any]:
        """Click mouse button at specified or current coordinates."""
        target_x, target_y = (x, y) if (x is not None and y is not None) else self.get_cursor_position()
        move_res = self.mouse_move(target_x, target_y, smooth=False)
        if move_res.get("isError"):
            return move_res

        cx, cy = move_res["x"], move_res["y"]
        btn = button.lower()
        down_flag = MOUSEEVENTF_LEFTDOWN if btn == "left" else (MOUSEEVENTF_RIGHTDOWN if btn == "right" else MOUSEEVENTF_MIDDLEDOWN)
        up_flag = MOUSEEVENTF_LEFTUP if btn == "left" else (MOUSEEVENTF_RIGHTUP if btn == "right" else MOUSEEVENTF_MIDDLEUP)

        clicks = 2 if double else 1
        for _ in range(clicks):
            if self.is_windows:
                try:
                    ctypes.windll.user32.mouse_event(down_flag, 0, 0, 0, 0)
                    time.sleep(0.01)
                    ctypes.windll.user32.mouse_event(up_flag, 0, 0, 0, 0)
                    if double:
                        time.sleep(0.05)
                except Exception:
                    pass

        with self._lock:
            self._action_log.append({"action": "mouse_click", "x": cx, "y": cy, "button": btn, "double": double})

        return {"isError": False, "x": cx, "y": cy, "button": btn, "double": double}

    def mouse_down(self, button: str = "left") -> Dict[str, Any]:
        """Press and hold mouse button."""
        btn = button.lower()
        flag = MOUSEEVENTF_LEFTDOWN if btn == "left" else (MOUSEEVENTF_RIGHTDOWN if btn == "right" else MOUSEEVENTF_MIDDLEDOWN)
        if self.is_windows:
            try:
                ctypes.windll.user32.mouse_event(flag, 0, 0, 0, 0)
            except Exception:
                pass
        with self._lock:
            self._action_log.append({"action": "mouse_down", "button": btn})
        return {"isError": False, "button": btn}

    def mouse_up(self, button: str = "left") -> Dict[str, Any]:
        """Release held mouse button."""
        btn = button.lower()
        flag = MOUSEEVENTF_LEFTUP if btn == "left" else (MOUSEEVENTF_RIGHTUP if btn == "right" else MOUSEEVENTF_MIDDLEUP)
        if self.is_windows:
            try:
                ctypes.windll.user32.mouse_event(flag, 0, 0, 0, 0)
            except Exception:
                pass
        with self._lock:
            self._action_log.append({"action": "mouse_up", "button": btn})
        return {"isError": False, "button": btn}

    def mouse_drag(
        self,
        start_x: int,
        start_y: int,
        end_x: int,
        end_y: int,
        steps: int = 10,
        button: str = "left",
    ) -> Dict[str, Any]:
        """Drag mouse pointer between coordinates with button depressed."""
        self.mouse_move(start_x, start_y, smooth=False)
        self.mouse_down(button)
        time.sleep(0.02)
        move_res = self.mouse_move(end_x, end_y, smooth=True, steps=steps)
        time.sleep(0.02)
        self.mouse_up(button)
        return {"isError": False, "start": (start_x, start_y), "end": (move_res["x"], move_res["y"]), "button": button}

    def mouse_scroll(self, dx: int = 0, dy: int = 0) -> Dict[str, Any]:
        """Scroll mouse wheel vertically or horizontally."""
        if self.is_windows and dy != 0:
            try:
                wheel_delta = int(dy * 120)
                ctypes.windll.user32.mouse_event(MOUSEEVENTF_WHEEL, 0, 0, wheel_delta, 0)
            except Exception:
                pass
        with self._lock:
            self._action_log.append({"action": "mouse_scroll", "dx": dx, "dy": dy})
        return {"isError": False, "dx": dx, "dy": dy}

    def _resolve_vk(self, key_name: str) -> Optional[int]:
        """Resolve key name string to virtual key code."""
        norm = key_name.strip().lower()
        if norm in VK_MAP:
            return VK_MAP[norm]
        if len(norm) == 1:
            ch = norm.upper()
            return ord(ch)
        return None

    def key_down(self, key: str) -> Dict[str, Any]:
        """Send key down event."""
        vk = self._resolve_vk(key)
        if vk is None:
            return {"isError": True, "error": f"Unrecognized key: {key}"}
        if self.is_windows:
            try:
                ctypes.windll.user32.keybd_event(vk, 0, KEYEVENTF_KEYDOWN, 0)
            except Exception:
                pass
        with self._lock:
            self._action_log.append({"action": "key_down", "key": key, "vk": vk})
        return {"isError": False, "key": key, "vk": vk}

    def key_up(self, key: str) -> Dict[str, Any]:
        """Send key up event."""
        vk = self._resolve_vk(key)
        if vk is None:
            return {"isError": True, "error": f"Unrecognized key: {key}"}
        if self.is_windows:
            try:
                ctypes.windll.user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)
            except Exception:
                pass
        with self._lock:
            self._action_log.append({"action": "key_up", "key": key, "vk": vk})
        return {"isError": False, "key": key, "vk": vk}

    def key_press(self, key: str) -> Dict[str, Any]:
        """Send tap keystroke event."""
        down_res = self.key_down(key)
        if down_res.get("isError"):
            return down_res
        time.sleep(0.01)
        up_res = self.key_up(key)
        return {"isError": False, "key": key, "vk": down_res.get("vk")}

    def key_chord(self, chord: str) -> Dict[str, Any]:
        """Execute combined hotkey chord like ctrl+c or alt+tab."""
        keys = [k.strip() for k in chord.replace("-", "+").split("+") if k.strip()]
        if not keys:
            return {"isError": True, "error": "Empty key chord"}

        pressed = []
        for k in keys:
            res = self.key_down(k)
            if res.get("isError"):
                for p in reversed(pressed):
                    self.key_up(p)
                return res
            pressed.append(k)

        time.sleep(0.02)
        for p in reversed(pressed):
            self.key_up(p)

        with self._lock:
            self._action_log.append({"action": "key_chord", "chord": chord, "keys": keys})
        return {"isError": False, "chord": chord, "keys": keys}

    def type_text(self, text: str, delay_ms: float = 10.0) -> Dict[str, Any]:
        """Type text string character by character."""
        sec = max(0.0, delay_ms / 1000.0)
        typed_chars = 0
        for ch in text:
            if ch == "\n":
                self.key_press("enter")
            elif ch == "\t":
                self.key_press("tab")
            else:
                vk = self._resolve_vk(ch)
                if vk and self.is_windows:
                    try:
                        shift_needed = ch.isupper() or ch in "~!@#$%^&*()_+{}|:\"<>?"
                        if shift_needed:
                            self.key_down("shift")
                        ctypes.windll.user32.keybd_event(vk, 0, KEYEVENTF_KEYDOWN, 0)
                        ctypes.windll.user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)
                        if shift_needed:
                            self.key_up("shift")
                    except Exception:
                        pass
                typed_chars += 1
            if sec > 0:
                time.sleep(sec)

        with self._lock:
            self._action_log.append({"action": "type_text", "length": len(text)})
        return {"isError": False, "typed_characters": typed_chars, "text_length": len(text)}

    def get_active_window(self) -> Dict[str, Any]:
        """Inspect current foreground window."""
        if self.is_windows:
            try:
                hwnd = ctypes.windll.user32.GetForegroundWindow()
                if hwnd:
                    length = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
                    buf = ctypes.create_unicode_buffer(length + 1)
                    ctypes.windll.user32.GetWindowTextW(hwnd, buf, length + 1)
                    title = buf.value

                    class RECT(ctypes.Structure):
                        _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long), ("right", ctypes.c_long), ("bottom", ctypes.c_long)]
                    r = RECT()
                    ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(r))

                    pid = ctypes.c_ulong()
                    ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))

                    return {
                        "hwnd": int(hwnd),
                        "title": title or "<Untitled Window>",
                        "rect": {"left": int(r.left), "top": int(r.top), "right": int(r.right), "bottom": int(r.bottom)},
                        "pid": int(pid.value),
                    }
            except Exception:
                pass
        with self._lock:
            return dict(self._sim_active_window)

    def list_windows(self, visible_only: bool = True) -> List[Dict[str, Any]]:
        """Enumerate top-level system windows."""
        results: List[Dict[str, Any]] = []
        if self.is_windows:
            try:
                EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
                class RECT(ctypes.Structure):
                    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long), ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

                def _enum_cb(hwnd, lparam):
                    if visible_only and not ctypes.windll.user32.IsWindowVisible(hwnd):
                        return True
                    length = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
                    if length == 0:
                        return True
                    buf = ctypes.create_unicode_buffer(length + 1)
                    ctypes.windll.user32.GetWindowTextW(hwnd, buf, length + 1)
                    title = buf.value
                    if not title.strip():
                        return True

                    r = RECT()
                    ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(r))
                    pid = ctypes.c_ulong()
                    ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))

                    results.append({
                        "hwnd": int(hwnd),
                        "title": title,
                        "rect": {"left": int(r.left), "top": int(r.top), "right": int(r.right), "bottom": int(r.bottom)},
                        "pid": int(pid.value),
                    })
                    return True

                ctypes.windll.user32.EnumWindows(EnumWindowsProc(_enum_cb), 0)
                if results:
                    return results
            except Exception:
                pass

        with self._lock:
            return [dict(w) for w in self._sim_windows]

    def focus_window(self, title_or_hwnd: Union[str, int]) -> Dict[str, Any]:
        """Focus window by title substring or handle."""
        target_str = str(title_or_hwnd).lower()
        RESTRICTED_WINDOW_PATTERNS = (
            "1password", "bitwarden", "keepass", "lastpass", "dashlane",
            "credential", "uac", "user account control", "windows security",
            "taskmgr", "task manager", "regedit", "registry editor",
        )
        if any(pat in target_str for pat in RESTRICTED_WINDOW_PATTERNS):
            return {"isError": True, "error": f"Security violation: focus blocked on protected window target '{title_or_hwnd}'"}
        target_hwnd = None
        if isinstance(title_or_hwnd, int):
            target_hwnd = title_or_hwnd
        elif self.is_windows:
            wins = self.list_windows(visible_only=True)
            for w in wins:
                if str(title_or_hwnd).lower() in w["title"].lower():
                    target_hwnd = w["hwnd"]
                    break

        if target_hwnd and self.is_windows:
            try:
                ctypes.windll.user32.ShowWindow(target_hwnd, 9)
                ctypes.windll.user32.SetForegroundWindow(target_hwnd)
                return {"isError": False, "hwnd": target_hwnd, "focused": True}
            except Exception as exc:
                return {"isError": True, "error": str(exc)}

        with self._lock:
            self._sim_active_window["title"] = str(title_or_hwnd)
            return {"isError": False, "target": title_or_hwnd, "focused": True, "simulated": True}


    def find_window_by_title_pattern(self, pattern: str, visible_only: bool = True) -> Dict[str, Any]:
        """Find windows whose titles match regular expression pattern."""
        try:
            regex = re.compile(pattern, re.IGNORECASE)
            windows = self.list_windows(visible_only=visible_only)
            matches = [w for w in windows if regex.search(w.get("title", ""))]
            return {"isError": False, "pattern": pattern, "matches": matches, "count": len(matches)}
        except Exception as exc:
            return {"isError": True, "error": str(exc)}

    def set_window_bounds(self, hwnd: int, x: int, y: int, width: int, height: int) -> Dict[str, Any]:
        """Relocate and resize window on desktop."""
        if self.is_windows:
            try:
                ctypes.windll.user32.MoveWindow(hwnd, x, y, width, height, True)
                return {
                    "isError": False,
                    "hwnd": hwnd,
                    "rect": {"left": x, "top": y, "right": x + width, "bottom": y + height},
                }
            except Exception as exc:
                return {"isError": True, "error": str(exc)}
        with self._lock:
            for w in self._sim_windows:
                if w.get("hwnd") == hwnd:
                    w["rect"] = {"left": x, "top": y, "right": x + width, "bottom": y + height}
                    return {"isError": False, "hwnd": hwnd, "rect": w["rect"], "simulated": True}
            return {
                "isError": False,
                "hwnd": hwnd,
                "rect": {"left": x, "top": y, "right": x + width, "bottom": y + height},
                "simulated": True,
            }

    def safe_key_sequence(self, keys: List[str], interval_ms: float = 50.0) -> Dict[str, Any]:
        """Execute sequence of key strokes with safety pauses."""
        pressed = []
        sec = max(0.005, interval_ms / 1000.0)
        for k in keys:
            res = self.key_press(k)
            if res.get("isError"):
                return {"isError": True, "error": res.get("error"), "pressed": pressed}
            pressed.append(k)
            time.sleep(sec)
        return {"isError": False, "sequence": keys, "keys_pressed": len(pressed)}

    def get_action_history(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Return recent action logs."""
        with self._lock:
            return list(self._action_log[-limit:])

class ScreenCaptureEngine:
    """
    Desktop screen capture engine.
    Captures full desktop or region with base64 serialization and synthetic fallback.
    """

    def __init__(self, bounds: Optional[CoordinateBounds] = None) -> None:
        self.bounds = bounds or CoordinateBounds()
        self._lock = threading.RLock()

    def capture(
        self,
        bbox: Optional[Tuple[int, int, int, int]] = None,
        save_path: Optional[str] = None,
        as_base64: bool = True,
    ) -> Dict[str, Any]:
        """Capture screen image returning metadata, bytes, and optional base64."""
        img = None
        if PIL_AVAILABLE:
            try:
                img = ImageGrab.grab(bbox=bbox)
            except Exception:
                img = None

        if img is None:
            w = self.bounds.width if not bbox else max(10, bbox[2] - bbox[0])
            h = self.bounds.height if not bbox else max(10, bbox[3] - bbox[1])
            if PIL_AVAILABLE:
                img = Image.new("RGB", (w, h), color=(15, 23, 42))
                draw = ImageDraw.Draw(img)
                for x in range(0, w, 80):
                    draw.line([(x, 0), (x, h)], fill=(30, 41, 59), width=1)
                for y in range(0, h, 80):
                    draw.line([(0, y), (w, y)], fill=(30, 41, 59), width=1)
                draw.rectangle([(20, 20), (w - 20, 80)], fill=(16, 185, 129))
                draw.text((40, 40), f"Hydra Sovereign Desktop Canvas - {datetime.datetime.now().isoformat()}", fill=(0, 0, 0))
            else:
                raw_bytes = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15c4\x00\x00\x00\rIDATx\x9cc`\x00\x00\x00\x02\x00\x01H\xaf\xa4q\x00\x00\x00\x00IEND\xaeB`\x82"
                b64 = base64.b64encode(raw_bytes).decode("ascii") if as_base64 else ""
                return {
                    "isError": False,
                    "width": 1,
                    "height": 1,
                    "format": "PNG",
                    "size_bytes": len(raw_bytes),
                    "base64": b64,
                    "sha256": hashlib.sha256(raw_bytes).hexdigest(),
                    "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                }

        buf = io.BytesIO()
        img.save(buf, format="PNG")
        raw_bytes = buf.getvalue()
        sha256 = hashlib.sha256(raw_bytes).hexdigest()

        if save_path:
            os.makedirs(os.path.dirname(os.path.abspath(save_path)), exist_ok=True)
            with open(save_path, "wb") as f:
                f.write(raw_bytes)

        b64 = base64.b64encode(raw_bytes).decode("ascii") if as_base64 else ""
        return {
            "isError": False,
            "width": img.width,
            "height": img.height,
            "format": "PNG",
            "size_bytes": len(raw_bytes),
            "path": save_path,
            "base64": b64,
            "sha256": sha256,
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }


class PlaywrightAutomationBridge:
    """
    Playwright browser automation bridge for computer use agents.
    Supports navigation, DOM inspection, element interactions, PDF printing, and network interception.
    Executes all Playwright calls on a dedicated single-threaded executor to preserve greenlet thread affinity.
    """

    def __init__(self, headless: bool = True) -> None:
        self.headless = headless
        self._lock = threading.RLock()
        self._executor = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="hydra-playwright")
        self._playwright: Optional[Any] = None
        self._browser: Optional[Any] = None
        self._context: Optional[Any] = None
        self._page: Optional[Any] = None
        self._network_logs: List[Dict[str, Any]] = []

    def _run(self, fn: Callable[..., Any], *args: Any, timeout: float = 30.0, **kwargs: Any) -> Any:
        """Dispatch Playwright operation onto dedicated worker thread."""
        fut = self._executor.submit(fn, *args, **kwargs)
        return fut.result(timeout=timeout)

    def _ensure_browser(self) -> Any:
        """Launch browser instance on demand."""
        if not PLAYWRIGHT_AVAILABLE:
            raise RuntimeError("Playwright uninstalled; run pip install playwright && playwright install.")
        with self._lock:
            if self._page is None:
                self._playwright = sync_playwright().start()
                self._browser = self._playwright.chromium.launch(headless=self.headless)
                self._context = self._browser.new_context()
                self._page = self._context.new_page()

                def _on_request(req):
                    self._network_logs.append({
                        "type": "request",
                        "url": req.url,
                        "method": req.method,
                        "resource_type": req.resource_type,
                        "timestamp": time.time(),
                    })
                    if len(self._network_logs) > 500:
                        self._network_logs.pop(0)

                def _on_response(res):
                    self._network_logs.append({
                        "type": "response",
                        "url": res.url,
                        "status": res.status,
                        "content_type": res.headers.get("content-type", ""),
                        "timestamp": time.time(),
                    })
                    if len(self._network_logs) > 500:
                        self._network_logs.pop(0)

                self._page.on("request", _on_request)
                self._page.on("response", _on_response)
            return self._page

    def get_info(self) -> Dict[str, Any]:
        """Thread-safe query of active browser url and title."""
        if self._page is None:
            return {"url": "", "title": ""}
        try:
            return self._run(lambda: {"url": self._page.url if self._page else "", "title": self._page.title() if self._page else ""})
        except Exception:
            return {"url": "", "title": ""}

    def navigate(self, url: str, timeout_ms: int = 30000) -> Dict[str, Any]:
        """Navigate to URL and return title and status."""
        restricted, reason = _is_restricted_url(url)
        if restricted:
            return {"isError": True, "error": reason}
        return self._run(self._navigate_internal, url, timeout_ms)

    def _navigate_internal(self, url: str, timeout_ms: int) -> Dict[str, Any]:
        try:
            page = self._ensure_browser()
            resp = page.goto(url, timeout=timeout_ms)
            status = resp.status if resp else 200
            return {
                "isError": False,
                "url": page.url,
                "status": status,
                "title": page.title(),
            }
        except Exception as exc:
            return {"isError": True, "error": str(exc)}

    def inspect_dom(self, selector: str = "body") -> Dict[str, Any]:
        """Extract semantic interactive elements from DOM tree."""
        return self._run(self._inspect_dom_internal, selector)

    def _inspect_dom_internal(self, selector: str = "body") -> Dict[str, Any]:
        try:
            page = self._ensure_browser()
            script = """
            (sel) => {
                const root = document.querySelector(sel) || document.body;
                const items = [];
                const nodes = root.querySelectorAll('button, a, input, select, textarea, [role="button"], h1, h2, h3, p');
                nodes.forEach((el, idx) => {
                    if (idx > 200) return;
                    const r = el.getBoundingClientRect();
                    const visible = r.width > 0 && r.height > 0 && window.getComputedStyle(el).display !== 'none';
                    if (!visible) return;
                    items.push({
                        tag: el.tagName.toLowerCase(),
                        id: el.id || '',
                        text: (el.innerText || el.value || '').trim().slice(0, 100),
                        type: el.type || '',
                        href: el.href || '',
                        box: { x: Math.round(r.x), y: Math.round(r.y), width: Math.round(r.width), height: Math.round(r.height) }
                    });
                });
                return { count: items.length, elements: items };
            }
            """
            res = page.evaluate(script, selector)
            return {"isError": False, "selector": selector, **res}
        except Exception as exc:
            return {"isError": True, "error": str(exc)}

    def click_element(self, selector: str, timeout_ms: int = 15000) -> Dict[str, Any]:
        """Click element in DOM matching selector."""
        return self._run(self._click_element_internal, selector, timeout_ms)

    def _click_element_internal(self, selector: str, timeout_ms: int) -> Dict[str, Any]:
        try:
            page = self._ensure_browser()
            page.click(selector, timeout=timeout_ms)
            return {"isError": False, "selector": selector, "clicked": True}
        except Exception as exc:
            return {"isError": True, "error": str(exc)}

    def type_element(self, selector: str, text: str, delay_ms: float = 10.0, timeout_ms: int = 15000) -> Dict[str, Any]:
        """Type text into element matching selector."""
        return self._run(self._type_element_internal, selector, text, delay_ms, timeout_ms)

    def _type_element_internal(self, selector: str, text: str, delay_ms: float, timeout_ms: int) -> Dict[str, Any]:
        try:
            page = self._ensure_browser()
            page.fill(selector, text, timeout=timeout_ms)
            return {"isError": False, "selector": selector, "typed_length": len(text)}
        except Exception as exc:
            return {"isError": True, "error": str(exc)}

    def capture_screenshot(self, path: Optional[str] = None, full_page: bool = False) -> Dict[str, Any]:
        """Capture browser page screenshot."""
        return self._run(self._capture_screenshot_internal, path, full_page)

    def _capture_screenshot_internal(self, path: Optional[str], full_page: bool) -> Dict[str, Any]:
        try:
            page = self._ensure_browser()
            png_bytes = page.screenshot(path=path, full_page=full_page)
            b64 = base64.b64encode(png_bytes).decode("ascii")
            return {
                "isError": False,
                "size_bytes": len(png_bytes),
                "path": path,
                "base64": b64,
                "sha256": hashlib.sha256(png_bytes).hexdigest(),
            }
        except Exception as exc:
            return {"isError": True, "error": str(exc)}

    def print_pdf(self, path: Optional[str] = None) -> Dict[str, Any]:
        """Print active page to PDF document."""
        return self._run(self._print_pdf_internal, path)

    def _print_pdf_internal(self, path: Optional[str]) -> Dict[str, Any]:
        try:
            page = self._ensure_browser()
            pdf_bytes = page.pdf(path=path)
            return {
                "isError": False,
                "size_bytes": len(pdf_bytes),
                "path": path,
                "sha256": hashlib.sha256(pdf_bytes).hexdigest(),
            }
        except Exception as exc:
            return {"isError": True, "error": str(exc)}

    def fill_form(
        self,
        fields: Dict[str, str],
        form_selector: Optional[str] = None,
        submit: bool = False,
        timeout_ms: int = 15000,
    ) -> Dict[str, Any]:
        """Fill multiple form fields and optionally submit."""
        return self._run(self._fill_form_internal, fields, form_selector, submit, timeout_ms)

    def _fill_form_internal(
        self,
        fields: Dict[str, str],
        form_selector: Optional[str],
        submit: bool,
        timeout_ms: int,
    ) -> Dict[str, Any]:
        filled = []
        try:
            page = self._ensure_browser()
            for sel, val in fields.items():
                target = f"{form_selector} {sel}" if form_selector else sel
                page.fill(target, str(val), timeout=timeout_ms)
                filled.append(sel)
            if submit:
                if form_selector:
                    page.eval_on_selector(form_selector, "form => form.submit()")
                else:
                    page.keyboard.press("Enter")
            return {"isError": False, "filled": filled, "count": len(filled), "submitted": submit}
        except Exception as exc:
            return {"isError": True, "error": str(exc), "filled": filled}

    def scroll_until_visible(
        self,
        selector: str,
        max_scrolls: int = 10,
        scroll_step: int = 400,
        timeout_ms: int = 5000,
    ) -> Dict[str, Any]:
        """Scroll down page until element matching selector becomes visible."""
        return self._run(self._scroll_until_visible_internal, selector, max_scrolls, scroll_step, timeout_ms)

    def _scroll_until_visible_internal(
        self,
        selector: str,
        max_scrolls: int,
        scroll_step: int,
        timeout_ms: int,
    ) -> Dict[str, Any]:
        try:
            page = self._ensure_browser()
            for i in range(max_scrolls):
                if page.is_visible(selector):
                    return {"isError": False, "selector": selector, "visible": True, "scrolls": i}
                page.mouse.wheel(0, scroll_step)
                time.sleep(0.1)
            is_vis = page.is_visible(selector)
            return {"isError": False, "selector": selector, "visible": is_vis, "scrolls": max_scrolls}
        except Exception as exc:
            return {"isError": True, "error": str(exc)}

    def extract_table_data(self, selector: str = "table") -> Dict[str, Any]:
        """Extract structured tabular data from table element."""
        return self._run(self._extract_table_data_internal, selector)

    def _extract_table_data_internal(self, selector: str = "table") -> Dict[str, Any]:
        try:
            page = self._ensure_browser()
            script = """
            (sel) => {
                const tbl = document.querySelector(sel);
                if (!tbl) return { found: false, headers: [], rows: [] };
                const headers = [];
                tbl.querySelectorAll("th").forEach(th => headers.push(th.innerText.trim()));
                const rows = [];
                tbl.querySelectorAll("tbody tr, tr").forEach(tr => {
                    const row = [];
                    tr.querySelectorAll("td").forEach(td => row.push(td.innerText.trim()));
                    if (row.length > 0) rows.push(row);
                });
                return { found: true, headers: headers, rows: rows, count: rows.length };
            }
            """
            res = page.evaluate(script, selector)
            if not res.get("found"):
                return {"isError": True, "error": f"Table matching '{selector}' not found"}
            return {
                "isError": False,
                "selector": selector,
                "headers": res.get("headers", []),
                "rows": res.get("rows", []),
                "row_count": len(res.get("rows", [])),
            }
        except Exception as exc:
            return {"isError": True, "error": str(exc)}

    def drag_and_drop(self, source_selector: str, target_selector: str, timeout_ms: int = 15000) -> Dict[str, Any]:
        """Perform drag and drop between DOM elements."""
        return self._run(self._drag_and_drop_internal, source_selector, target_selector, timeout_ms)

    def _drag_and_drop_internal(self, source_selector: str, target_selector: str, timeout_ms: int = 15000) -> Dict[str, Any]:
        try:
            page = self._ensure_browser()
            page.drag_and_drop(source_selector, target_selector, timeout=timeout_ms)
            return {"isError": False, "source": source_selector, "target": target_selector, "dropped": True}
        except Exception as exc:
            return {"isError": True, "error": str(exc)}

    def get_network_logs(self, limit: int = 50) -> Dict[str, Any]:
        """Retrieve recorded network traffic logs."""
        with self._lock:
            entries = list(self._network_logs[-limit:])
            return {"isError": False, "count": len(entries), "logs": entries}

    def close(self) -> None:
        """Close active browser and contexts."""
        with self._lock:
            try:
                self._executor.submit(self._close_internal).result(timeout=5.0)
            except Exception:
                pass
            self._executor.shutdown(wait=False)
            self._executor = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="hydra-playwright")

    def _close_internal(self) -> None:
        if self._page:
            try:
                self._page.close()
            except Exception:
                pass
            self._page = None
        if self._context:
            try:
                self._context.close()
            except Exception:
                pass
            self._context = None
        if self._browser:
            try:
                self._browser.close()
            except Exception:
                pass
            self._browser = None
        if self._playwright:
            try:
                self._playwright.stop()
            except Exception:
                pass
            self._playwright = None


class ComputerUseEngine:
    """
    Unified Sovereign Computer Use and Desktop Automation Engine.
    Combines coordinate safety bounds, OS controller, screen capture, and browser automation.
    """

    def __init__(self) -> None:
        self.bounds = CoordinateBounds()
        self.os = OSController(self.bounds)
        self.screen = ScreenCaptureEngine(self.bounds)
        self.browser = PlaywrightAutomationBridge(headless=True)
        self._lock = threading.RLock()
        self.reset_telemetry()

    def reset_telemetry(self) -> None:
        """Reset telemetry counters."""
        with getattr(self, "_lock", threading.RLock()):
            self._actions_executed: int = 0
            self._actions_failed: int = 0
            self.bounds.reset_telemetry()

    def dispatch(self, action: str, **kwargs: Any) -> Dict[str, Any]:
        """Dispatch unified computer use action."""
        act = (action or "").strip().lower()
        with self._lock:
            self._actions_executed += 1

        try:
            if act in ("screen_capture", "screenshot", "capture"):
                bbox = kwargs.get("bbox")
                path = kwargs.get("path")
                as_b64 = kwargs.get("as_base64", True)
                return self.screen.capture(bbox=bbox, save_path=path, as_base64=as_b64)

            elif act in ("mouse_move", "move"):
                x = int(kwargs.get("x", 0))
                y = int(kwargs.get("y", 0))
                smooth = kwargs.get("smooth", True)
                steps = int(kwargs.get("steps", 5))
                return self.os.mouse_move(x, y, smooth=smooth, steps=steps)

            elif act in ("mouse_click", "click"):
                x = kwargs.get("x")
                y = kwargs.get("y")
                ix = int(x) if x is not None else None
                iy = int(y) if y is not None else None
                button = kwargs.get("button", "left")
                double = bool(kwargs.get("double", False))
                return self.os.mouse_click(x=ix, y=iy, button=button, double=double)

            elif act in ("mouse_drag", "drag"):
                sx = int(kwargs.get("start_x", 0))
                sy = int(kwargs.get("start_y", 0))
                ex = int(kwargs.get("end_x", 0))
                ey = int(kwargs.get("end_y", 0))
                steps = int(kwargs.get("steps", 10))
                button = kwargs.get("button", "left")
                return self.os.mouse_drag(sx, sy, ex, ey, steps=steps, button=button)

            elif act in ("mouse_scroll", "scroll"):
                dx = int(kwargs.get("dx", 0))
                dy = int(kwargs.get("dy", 0))
                return self.os.mouse_scroll(dx=dx, dy=dy)

            elif act in ("key_press", "press"):
                key = kwargs.get("key", "")
                if not key:
                    return {"isError": True, "error": "Parameter key required"}
                return self.os.key_press(key)

            elif act in ("key_chord", "chord", "hotkey"):
                chord = kwargs.get("chord", "")
                if not chord:
                    return {"isError": True, "error": "Parameter chord required"}
                return self.os.key_chord(chord)

            elif act in ("normalize_coordinates", "normalize"):
                x = int(kwargs.get("x", 0))
                y = int(kwargs.get("y", 0))
                nx, ny = self.bounds.normalize(x, y)
                return {"isError": False, "x": x, "y": y, "norm_x": nx, "norm_y": ny}

            elif act in ("denormalize_coordinates", "denormalize"):
                nx = float(kwargs.get("norm_x", kwargs.get("x", 0.0)))
                ny = float(kwargs.get("norm_y", kwargs.get("y", 0.0)))
                px, py = self.bounds.denormalize(nx, ny)
                return {"isError": False, "norm_x": nx, "norm_y": ny, "pixel_x": px, "pixel_y": py}

            elif act in ("type_text", "type", "input"):
                text = kwargs.get("text", "")
                DANGEROUS_TYPE_PATTERNS = (
                    r"(?i)\brm\s+-(rf|fr)\s+[/~*]",
                    r"(?i)\bdel\s+/[fF]\s+/[sS]\s+/[qQ]\s+\*",
                    r"(?i)\bRemove-Item\b.*-Recurse.*-Force\s+([/\\*]|[a-z]:\\)",
                )
                for pat in DANGEROUS_TYPE_PATTERNS:
                    if re.search(pat, text):
                        return {"isError": True, "error": "Security violation: blocked typing potentially destructive command stream"}
                delay_ms = float(kwargs.get("delay_ms", 10.0))
                return self.os.type_text(text, delay_ms=delay_ms)

            elif act in ("window_action", "window"):
                sub = kwargs.get("sub_action", "active").lower()
                if sub == "active":
                    win = self.os.get_active_window()
                    return {"isError": False, "window": win}
                elif sub == "list":
                    wins = self.os.list_windows()
                    return {"isError": False, "windows": wins, "count": len(wins)}
                elif sub == "focus":
                    target = kwargs.get("target", "")
                    return self.os.focus_window(target)
                else:
                    return {"isError": True, "error": f"Unknown window sub_action: {sub}"}

            elif act in ("browser_navigate", "browse"):
                url = kwargs.get("url", "")
                if not url:
                    return {"isError": True, "error": "Parameter url required"}
                return self.browser.navigate(url)

            elif act in ("browser_inspect", "inspect_dom"):
                selector = kwargs.get("selector", "body")
                return self.browser.inspect_dom(selector=selector)

            elif act in ("browser_click", "click_element"):
                selector = kwargs.get("selector", "")
                if not selector:
                    return {"isError": True, "error": "Parameter selector required"}
                return self.browser.click_element(selector)

            elif act in ("browser_type", "type_element"):
                selector = kwargs.get("selector", "")
                text = kwargs.get("text", "")
                if not selector:
                    return {"isError": True, "error": "Parameter selector required"}
                return self.browser.type_element(selector, text)

            elif act in ("browser_screenshot", "page_screenshot"):
                path = kwargs.get("path")
                full_page = bool(kwargs.get("full_page", False))
                return self.browser.capture_screenshot(path=path, full_page=full_page)

            elif act in ("browser_pdf", "print_pdf"):
                path = kwargs.get("path")
                return self.browser.print_pdf(path=path)

            elif act in ("browser_network", "network_logs"):
                limit = int(kwargs.get("limit", 50))
                return self.browser.get_network_logs(limit=limit)

            elif act == "add_fence":
                label = kwargs.get("label", "restricted")
                x1 = int(kwargs.get("x1", 0))
                y1 = int(kwargs.get("y1", 0))
                x2 = int(kwargs.get("x2", 0))
                y2 = int(kwargs.get("y2", 0))
                self.bounds.add_fence(label, x1, y1, x2, y2)
                return {"isError": False, "label": label, "fences": self.bounds.list_fences()}


            elif act in ("fill_form", "browser_fill_form"):
                fields = kwargs.get("fields", {})
                form_sel = kwargs.get("form_selector")
                sub = bool(kwargs.get("submit", False))
                return self.browser.fill_form(fields, form_selector=form_sel, submit=sub)

            elif act in ("scroll_until_visible", "browser_scroll_until_visible"):
                selector = kwargs.get("selector", "")
                max_s = int(kwargs.get("max_scrolls", 10))
                step = int(kwargs.get("scroll_step", 400))
                return self.browser.scroll_until_visible(selector, max_scrolls=max_s, scroll_step=step)

            elif act in ("extract_table_data", "browser_extract_table"):
                selector = kwargs.get("selector", "table")
                return self.browser.extract_table_data(selector=selector)

            elif act in ("safe_drag_and_drop", "drag_and_drop"):
                src_sel = kwargs.get("source_selector") or kwargs.get("source")
                tgt_sel = kwargs.get("target_selector") or kwargs.get("target")
                if src_sel and tgt_sel:
                    return self.browser.drag_and_drop(str(src_sel), str(tgt_sel))
                fc = kwargs.get("from_coord") or (int(kwargs.get("from_x", kwargs.get("start_x", 0))), int(kwargs.get("from_y", kwargs.get("start_y", 0))))
                tc = kwargs.get("to_coord") or (int(kwargs.get("to_x", kwargs.get("end_x", 100))), int(kwargs.get("to_y", kwargs.get("end_y", 100))))
                stp = int(kwargs.get("steps", 10))
                btn = kwargs.get("button", "left")
                return self.safe_drag_and_drop(fc, tc, steps=stp, button=btn)

            elif act in ("find_window_by_title_pattern", "find_window"):
                pat = kwargs.get("pattern", "")
                return self.os.find_window_by_title_pattern(pat)

            elif act in ("set_window_bounds", "resize_window"):
                hwnd = int(kwargs.get("hwnd", 0))
                x = int(kwargs.get("x", 0))
                y = int(kwargs.get("y", 0))
                w = int(kwargs.get("width", 800))
                h = int(kwargs.get("height", 600))
                return self.os.set_window_bounds(hwnd, x, y, w, h)

            elif act in ("capture_active_window", "active_window_screenshot"):
                as_b64 = bool(kwargs.get("as_base64", True))
                return self.capture_active_window(as_base64=as_b64)

            elif act in ("safe_key_sequence", "key_sequence"):
                keys = kwargs.get("keys", [])
                interval = float(kwargs.get("interval_ms", 50.0))
                return self.os.safe_key_sequence(keys, interval_ms=interval)

            elif act == "remove_fence":
                label = kwargs.get("label", "")
                removed = self.bounds.remove_fence(label)
                return {"isError": False, "label": label, "removed": removed}

            else:
                with self._lock:
                    self._actions_failed += 1
                return {"isError": True, "error": f"Unknown computer use action: {action}"}

        except Exception as exc:
            with self._lock:
                self._actions_failed += 1
            return {"isError": True, "error": f"Computer use action '{action}' failed: {exc}"}

    def capture_active_window(self, as_base64: bool = True) -> Dict[str, Any]:
        """Capture screenshot of current active window bounding box."""
        win = self.os.get_active_window()
        rect = win.get("rect", {})
        left = max(0, rect.get("left", 0))
        top = max(0, rect.get("top", 0))
        right = max(left + 10, rect.get("right", left + 100))
        bottom = max(top + 10, rect.get("bottom", top + 100))
        bbox = (left, top, right, bottom)
        cap = self.screen.capture(bbox=bbox, as_base64=as_base64)
        return {"isError": False, "window": win, "capture": cap}

    def safe_drag_and_drop(
        self,
        from_coord: Tuple[int, int],
        to_coord: Tuple[int, int],
        steps: int = 10,
        button: str = "left",
    ) -> Dict[str, Any]:
        """Execute safe coordinate drag and drop with boundary guards."""
        fx, fy, _ = self.bounds.clip(from_coord[0], from_coord[1])
        tx, ty, _ = self.bounds.clip(to_coord[0], to_coord[1])
        safe_f, err_f = self.bounds.check_safety(fx, fy)
        if not safe_f:
            return {"isError": True, "error": f"From coordinate unsafe: {err_f}"}
        safe_t, err_t = self.bounds.check_safety(tx, ty)
        if not safe_t:
            return {"isError": True, "error": f"To coordinate unsafe: {err_t}"}
        return self.os.mouse_drag(fx, fy, tx, ty, steps=steps, button=button)


    def fill_form(
        self,
        fields: Dict[str, str],
        form_selector: Optional[str] = None,
        submit: bool = False,
    ) -> Dict[str, Any]:
        """Composite action: auto-fill web form fields."""
        return self.browser.fill_form(fields=fields, form_selector=form_selector, submit=submit)

    def scroll_until_visible(
        self,
        selector: str,
        max_scrolls: int = 10,
        scroll_step: int = 400,
        timeout_ms: int = 5000,
    ) -> Dict[str, Any]:
        """Composite action: scroll page until target element visible."""
        return self.browser.scroll_until_visible(
            selector=selector, max_scrolls=max_scrolls, scroll_step=scroll_step, timeout_ms=timeout_ms
        )

    def extract_table_data(self, selector: str = "table") -> Dict[str, Any]:
        """Composite action: extract structured table dataset."""
        return self.browser.extract_table_data(selector=selector)

    def find_window_by_title_pattern(
        self, pattern: str, visible_only: bool = True
    ) -> Dict[str, Any]:
        """Composite action: find windows matching regex title pattern."""
        return self.os.find_window_by_title_pattern(pattern=pattern, visible_only=visible_only)

    def set_window_bounds(
        self, hwnd: int, x: int, y: int, width: int, height: int
    ) -> Dict[str, Any]:
        """Composite action: reposition and resize window."""
        return self.os.set_window_bounds(hwnd=hwnd, x=x, y=y, width=width, height=height)

    def safe_key_sequence(
        self, keys: Sequence[str], interval_ms: float = 50.0
    ) -> Dict[str, Any]:
        """Composite action: safely execute sequence of key presses."""
        return self.os.safe_key_sequence(keys=keys, interval_ms=interval_ms)

    def get_telemetry(self) -> Dict[str, Any]:
        """Return comprehensive engine telemetry."""
        with self._lock:
            return {
                "actions_executed": self._actions_executed,
                "actions_failed": self._actions_failed,
                "bounds": self.bounds.get_telemetry(),
                "active_window": self.os.get_active_window(),
                "cursor": self.os.get_cursor_position(),
            }


_GLOBAL_COMPUTER_USE_ENGINE: Optional[ComputerUseEngine] = None


def get_computer_use_engine() -> ComputerUseEngine:
    """Return singleton ComputerUseEngine instance."""
    global _GLOBAL_COMPUTER_USE_ENGINE
    if _GLOBAL_COMPUTER_USE_ENGINE is None:
        _GLOBAL_COMPUTER_USE_ENGINE = ComputerUseEngine()
    return _GLOBAL_COMPUTER_USE_ENGINE


def reset_computer_use_engine() -> None:
    """Reset global ComputerUseEngine instance."""
    global _GLOBAL_COMPUTER_USE_ENGINE
    if _GLOBAL_COMPUTER_USE_ENGINE is not None:
        _GLOBAL_COMPUTER_USE_ENGINE.browser.close()
        _GLOBAL_COMPUTER_USE_ENGINE = None
