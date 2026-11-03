"""
Screen Glow for Hydra computer use.

A full-screen border glow shown ONLY while the live driver is actively
executing commands (plus a short idle grace after the last command).
This implements the spec graded MISSING in the 2026-10-20 Hydra audit:
full-screen, gradient, green, pulsing, gated on the computer-use-active
state -- Codex-style. The anti-pattern it replaces is a solid green
border that is always on.

Rendering: a pure Win32 layered window with PER-PIXEL alpha
(UpdateLayeredWindow over a 32bpp DIB). The falloff lives in the alpha
channel: intensity follows cos^2 from the screen edge across FRAME_PX
pixels and reaches zero with zero slope, so the glow fades to nothing
with no internal edge line. (The first build drew opaque color-keyed
bands; any banded design keeps a visible last band, which is why this
build renders per-pixel alpha instead. Owner tuning, 2026-10-27:
thicker, ~80% peak opacity, edgeless fade; the pulse is unchanged --
1.6 s period, intensity 0.62..1.0.)

This process is a pure display of driver state. The driver
(live_driver.py) launches it, maintains a JSON state file, and refuses
to run if the glow cannot start. The glow:

  state file (written atomically by the driver):
    {"executing": bool, "last_activity": epoch, "driver_pid": int}

  visible  <=>  executing is true
                OR now - last_activity <= GRACE_S
                OR a driver task loop's session is active (below)

Session-level glow (owner ruling, 2026-10-27): per-command execution
flickers when a driver loop's think time (e.g. local model inference)
exceeds GRACE_S. A task loop therefore asserts a session file:

  session file (written by the task loop, path passed with --session):
    {"active": bool, "loop_pid": int, "updated": ISO-8601 timestamp}

  session visible  <=>  active is true AND the file's updated timestamp
                        is at most SESSION_FRESH_S (30 s) old. The loop
                        heartbeats it at least once per second for as
                        long as it lives, including during inference;
                        it clears on loop exit, or when heartbeats stop
                        for ~30 s, so a hung loop cannot leave the glow
                        lit forever. The pulse is unchanged.

  - watches the driver pid and exits when the driver dies, so the glow
    can never outlive the thing it describes;
  - writes a ready file once the overlay window exists and its first
    frame has been presented, so the driver can verify startup before
    accepting work;
  - writes an error file and exits non-zero if setup fails.

The overlay window is click-through (WS_EX_TRANSPARENT), always-on-top,
and never takes focus (WS_EX_NOACTIVATE).
"""

from __future__ import annotations

import argparse
import ctypes
import json
import math
import os
import sys
import time
import traceback
from ctypes import wintypes

GRACE_S = 2.0
SESSION_FRESH_S = 30.0
TICK_MS = 60
FRAME_PX = 36
PULSE_PERIOD_S = 1.6
OPACITY = 0.8  # peak opacity at the screen edge (alpha 204/255)

# gradient endpoints (outer -> inner), green per spec
OUTER = (64, 255, 120)
INNER = (0, 64, 22)

WS_POPUP = 0x80000000
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOPMOST = 0x00000008
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000
SW_HIDE = 0
SW_SHOWNOACTIVATE = 4
WM_DESTROY = 0x0002
WM_TIMER = 0x0113
TIMER_ID = 1
AC_SRC_OVER = 0x00
AC_SRC_ALPHA = 0x01
ULW_ALPHA = 0x02
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
STILL_ACTIVE = 259
ERROR_CLASS_ALREADY_EXISTS = 1410

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
gdi32 = ctypes.windll.gdi32

WNDPROC = ctypes.WINFUNCTYPE(
    ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT,
    ctypes.c_size_t, ctypes.c_ssize_t,
)


class WNDCLASSEXW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.UINT),
        ("style", wintypes.UINT),
        ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HICON),
        ("hCursor", wintypes.HANDLE),
        ("hbrBackground", wintypes.HANDLE),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
        ("hIconSm", wintypes.HICON),
    ]


class BLENDFUNCTION(ctypes.Structure):
    _fields_ = [
        ("BlendOp", ctypes.c_ubyte),
        ("BlendFlags", ctypes.c_ubyte),
        ("SourceConstantAlpha", ctypes.c_ubyte),
        ("AlphaFormat", ctypes.c_ubyte),
    ]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", ctypes.c_long),
        ("biHeight", ctypes.c_long),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", ctypes.c_long),
        ("biYPelsPerMeter", ctypes.c_long),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class SIZE(ctypes.Structure):
    _fields_ = [("cx", ctypes.c_long), ("cy", ctypes.c_long)]


