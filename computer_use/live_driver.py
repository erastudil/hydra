"""
Live Driver for Hydra computer use.

Runs Hydra's own ComputerUseEngine (hydra_cli.computer_use) INSIDE the
user's interactive Windows session and executes command files dropped by
a remote operator. This exists because OS-level input (SendInput family)
and screen capture only reach the real desktop from a process running in
that session; an SSH-spawned process lives in session 0 and its input
goes nowhere the user can see.

IPC contract (all under the workdir, default
C:\\Users\\jpm05\\hydra\\live-driver, override with HYDRA_LIVE_DRIVER_HOME):

  cmd\\<id>.json    one command: {"id": ..., "action": ..., "params": {...}}
  done\\<id>.json   result written atomically when the command finishes
  shots\\<name>.png screenshots captured by the screenshot action
  heartbeat.json   rewritten every loop: pid, session id, screen size
  driver.log       append-only action log
  glow_state.json  screen-glow state the driver maintains (executing,
                   last_activity, driver_pid); glow_ready.json /
                   glow_error.txt are the glow's startup handshake
  session_state.json  task-loop session state (active, loop_pid,
                   updated), written by a driver task loop (e.g. the
                   local model driver); while fresh (<= 30 s) and
                   active it holds the glow lit for the whole session,
                   including model think time between commands. The
                   driver deletes any stale session file at startup and
                   passes its path to the glow via --session.

Screen glow: starting the driver launches computer_use/screen_glow.py,
a click-through border glow shown only while commands execute (plus a
short idle grace, owned by the glow). The driver refuses to run if the
glow cannot start: an unglowed run would be a silent spec violation
(2026-10-20 audit: the glow is gated on computer-use activity, never a
static always-on border). Stopping the driver stops the glow.

Actions: screenshot, click, move, key, chord, paste, type, focus,
windows, active, launch, wait, quit. Every action is executed through
the engine's dispatch (paste/type are driver-level composites: paste
sets the session clipboard via Win32 and sends Ctrl+V through the
engine; type uses VkKeyScanW so punctuation maps to the real layout,
which the engine's type_text does not guarantee).

Safety posture (from the 2026-10-20 audit): the engine's return values
are never treated as proof. The operator verifies every step from the
screenshots this driver saves, and success of a task is only ever an
externally observed fact (a file on disk, a read screenshot).
"""

from __future__ import annotations

import ctypes
import json
import os
import subprocess
import sys
import time
import traceback
from ctypes import wintypes

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

WORKDIR = os.environ.get(
    "HYDRA_LIVE_DRIVER_HOME", r"C:\Users\jpm05\hydra\live-driver"
)
CMD_DIR = os.path.join(WORKDIR, "cmd")
DONE_DIR = os.path.join(WORKDIR, "done")
SHOTS_DIR = os.path.join(WORKDIR, "shots")
LOG_PATH = os.path.join(WORKDIR, "driver.log")
HEARTBEAT_PATH = os.path.join(WORKDIR, "heartbeat.json")
GLOW_SCRIPT = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "screen_glow.py"
)
GLOW_STATE_PATH = os.path.join(WORKDIR, "glow_state.json")
SESSION_STATE_PATH = os.path.join(WORKDIR, "session_state.json")
GLOW_READY_PATH = os.path.join(WORKDIR, "glow_ready.json")
GLOW_ERROR_PATH = os.path.join(WORKDIR, "glow_error.txt")

for _d in (CMD_DIR, DONE_DIR, SHOTS_DIR):
    os.makedirs(_d, exist_ok=True)


def log(msg: str) -> None:
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}"
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def session_id() -> int:
    try:
        sid = wintypes.DWORD(0)
        ok = ctypes.windll.kernel32.ProcessIdToSessionId(
            os.getpid(), ctypes.byref(sid)
        )
        return int(sid.value) if ok else -1
    except Exception:
        return -1


def set_clipboard_text(text: str) -> None:
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    CF_UNICODETEXT = 13
    GMEM_MOVEABLE = 0x0002
    kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
    kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    kernel32.GlobalLock.restype = wintypes.LPVOID
    kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    user32.SetClipboardData.restype = wintypes.HANDLE
    user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
    if not user32.OpenClipboard(None):
        raise RuntimeError("OpenClipboard failed")
    try:
        user32.EmptyClipboard()
        data = text.encode("utf-16-le") + b"\x00\x00"
        handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
        if not handle:
            raise RuntimeError("GlobalAlloc failed")
        ptr = kernel32.GlobalLock(handle)
        if not ptr:
            raise RuntimeError("GlobalLock failed")
        ctypes.memmove(ptr, data, len(data))
        kernel32.GlobalUnlock(handle)
        if not user32.SetClipboardData(CF_UNICODETEXT, handle):
            raise RuntimeError("SetClipboardData failed")
    finally:
        user32.CloseClipboard()