user32.DefWindowProcW.restype = ctypes.c_ssize_t
user32.DefWindowProcW.argtypes = [
    wintypes.HWND, wintypes.UINT, ctypes.c_size_t, ctypes.c_ssize_t,
]
user32.CreateWindowExW.restype = wintypes.HWND
user32.CreateWindowExW.argtypes = [
    wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    wintypes.HWND, wintypes.HANDLE, wintypes.HINSTANCE, ctypes.c_void_p,
]
user32.UpdateLayeredWindow.restype = wintypes.BOOL
user32.UpdateLayeredWindow.argtypes = [
    wintypes.HWND, wintypes.HDC, ctypes.POINTER(POINT), ctypes.POINTER(SIZE),
    wintypes.HDC, ctypes.POINTER(POINT), wintypes.COLORREF,
    ctypes.POINTER(BLENDFUNCTION), wintypes.DWORD,
]
gdi32.CreateDIBSection.restype = wintypes.HANDLE
gdi32.CreateDIBSection.argtypes = [
    wintypes.HDC, ctypes.POINTER(BITMAPINFO), wintypes.UINT,
    ctypes.POINTER(ctypes.c_void_p), wintypes.HANDLE, wintypes.DWORD,
]
# Every other Win32 call gets explicit signatures too: without them
# ctypes truncates 64-bit handles to C int, which overflows depending
# on the handle value the kernel happens to hand out (observed live:
# SelectObject argument 2 OverflowError on one launch, fine on the
# previous one -- a handle lottery, not a logic difference).
kernel32.GetModuleHandleW.restype = wintypes.HINSTANCE
kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.GetExitCodeProcess.restype = wintypes.BOOL
kernel32.GetExitCodeProcess.argtypes = [
    wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD),
]
kernel32.CloseHandle.restype = wintypes.BOOL
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
user32.RegisterClassExW.restype = wintypes.ATOM
user32.RegisterClassExW.argtypes = [ctypes.POINTER(WNDCLASSEXW)]
user32.GetSystemMetrics.restype = ctypes.c_int
user32.GetSystemMetrics.argtypes = [ctypes.c_int]
user32.SetTimer.restype = ctypes.c_size_t
user32.SetTimer.argtypes = [
    wintypes.HWND, ctypes.c_size_t, wintypes.UINT, ctypes.c_void_p,
]
user32.ShowWindow.restype = wintypes.BOOL
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.DestroyWindow.restype = wintypes.BOOL
user32.DestroyWindow.argtypes = [wintypes.HWND]
user32.GetDC.restype = wintypes.HDC
user32.GetDC.argtypes = [wintypes.HWND]
user32.ReleaseDC.restype = ctypes.c_int
user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
user32.DispatchMessageW.restype = ctypes.c_ssize_t
user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
gdi32.CreateCompatibleDC.restype = wintypes.HDC
gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
gdi32.SelectObject.restype = wintypes.HANDLE
gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HANDLE]
gdi32.DeleteObject.restype = wintypes.BOOL
gdi32.DeleteObject.argtypes = [wintypes.HANDLE]
gdi32.DeleteDC.restype = wintypes.BOOL
gdi32.DeleteDC.argtypes = [wintypes.HDC]


def pid_alive(pid: int) -> bool:
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return False
    try:
        code = wintypes.DWORD(0)
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return False
        return code.value == STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