def type_text_vk(engine, text: str, delay_s: float = 0.012) -> dict:
    """Type text using VkKeyScanW so layout punctuation is correct."""
    user32 = ctypes.windll.user32
    KEYEVENTF_KEYUP = 0x0002
    typed = 0
    for ch in text:
        if ch == "\n":
            engine.os.key_press("enter")
            typed += 1
            continue
        if ch == "\t":
            engine.os.key_press("tab")
            typed += 1
            continue
        scan = user32.VkKeyScanW(ch)
        if scan == -1 or (scan & 0xFF) == 0xFF:
            return {
                "isError": True,
                "error": f"VkKeyScanW cannot map character {ch!r}",
                "typed_characters": typed,
            }
        vk = scan & 0xFF
        shift_state = (scan >> 8) & 0xFF
        mods = []
        if shift_state & 0x01:
            mods.append(0x10)  # VK_SHIFT
        if shift_state & 0x02:
            mods.append(0x11)  # VK_CONTROL
        if shift_state & 0x04:
            mods.append(0x12)  # VK_MENU (alt)
        for m in mods:
            user32.keybd_event(m, 0, 0, 0)
        user32.keybd_event(vk, 0, 0, 0)
        user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)
        for m in reversed(mods):
            user32.keybd_event(m, 0, KEYEVENTF_KEYUP, 0)
        typed += 1
        if delay_s > 0:
            time.sleep(delay_s)
    return {"isError": False, "typed_characters": typed, "text_length": len(text)}