class Glow:
    CLASS_NAME = "HydraScreenGlowWnd"

    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.shown = False
        self.dead_checks = 0
        self.seen_state = False
        self.missing_since: float | None = None

        # Physical pixels, matching the engine's screen coordinates.
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            pass
        self.width = int(user32.GetSystemMetrics(0))
        self.height = int(user32.GetSystemMetrics(1))
        if self.width <= 0 or self.height <= 0:
            raise RuntimeError(f"bad screen size {self.width}x{self.height}")

        self._wndproc_cb = WNDPROC(self._wndproc)
        self.hwnd = self._create_window()
        self.hdc_screen = user32.GetDC(0)
        self.mem_dc, self.hbmp, self.bits = self._create_surface()
        self._render(0.8)
        if not self._present():
            raise RuntimeError("first UpdateLayeredWindow failed")

    # -- window setup --------------------------------------------------

    def _wndproc(self, hwnd, msg, wparam, lparam):
        if msg == WM_TIMER:
            self.tick()
            return 0
        if msg == WM_DESTROY:
            user32.PostQuitMessage(0)
            return 0
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _create_window(self) -> int:
        hinstance = kernel32.GetModuleHandleW(None)
        wc = WNDCLASSEXW()
        wc.cbSize = ctypes.sizeof(WNDCLASSEXW)
        wc.style = 0
        wc.lpfnWndProc = self._wndproc_cb
        wc.cbClsExtra = 0
        wc.cbWndExtra = 0
        wc.hInstance = hinstance
        wc.hIcon = None
        wc.hCursor = None
        wc.hbrBackground = None
        wc.lpszMenuName = None
        wc.lpszClassName = self.CLASS_NAME
        wc.hIconSm = None
        if not user32.RegisterClassExW(ctypes.byref(wc)):
            err = kernel32.GetLastError()
            if err != ERROR_CLASS_ALREADY_EXISTS:
                raise RuntimeError(f"RegisterClassExW failed: {err}")
        exstyle = (
            WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOPMOST
            | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE
        )
        hwnd = user32.CreateWindowExW(
            exstyle, self.CLASS_NAME, None, WS_POPUP,
            0, 0, self.width, self.height, None, None, hinstance, None,
        )
        if not hwnd:
            raise RuntimeError(
                f"CreateWindowExW failed: {kernel32.GetLastError()}"
            )
        if not user32.SetTimer(hwnd, TIMER_ID, TICK_MS, None):
            raise RuntimeError("SetTimer failed")
        return hwnd

    def _create_surface(self):
        bmi = BITMAPINFO()
        bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bmi.bmiHeader.biWidth = self.width
        bmi.bmiHeader.biHeight = -self.height  # top-down
        bmi.bmiHeader.biPlanes = 1
        bmi.bmiHeader.biBitCount = 32
        bmi.bmiHeader.biCompression = 0
        bits = ctypes.c_void_p()
        hbmp = gdi32.CreateDIBSection(
            self.hdc_screen, ctypes.byref(bmi), 0,
            ctypes.byref(bits), None, 0,
        )
        if not hbmp or not bits.value:
            raise RuntimeError("CreateDIBSection failed")
        ctypes.memset(bits.value, 0, self.width * self.height * 4)
        mem_dc = gdi32.CreateCompatibleDC(self.hdc_screen)
        if not mem_dc:
            raise RuntimeError("CreateCompatibleDC failed")
        gdi32.SelectObject(mem_dc, hbmp)
        return mem_dc, hbmp, bits.value

    # -- rendering -----------------------------------------------------

    def _pixel(self, d: int, pulse: float) -> bytes:
        """Premultiplied BGRA for a pixel d px from the nearest edge."""
        t = d / FRAME_PX
        profile = math.cos(t * math.pi / 2) ** 2
        alpha = int(255 * OPACITY * pulse * profile + 0.5)
        if alpha <= 0:
            return b"\x00\x00\x00\x00"
        r = OUTER[0] + (INNER[0] - OUTER[0]) * t
        g = OUTER[1] + (INNER[1] - OUTER[1]) * t
        b = OUTER[2] + (INNER[2] - OUTER[2]) * t
        return bytes((
            int(b * alpha / 255),
            int(g * alpha / 255),
            int(r * alpha / 255),
            alpha,
        ))

    def _row_template(self, dy: int, pulse: float) -> bytes:
        """One scanline whose nearest horizontal edge is dy px away.

        dy == FRAME_PX is the sentinel for interior rows: only the
        left/right fade columns are non-zero there.
        """
        w, f = self.width, FRAME_PX
        left = b"".join(
            self._pixel(min(x, dy), pulse) for x in range(f)
        )
        mid_pixel = self._pixel(dy, pulse) if dy < f else b"\x00\x00\x00\x00"
        mid = mid_pixel * (w - 2 * f)
        right = b"".join(
            self._pixel(min(w - 1 - x, dy), pulse)
            for x in range(w - f, w)
        )
        return left + mid + right

    def _render(self, pulse: float) -> None:
        w, h, f = self.width, self.height, FRAME_PX
        row_bytes = w * 4
        templates = {
            dy: self._row_template(dy, pulse) for dy in range(f + 1)
        }
        for y in range(h):
            dy = min(y, h - 1 - y, f)
            ctypes.memmove(
                self.bits + y * row_bytes, templates[dy], row_bytes
            )

    def _present(self) -> bool:
        blend = BLENDFUNCTION(AC_SRC_OVER, 0, 255, AC_SRC_ALPHA)
        dst = POINT(0, 0)
        src = POINT(0, 0)
        size = SIZE(self.width, self.height)
        return bool(user32.UpdateLayeredWindow(
            self.hwnd, self.hdc_screen, ctypes.byref(dst),
            ctypes.byref(size), self.mem_dc, ctypes.byref(src),
            0, ctypes.byref(blend), ULW_ALPHA,
        ))

    # -- state ---------------------------------------------------------

    def read_state(self) -> dict | None:
        try:
            with open(self.args.state, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None

    def session_active(self) -> bool:
        """True while a task loop's session file is fresh and active."""
        path = getattr(self.args, "session", None)
        if not path:
            return False
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not data.get("active"):
                return False
            from datetime import datetime

            updated = datetime.fromisoformat(str(data.get("updated")))
            age = time.time() - updated.timestamp()
            return age <= SESSION_FRESH_S
        except Exception:
            return False

    def should_exit(self, state: dict | None) -> bool:
        if state is not None:
            self.seen_state = True
            self.missing_since = None
        elif self.seen_state:
            if self.missing_since is None:
                self.missing_since = time.time()
            elif time.time() - self.missing_since > 5.0:
                return True
        if pid_alive(self.args.driver_pid):
            self.dead_checks = 0
            return False
        self.dead_checks += 1
        # ~1s of consecutive dead readings before believing it.
        return self.dead_checks >= 16

    # -- main loop -----------------------------------------------------

    def tick(self) -> None:
        state = self.read_state()
        if self.should_exit(state):
            user32.DestroyWindow(self.hwnd)
            return
        active = False
        if state is not None:
            if state.get("executing"):
                active = True
            else:
                last = float(state.get("last_activity") or 0.0)
                active = (time.time() - last) <= GRACE_S
        if not active and self.session_active():
            active = True
        if active and not self.shown:
            user32.ShowWindow(self.hwnd, SW_SHOWNOACTIVATE)
            self.shown = True
        elif not active and self.shown:
            user32.ShowWindow(self.hwnd, SW_HIDE)
            self.shown = False
        if self.shown:
            phase = (time.time() % PULSE_PERIOD_S) / PULSE_PERIOD_S
            pulse = 0.62 + 0.38 * (0.5 + 0.5 * math.sin(2 * math.pi * phase))
            self._render(pulse)
            self._present()

    def run(self) -> None:
        # Signal readiness only after the window exists and a first
        # frame has been presented successfully.
        self.read_state()
        payload = {"pid": os.getpid(), "driver_pid": self.args.driver_pid,
                   "time": time.strftime("%Y-%m-%d %H:%M:%S")}
        tmp = self.args.ready + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f)
        os.replace(tmp, self.args.ready)
        msg = wintypes.MSG()
        while True:
            ret = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if ret in (0, -1):
                break
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        try:
            gdi32.DeleteObject(self.hbmp)
            gdi32.DeleteDC(self.mem_dc)
            user32.ReleaseDC(0, self.hdc_screen)
        except Exception:
            pass


def main() -> int:
    parser = argparse.ArgumentParser(description="Hydra computer-use screen glow")
    parser.add_argument("--state", required=True)
    parser.add_argument("--session", default=None,
                        help="optional task-loop session state file; while it "
                             "is fresh and active the glow stays lit")
    parser.add_argument("--ready", required=True)
    parser.add_argument("--error", required=True)
    parser.add_argument("--driver-pid", type=int, required=True)
    args = parser.parse_args()
    try:
        Glow(args).run()
    except Exception:
        try:
            with open(args.error, "w", encoding="utf-8") as f:
                f.write(traceback.format_exc())
        except Exception:
            pass
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