class Driver:
    def __init__(self) -> None:
        from hydra_cli.computer_use import get_computer_use_engine

        self.engine = get_computer_use_engine()
        self.running = True
        self.glow_proc: subprocess.Popen | None = None
        self._last_activity = 0.0
        # The glow is part of the driver's contract with the owner: if
        # it cannot start, the driver refuses to run (see _start_glow).
        self._start_glow()
        log(
            f"driver start pid={os.getpid()} session={session_id()} "
            f"screen={self.engine.os.get_screen_size()}"
        )

    # -- screen glow ---------------------------------------------------

    def _write_glow_state(self, executing: bool) -> None:
        data = {
            "executing": executing,
            "last_activity": self._last_activity,
            "driver_pid": os.getpid(),
            "updated": time.time(),
        }
        tmp = GLOW_STATE_PATH + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f)
            os.replace(tmp, GLOW_STATE_PATH)
        except Exception:
            log("glow state write failed:\n" + traceback.format_exc(limit=3))

    def _start_glow(self) -> None:
        for path in (GLOW_READY_PATH, GLOW_ERROR_PATH):
            try:
                os.remove(path)
            except OSError:
                pass
        # A session file left by a dead loop must not light the new
        # driver's glow; loops write a fresh one when they start.
        try:
            os.remove(SESSION_STATE_PATH)
        except OSError:
            pass
        self._write_glow_state(False)
        self.glow_proc = subprocess.Popen(
            [
                sys.executable,
                GLOW_SCRIPT,
                "--state", GLOW_STATE_PATH,
                "--session", SESSION_STATE_PATH,
                "--ready", GLOW_READY_PATH,
                "--error", GLOW_ERROR_PATH,
                "--driver-pid", str(os.getpid()),
            ],
            cwd=REPO_ROOT,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        deadline = time.time() + 8.0
        while time.time() < deadline:
            if os.path.exists(GLOW_READY_PATH):
                log(f"glow ready pid={self.glow_proc.pid}")
                return
            if os.path.exists(GLOW_ERROR_PATH):
                try:
                    with open(GLOW_ERROR_PATH, "r", encoding="utf-8") as f:
                        detail = f.read().strip()
                except Exception:
                    detail = "(unreadable error file)"
                raise RuntimeError(f"screen glow failed to start: {detail}")
            if self.glow_proc.poll() is not None:
                raise RuntimeError(
                    "screen glow exited during startup "
                    f"rc={self.glow_proc.returncode}"
                )
            time.sleep(0.1)
        raise RuntimeError("screen glow did not signal ready within 8s")

    def _stop_glow(self) -> None:
        proc, self.glow_proc = self.glow_proc, None
        self._write_glow_state(False)
        if proc is None:
            return
        try:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=3)
        except Exception:
            log("glow stop failed:\n" + traceback.format_exc(limit=3))
        log("glow stopped")

    def heartbeat(self) -> None:
        data = {
            "pid": os.getpid(),
            "session_id": session_id(),
            "screen_size": self.engine.os.get_screen_size(),
            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        tmp = HEARTBEAT_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.replace(tmp, HEARTBEAT_PATH)

    def execute(self, cmd: dict) -> dict:
        action = str(cmd.get("action", "")).strip().lower()
        p = cmd.get("params") or {}
        eng = self.engine

        if action == "quit":
            self.running = False
            return {"isError": False, "quitting": True}
        if action == "wait":
            time.sleep(float(p.get("seconds", 1)))
            return {"isError": False, "waited": float(p.get("seconds", 1))}
        if action == "screenshot":
            name = str(p.get("name") or f"shot-{int(time.time())}.png")
            path = os.path.join(SHOTS_DIR, name)
            res = eng.dispatch("screenshot", path=path, as_base64=False)
            res.pop("base64", None)
            return res
        if action == "click":
            res = eng.dispatch(
                "mouse_click",
                x=p.get("x"),
                y=p.get("y"),
                button=p.get("button", "left"),
                double=bool(p.get("double", False)),
            )
            return res
        if action == "move":
            return eng.dispatch("mouse_move", x=p.get("x", 0), y=p.get("y", 0))
        if action == "key":
            return eng.dispatch("key_press", key=p.get("key", ""))
        if action == "chord":
            return eng.dispatch("key_chord", chord=p.get("chord", ""))
        if action == "paste":
            set_clipboard_text(str(p.get("text", "")))
            return eng.dispatch("key_chord", chord="ctrl+v")
        if action == "type":
            return type_text_vk(eng, str(p.get("text", "")))
        if action == "focus":
            return eng.dispatch("window_action", sub_action="focus", target=p.get("target", ""))
        if action == "windows":
            return eng.dispatch("window_action", sub_action="list")
        if action == "active":
            return eng.dispatch("window_action", sub_action="active")
        if action == "launch":
            return eng.dispatch("launch_app", target=p.get("target", ""), args=p.get("args") or [])
        return {"isError": True, "error": f"unknown driver action: {action}"}

    def run_once(self) -> None:
        try:
            self.heartbeat()
        except Exception:
            # A reader (scp) can hold heartbeat.json open on Windows and
            # make os.replace fail; the heartbeat is advisory, never let
            # it starve command processing.
            pass
        try:
            names = sorted(os.listdir(CMD_DIR))
        except Exception:
            return
        for name in names:
            if not name.endswith(".json"):
                continue
            cmd_id = name[:-5]
            done_path = os.path.join(DONE_DIR, name)
            if os.path.exists(done_path):
                continue
            cmd_path = os.path.join(CMD_DIR, name)
            try:
                with open(cmd_path, "r", encoding="utf-8") as f:
                    cmd = json.load(f)
            except PermissionError:
                # The file is still being copied in (scp holds it open);
                # skip this pass and read it once it is fully written.
                continue
            except Exception as exc:
                result = {"id": cmd_id, "ok": False, "error": f"bad command file: {exc}"}
                self._write_done(done_path, result)
                continue
            log(f"cmd {cmd_id}: {cmd.get('action')} {cmd.get('params')}")
            started = time.time()
            self._write_glow_state(True)
            try:
                res = self.execute(cmd)
                ok = not (isinstance(res, dict) and res.get("isError"))
                result = {"id": cmd_id, "ok": ok, "result": res}
            except Exception:
                result = {
                    "id": cmd_id,
                    "ok": False,
                    "error": traceback.format_exc(limit=5),
                }
            finally:
                self._last_activity = time.time()
                self._write_glow_state(False)
            result["elapsed_s"] = round(time.time() - started, 3)
            self._write_done(done_path, result)
            log(f"done {cmd_id}: ok={result['ok']}")
            if not self.running:
                break

    @staticmethod
    def _write_done(done_path: str, result: dict) -> None:
        tmp = done_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, default=str)
        os.replace(tmp, done_path)

    def loop(self) -> None:
        try:
            while self.running:
                try:
                    self.run_once()
                except Exception:
                    log("loop error:\n" + traceback.format_exc(limit=5))
                time.sleep(0.25)
        finally:
            self._stop_glow()
        log("driver exit")


if __name__ == "__main__":
    try:
        Driver().loop()
    except Exception:
        # Startup failures (including a glow that cannot start) are
        # reported in driver.log and exit non-zero; the driver never
        # runs silently without its glow.
        log("FATAL: driver failed to start\n" + traceback.format_exc(limit=5))
        sys.exit(1)
