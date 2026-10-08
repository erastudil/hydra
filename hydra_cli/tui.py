"""
Hydra TUI composer.

An inline prompt_toolkit application pinned beneath normal terminal scrollback:
a live status line, a rounded input box, and a footer of key hints plus a
context gauge. The REPL keeps the main thread; the composer runs on a
background thread and the two meet through queues:

  read_line()                  REPL blocks here for the next instruction
  ask()                        agent confirmation prompts answered from the composer
  ui.push_steering / drain     in-flight directives consumed by run_agent_loop

REPL output passes through a line gate into prompt_toolkit's StdoutProxy, so
text lands above the composer in whole lines and the composer re-lays out at
the current width on every redraw. Native terminal scrollback stays intact.

Keys: Enter sends; while a turn runs Enter queues and a second Enter on the
empty box steers the queued text into the running turn; Alt+Enter or Ctrl+J
inserts a newline; Ctrl+Space toggles dictation; Escape interrupts, clears,
or exits on double press; Ctrl+C copies the selection and Ctrl+V pastes;
mouse click moves the cursor and drag selects; mouse wheel scrolls the
terminal history above the pinned composer; Backspace or Delete removes
the whole selection; Tab completes slash commands.
"""

from __future__ import annotations

import _thread
import asyncio
import atexit
import math
import os
import queue
import re
import shutil
import signal
import sys
import threading
import time
from contextlib import contextmanager
from typing import Any, Callable, Dict, Iterator, List, Optional, Sequence, Tuple

from prompt_toolkit.application import Application
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.clipboard import Clipboard
from prompt_toolkit.filters import has_selection
from prompt_toolkit.formatted_text import fragment_list_width, to_formatted_text
from prompt_toolkit.history import FileHistory, History, InMemoryHistory
from prompt_toolkit.key_binding import KeyBindings, merge_key_bindings
from prompt_toolkit.key_binding.defaults import load_key_bindings
from prompt_toolkit.keys import Keys
from prompt_toolkit.layout import HSplit, Layout, VSplit, Window
from prompt_toolkit.layout.controls import BufferControl, FormattedTextControl
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.layout.mouse_handlers import MouseHandlers
from prompt_toolkit.layout.processors import Processor, Transformation, TransformationInput
from prompt_toolkit.mouse_events import MouseEvent, MouseEventType
from prompt_toolkit.output.color_depth import ColorDepth
from prompt_toolkit.patch_stdout import StdoutProxy
from prompt_toolkit.styles import Style
from prompt_toolkit.utils import get_cwidth

from hydra_cli import ui

Fragments = List[Tuple[str, str]]

_EOF = object()
_INTERRUPT = object()
_ANSI_ERASE = re.compile(r"\x1b\[[0-2]?K")

SPINNER_UNI = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
SPINNER_ASCII = "|/-\\"
METER_UNI = " ▁▂▃▄▅▆▇█"
METER_ASCII = " .:-=+*#%@"


def _should_wrap(text: str, index: int, x: int, width: int, char_width: int) -> bool:
    """True when the next character starts on the following visual row.

    A word that fits on an empty row moves there whole. A word longer than the
    row wraps on the character that crosses the width.
    """
    ch = text[index]
    if x > 0 and not ch.isspace() and (index == 0 or text[index - 1].isspace()):
        word_w = 0
        j = index
        while j < len(text) and not text[j].isspace():
            word_w += get_cwidth(text[j])
            j += 1
            if word_w > width:
                break
        if 0 < word_w <= width and x + word_w > width:
            return True
    return x + char_width > width


def _hydra_word_wrap_break(line: Sequence[Tuple[str, str]], col: int, x: int, char_width: int, width: int, c: str) -> bool:
    text = "".join(frag[1] for frag in line if len(frag) > 1 and frag[1])
    if col < 0 or col >= len(text):
        return x + char_width > width
    return _should_wrap(text, col, x, width, char_width)


def _prefix_width(get_line_prefix: Optional[Callable[..., Any]], lineno: int, wrap_count: int) -> int:
    if not get_line_prefix:
        return 0
    return fragment_list_width(to_formatted_text(get_line_prefix(lineno, wrap_count)))


def word_wrap_height(
    fragments: Sequence[Tuple[str, str]],
    width: int,
    get_line_prefix: Optional[Callable[..., Any]],
    lineno: int,
    slice_stop: Optional[int] = None,
) -> int:
    """Visual row count for one buffer line under the same breaks the window paints."""
    text = "".join(part for _, part, *_rest in fragments if part)
    if slice_stop is not None:
        text = text[:slice_stop]
    if width <= 0:
        return 10**8
    height = 1
    x = _prefix_width(get_line_prefix, lineno, 0)
    if x >= width:
        return 10**8
    for i, ch in enumerate(text):
        cw = get_cwidth(ch)
        if _should_wrap(text, i, x, width, cw):
            height += 1
            x = _prefix_width(get_line_prefix, lineno, height - 1)
            if x >= width:
                return 10**8
        x += cw
    return max(1, height)


def _install_word_wrap() -> None:
    """Teach prompt_toolkit's line wrapper to break on word boundaries."""
    import inspect
    import textwrap

    from prompt_toolkit.layout.containers import Window

    if getattr(Window, "_hydra_word_wrap", False):
        return
    needle = "if wrap_lines and x + char_width > width:"
    try:
        src = inspect.getsource(Window._copy_body)
    except OSError:
        return
    if needle not in src:
        return
    src = textwrap.dedent(src).replace(
        needle,
        "if wrap_lines and _hydra_word_wrap_break(line, col, x, char_width, width, c):",
        1,
    )
    globs = dict(Window._copy_body.__globals__)
    globs["_hydra_word_wrap_break"] = _hydra_word_wrap_break
    ns: Dict[str, Any] = {}
    exec(src, globs, ns)
    Window._copy_body = ns["_copy_body"]  # type: ignore[method-assign]
    Window._hydra_word_wrap = True  # type: ignore[attr-defined]


def _win32_scroll_viewport(lines: int) -> bool:
    """Move the visible console window within the screen buffer (negative = older history)."""
    if os.name != "nt" or lines == 0:
        return False
    try:
        import ctypes
        from ctypes import wintypes

        class COORD(ctypes.Structure):
            _fields_ = [("X", wintypes.SHORT), ("Y", wintypes.SHORT)]

        class SMALL_RECT(ctypes.Structure):
            _fields_ = [
                ("Left", wintypes.SHORT),
                ("Top", wintypes.SHORT),
                ("Right", wintypes.SHORT),
                ("Bottom", wintypes.SHORT),
            ]

        class CONSOLE_SCREEN_BUFFER_INFO(ctypes.Structure):
            _fields_ = [
                ("dwSize", COORD),
                ("dwCursorPosition", COORD),
                ("wAttributes", wintypes.WORD),
                ("srWindow", SMALL_RECT),
                ("dwMaximumWindowSize", COORD),
            ]

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
        info = CONSOLE_SCREEN_BUFFER_INFO()
        if not kernel32.GetConsoleScreenBufferInfo(handle, ctypes.byref(info)):
            return False
        sr = info.srWindow
        height = sr.Bottom - sr.Top
        width = sr.Right - sr.Left
        max_top = max(0, info.dwSize.Y - height - 1)
        new_top = max(0, min(max_top, sr.Top + lines))
        if new_top == sr.Top:
            return False
        rect = SMALL_RECT(0, new_top, width, new_top + height)
        return bool(kernel32.SetConsoleWindowInfo(handle, True, ctypes.byref(rect)))
    except Exception:
        return False


def scroll_terminal_history(lines: int) -> bool:
    """Scroll the host terminal's history viewport when the console API supports it."""
    if lines == 0:
        return False
    return _win32_scroll_viewport(lines)


class _PromptControl(BufferControl):
    """Buffer whose preferred height follows word breaks, matching the painted rows."""

    def __init__(self, *args: Any, on_wheel: Optional[Callable[[int], None]] = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._on_wheel = on_wheel

    def create_content(self, width: int, height: Optional[int]) -> Any:
        content = super().create_content(width, height)
        get_line = content.get_line

        def get_height_for_line(
            lineno: int,
            line_width: int,
            get_line_prefix: Optional[Callable[..., Any]],
            slice_stop: Optional[int] = None,
        ) -> int:
            return word_wrap_height(get_line(lineno), line_width, get_line_prefix, lineno, slice_stop)

        content.get_height_for_line = get_height_for_line
        return content

    def mouse_handler(self, mouse_event: MouseEvent) -> Any:
        if mouse_event.event_type == MouseEventType.SCROLL_UP:
            if self._on_wheel is not None:
                self._on_wheel(-3)
            else:
                scroll_terminal_history(-3)
            return None
        if mouse_event.event_type == MouseEventType.SCROLL_DOWN:
            if self._on_wheel is not None:
                self._on_wheel(3)
            else:
                scroll_terminal_history(3)
            return None
        return super().mouse_handler(mouse_event)


class _WheelRoot(HSplit):
    """HSplit that captures mouse-wheel events over the whole composer chrome."""

    def __init__(self, *args: Any, on_wheel: Optional[Callable[[int], None]] = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._on_wheel = on_wheel

    def write_to_screen(
        self,
        screen: Any,
        mouse_handlers: MouseHandlers,
        write_position: Any,
        parent_style: str,
        erase_bg: bool,
        z_index: Optional[int],
    ) -> None:
        super().write_to_screen(screen, mouse_handlers, write_position, parent_style, erase_bg, z_index)
        if self._on_wheel is None:
            return

        def wrap(handler: Callable[[MouseEvent], Any]) -> Callable[[MouseEvent], Any]:
            def combined(event: MouseEvent) -> Any:
                if event.event_type == MouseEventType.SCROLL_UP:
                    self._on_wheel(-3)
                    return None
                if event.event_type == MouseEventType.SCROLL_DOWN:
                    self._on_wheel(3)
                    return None
                return handler(event)

            return combined

        handlers = mouse_handlers.mouse_handlers
        for y in range(write_position.ypos, write_position.ypos + write_position.height):
            row = handlers[y]
            for x in range(write_position.xpos, write_position.xpos + write_position.width):
                row[x] = wrap(row[x])


class _SystemClipboard(Clipboard):
    """Windows clipboard. Other hosts keep an in-memory board when the OS board is closed."""

    def __init__(self) -> None:
        from prompt_toolkit.clipboard import ClipboardData
        from prompt_toolkit.selection import SelectionType

        self._ClipboardData = ClipboardData
        self._SelectionType = SelectionType
        self._memory: Optional[Any] = None

    def set_data(self, data: Any) -> None:
        self._memory = data
        if os.name == "nt":
            _win32_set_clipboard(data.text)
            return
        _foreign_set_clipboard(data.text)

    def get_data(self) -> Any:
        text = _win32_get_clipboard() if os.name == "nt" else _foreign_get_clipboard()
        if text is None:
            return self._memory or self._ClipboardData()
        if self._memory is not None and self._memory.text == text:
            return self._memory
        kind = self._SelectionType.LINES if "\n" in text else self._SelectionType.CHARACTERS
        return self._ClipboardData(text=text, type=kind)


def _win32_set_clipboard(text: str) -> None:
    import ctypes
    from ctypes import wintypes

    CF_UNICODETEXT = 13
    GMEM_MOVEABLE = 0x0002
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    kernel32.GlobalAlloc.restype = ctypes.c_void_p
    kernel32.GlobalLock.restype = ctypes.c_void_p
    user32.SetClipboardData.argtypes = [wintypes.UINT, ctypes.c_void_p]
    if not user32.OpenClipboard(None):
        return
    try:
        user32.EmptyClipboard()
        raw = text.encode("utf-16-le") + b"\x00\x00"
        handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(raw))
        if not handle:
            return
        locked = kernel32.GlobalLock(handle)
        ctypes.memmove(locked, raw, len(raw))
        kernel32.GlobalUnlock(handle)
        if not user32.SetClipboardData(CF_UNICODETEXT, handle):
            kernel32.GlobalFree(handle)
    finally:
        user32.CloseClipboard()


def _win32_get_clipboard() -> Optional[str]:
    import ctypes
    from ctypes import wintypes

    CF_UNICODETEXT = 13
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    user32.GetClipboardData.restype = ctypes.c_void_p
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
    if not user32.OpenClipboard(None):
        return None
    try:
        handle = user32.GetClipboardData(CF_UNICODETEXT)
        if not handle:
            return ""
        locked = kernel32.GlobalLock(handle)
        if not locked:
            return ""
        try:
            return ctypes.wstring_at(locked)
        finally:
            kernel32.GlobalUnlock(handle)
    finally:
        user32.CloseClipboard()


def _foreign_set_clipboard(text: str) -> None:
    try:
        import pyperclip
        pyperclip.copy(text)
    except Exception:
        return


def _foreign_get_clipboard() -> Optional[str]:
    try:
        import pyperclip
        return pyperclip.paste()
    except Exception:
        return None


def _hex(rgb: Tuple[int, int, int]) -> str:
    return "#{:02x}{:02x}{:02x}".format(*rgb)


def _trim(text: str, width: int) -> str:
    """Cut text to a display width, marking the cut with an ellipsis."""
    if width <= 0:
        return ""
    if get_cwidth(text) <= width:
        return text
    out = ""
    for ch in text:
        if get_cwidth(out + ch) > width - 1:
            break
        out += ch
    return out + "…"


def _frag_width(frags: Fragments) -> int:
    return sum(get_cwidth(text) for _, text in frags)


class _LineGate:
    """Text stream that forwards only complete lines to the StdoutProxy.

    Partial writes wait for their newline, so a half-written line never splits
    the composer redraw. Carriage-return overwrites collapse to their final
    segment and erase-line escapes drop, which keeps spinner-style writers from
    leaving debris in scrollback.
    """

    def __init__(self, proxy: StdoutProxy, original: Any) -> None:
        self._proxy = proxy
        self._original = original
        self._partial = ""
        self._lock = threading.Lock()

    @staticmethod
    def _settle(line: str) -> str:
        line = _ANSI_ERASE.sub("", line).rstrip("\r")
        if "\r" in line:
            segments = [seg for seg in line.split("\r") if seg]
            line = segments[-1] if segments else ""
        return line

    def write(self, data: Any) -> int:
        if not isinstance(data, str):
            data = str(data)
        if not data:
            return 0
        with self._lock:
            text = self._partial + data
            if "\n" not in text:
                self._partial = text
                return len(data)
            head, _, self._partial = text.rpartition("\n")
            payload = "\n".join(self._settle(line) for line in head.split("\n")) + "\n"
            self._proxy.write(payload)
        return len(data)

    def writelines(self, lines: Sequence[str]) -> None:
        for line in lines:
            self.write(line)

    def flush(self) -> None:
        return None

    def drain(self) -> None:
        with self._lock:
            if self._partial:
                self._proxy.write(self._settle(self._partial) + "\n")
                self._partial = ""

    def isatty(self) -> bool:
        try:
            return bool(self._original.isatty())
        except Exception:
            return False

    def fileno(self) -> int:
        return self._original.fileno()

    def writable(self) -> bool:
        return True

    @property
    def encoding(self) -> str:
        return getattr(self._original, "encoding", None) or "utf-8"

    @property
    def errors(self) -> str:
        return getattr(self._original, "errors", None) or "replace"

    def __getattr__(self, name: str) -> Any:
        return getattr(self._original, name)


def reflow_rows_above_cursor(screen: Any, cursor_x: int, cursor_y: int, new_width: int) -> int:
    """Extra terminal rows created above the cursor when a width shrink reflows the last frame.

    Terminals that rewrap on resize split every previously drawn row longer than
    the new width. prompt_toolkit's erase only climbs the pre-resize row count,
    so the split-off tops of the status line and top border survive as debris.
    """
    if new_width <= 0:
        return 0
    extra = 0
    for y in range(cursor_y):
        row = screen.data_buffer.get(y, {}) if hasattr(screen.data_buffer, "get") else screen.data_buffer[y]
        used = 0
        for x, cell in row.items():
            if cell.char not in (" ", ""):
                used = max(used, x + 1)
        if used > new_width:
            extra += (used - 1) // new_width
    return extra + cursor_x // new_width


class _ReflowSafeApplication(Application):
    """Application whose resize erase also clears rows the terminal created by rewrapping the old frame."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        orig_report = self.renderer.report_absolute_cursor_row

        def _report(row: int) -> None:
            orig_report(row)
            self.invalidate()

        self.renderer.report_absolute_cursor_row = _report

    def _on_resize(self) -> None:
        renderer = self.renderer
        screen = getattr(renderer, "_last_screen", None)
        old = getattr(renderer, "_last_size", None)
        cursor = getattr(renderer, "_cursor_pos", None)
        try:
            new_width = self.output.get_size().columns
        except Exception:
            new_width = 0
        if screen is not None and old is not None and cursor is not None and 0 < new_width < old.columns:
            extra = reflow_rows_above_cursor(screen, cursor.x, cursor.y, new_width)
            if extra:
                self.output.cursor_up(extra)
        super()._on_resize()


class _Placeholder(Processor):
    """Dim hint text shown while the composer is empty."""

    def __init__(self, text: Callable[[], str]) -> None:
        self._text = text

    def apply_transformation(self, ti: TransformationInput) -> Transformation:
        if ti.lineno == 0 and not ti.document.text:
            return Transformation(list(ti.fragments) + [("class:placeholder", self._text())])
        return Transformation(ti.fragments)


class HydraTUI:
    """Composer, status line, and footer for the interactive Hydra agent."""

    def __init__(
        self,
        commands: Sequence[Tuple[str, str]],
        complete: Optional[Callable[[str, str], List[str]]] = None,
        history_path: Optional[str] = None,
        pt_input: Any = None,
        pt_output: Any = None,
        bridge_stdout: bool = True,
        clipboard: Optional[Clipboard] = None,
        mouse_support: Optional[bool] = None,
    ) -> None:
        self.commands = list(commands)
        self._complete_fn = complete
        if pt_input is None and os.name == "nt":
            from hydra_cli.win_console import console_input

            pt_input = console_input()
        self._pt_input = pt_input
        self._pt_output = pt_output
        self._bridge_stdout = bridge_stdout
        self._clipboard = clipboard if clipboard is not None else _SystemClipboard()
        if mouse_support is None:
            self._mouse_support = os.environ.get("HYDRA_MOUSE", "").strip().lower() in ("1", "true", "yes", "on")
        else:
            self._mouse_support = bool(mouse_support)
        self.model = ""
        self.tier = ""
        self.dialect = ""
        self.effort = ""
        self.heat = ""
        self.strategy = ""
        self.tokens = 0
        self.budget = 0
        self.turns = 0
        self.step_mode = False

        self._buffer = Buffer(multiline=True, history=self._make_history(history_path))
        self._buffer.on_text_changed += self._on_buffer_edited
        self._main_q: "queue.Queue[object]" = queue.Queue()
        self._answer_q: "queue.Queue[object]" = queue.Queue()
        self._lock = threading.RLock()
        self._waiting: Optional[str] = None
        self._ask_label = ""
        self._queued: List[str] = []
        self._busy_since = 0.0
        self._activity: Optional[str] = None
        self._interrupting = False
        self._notice = ""
        self._notice_style = "class:notice"
        self._notice_until = 0.0
        self._last_empty_enter = 0.0
        self._last_ctrl_c = 0.0
        self._recorder: Any = None
        self._transcribing = False
        self._history_scroll = 0  # <0 means viewport is looking at older scrollback

        self._app: Optional[Application] = None
        self._thread: Optional[threading.Thread] = None
        self._ready = threading.Event()
        self._failed: Optional[BaseException] = None
        self._proxy: Optional[StdoutProxy] = None
        self._saved_streams: Optional[Tuple[Any, Any]] = None
        self._atexit_registered = False

        self._uni = ui.can_encode("╭─╮│╰╯❯⠋▁⏎◆●", sys.__stdout__)
        self._spinner = SPINNER_UNI if self._uni else SPINNER_ASCII
        self._meter = METER_UNI if self._uni else METER_ASCII
        self._g = {
            "tl": "╭" if self._uni else "+", "tr": "╮" if self._uni else "+",
            "bl": "╰" if self._uni else "+", "br": "╯" if self._uni else "+",
            "h": "─" if self._uni else "-", "v": "│" if self._uni else "|",
            "prompt": "❯" if self._uni else ">", "enter": "⏎" if self._uni else "enter",
            "enter2": "⏎⏎" if self._uni else "enter x2",
            "dot": "·" if self._uni else "-", "rec": "●" if self._uni else "*",
            "diamond": "◆" if self._uni else "*", "steer": "⤷" if self._uni else "->",
            "fill": "▰" if self._uni else "#", "empty": "▱" if self._uni else ".",
        }

    # ------------------------------------------------------------------ lifecycle

    @staticmethod
    def _make_history(path: Optional[str]) -> History:
        if path:
            try:
                os.makedirs(os.path.dirname(path), exist_ok=True)
                return FileHistory(path)
            except OSError:
                pass
        return InMemoryHistory()

    @property
    def active(self) -> bool:
        return self._app is not None and self._failed is None

    def start(self) -> None:
        """Install the stdout bridge and launch the composer thread."""
        if self._app is not None:
            return
        self._ready.clear()
        self._failed = None
        self._install_streams()
        ui.set_status_sink(self._on_activity)
        ui.set_prompt_handler(self.ask)
        self._thread = threading.Thread(target=self._run, name="hydra-tui", daemon=True)
        self._thread.start()
        self._ready.wait(5.0)
        if not self._atexit_registered:
            atexit.register(self.close)
            self._atexit_registered = True

    def _run(self) -> None:
        try:
            self._app = self._build_app()
            self._app.run(handle_sigint=False, pre_run=self._on_app_start)
        except BaseException as exc:
            self._failed = exc
        finally:
            self._ready.set()
            if self._failed is not None:
                self._main_q.put(_EOF)
                self._answer_q.put(_EOF)

    def _on_app_start(self) -> None:
        assert self._app is not None
        self._app.create_background_task(self._ticker())
        self._ready.set()

    def _stop_app(self) -> None:
        app = self._app
        if app is not None and app.is_running:
            def _exit() -> None:
                if app.is_running and not app.future.done():
                    app.exit()
            try:
                app.loop.call_soon_threadsafe(_exit)
            except Exception:
                pass
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(3.0)
        self._thread = None
        self._app = None

    def close(self) -> None:
        """Erase the composer and hand the terminal back in cooked mode."""
        if self._recorder is not None:
            try:
                self._recorder.cancel()
            except Exception:
                pass
            self._recorder = None
        self._stop_app()
        ui.set_status_sink(None)
        ui.set_prompt_handler(None)
        self._restore_streams()

    @contextmanager
    def suspended(self) -> Iterator[None]:
        """Release the terminal for full-screen interactive flows, then relaunch the composer."""
        self.close()
        try:
            yield
        finally:
            self.start()

    def _install_streams(self) -> None:
        if self._proxy is not None or not self._bridge_stdout:
            return
        self._saved_streams = (sys.stdout, sys.stderr)
        self._proxy = StdoutProxy(sleep_between_writes=0.03, raw=True)
        sys.stdout = _LineGate(self._proxy, self._saved_streams[0])  # type: ignore[assignment]
        sys.stderr = _LineGate(self._proxy, self._saved_streams[1])  # type: ignore[assignment]

    def _restore_streams(self) -> None:
        if self._proxy is None or self._saved_streams is None:
            return
        for stream in (sys.stdout, sys.stderr):
            if isinstance(stream, _LineGate):
                stream.drain()
        try:
            self._proxy.close()
        except Exception:
            pass
        sys.stdout, sys.stderr = self._saved_streams
        self._proxy = None
        self._saved_streams = None

    def _invalidate(self) -> None:
        app = self._app
        if app is not None:
            try:
                app.invalidate()
            except Exception:
                pass

    async def _ticker(self) -> None:
        while True:
            await asyncio.sleep(0.08)
            if self._animating():
                self._invalidate()

    def _animating(self) -> bool:
        return (
            self._waiting != "main"
            or self._recorder is not None
            or self._transcribing
            or (self._notice_until and time.monotonic() < self._notice_until + 0.2)
        )

    # ------------------------------------------------------------------ REPL bridge

    def update_status(self, **fields: Any) -> None:
        for key, value in fields.items():
            if hasattr(self, key):
                setattr(self, key, value)
        self._invalidate()

    def read_line(self) -> str:
        """Block until the composer delivers the next instruction; EOFError on exit."""
        if not self.active:
            return input(f"{self._g['prompt']} ")
        leftovers = ui.drain_steering()
        with self._lock:
            if leftovers:
                self._queued[0:0] = leftovers
            if self._queued:
                text = self._queued.pop(0)
                self._begin_busy()
                self._echo_user(text)
                return text
            self._waiting = "main"
            self._activity = None
        self._invalidate()
        item = self._main_q.get()
        with self._lock:
            self._waiting = None
            self._begin_busy()
        self._invalidate()
        if item is _EOF:
            raise EOFError
        if item is _INTERRUPT:
            raise KeyboardInterrupt
        return str(item)

    def ask(self, prompt: str = "") -> str:
        """Answer an agent confirmation prompt through the composer."""
        if not self.active:
            return input(prompt)
        label = " ".join(prompt.split())
        mode = ui.color_mode()
        reset = ui.RESET if mode else ""
        sys.stdout.write(f"\n  {ui.fg(ui.AMBER_RGB, mode, True)}{self._g['diamond']}{reset} {ui.fg(ui.AMBER_RGB, mode)}{label}{reset}\n")
        with self._lock:
            self._waiting = "answer"
            self._ask_label = label
        self._invalidate()
        item = self._answer_q.get()
        with self._lock:
            self._waiting = None
            self._ask_label = ""
        self._invalidate()
        if item is _INTERRUPT:
            raise KeyboardInterrupt
        if item is _EOF:
            raise EOFError
        return str(item)

    def begin_stream(self) -> None:
        """Open a live answer above the composer. Tokens follow through write_token."""
        self._pin_prompt()
        self.write_token("\n")

    def write_token(self, text: str) -> None:
        """Paint generated text immediately. Partial tokens skip the line gate."""
        if not text:
            return
        if self._history_scroll < 0:
            self._pin_prompt()
        proxy = self._proxy
        if proxy is not None:
            proxy.write(text)
            proxy.flush()
            return
        target = sys.stdout
        target.write(text)
        target.flush()

    def end_stream(self) -> None:
        self.write_token("\n\n")
        self._pin_prompt()

    def print_answer(self, answer: str) -> None:
        self._pin_prompt()
        elapsed = time.monotonic() - self._busy_since if self._busy_since else 0.0
        dot = self._g["dot"]
        header = f"{dot} {self.model} {dot} {elapsed:.1f}s" if self.model else f"{dot} {elapsed:.1f}s"
        sys.stdout.write("\n" + ui.render_answer(answer, header=header) + "\n\n")

    def _begin_busy(self) -> None:
        self._busy_since = time.monotonic()
        self._activity = None
        self._interrupting = False

    def _on_activity(self, text: Optional[str]) -> None:
        # Rotating braille chatter is gone; keep a single Working... line (glow does the rest).
        self._activity = "working..." if text else None
        self._invalidate()

    def _on_wheel(self, lines: int) -> None:
        """Mouse wheel: scroll terminal history. Negative looks up; pin again when back at bottom."""
        if lines < 0:
            if scroll_terminal_history(lines):
                self._history_scroll += lines
            return
        # Scrolling down toward the live prompt.
        if scroll_terminal_history(lines):
            self._history_scroll = min(0, self._history_scroll + lines)
        if self._history_scroll >= 0:
            self._history_scroll = 0
            self._pin_prompt()

    def _pin_prompt(self) -> None:
        """Keep the composer glued to the bottom of the visible window."""
        self._history_scroll = 0
        app = self._app
        if app is None:
            return
        pin = getattr(app.output, "scroll_buffer_to_prompt", None)
        if callable(pin):
            try:
                pin()
            except Exception:
                pass
        self._invalidate()

    def _on_buffer_edited(self, _buffer: Buffer) -> None:
        if self._history_scroll < 0:
            self._pin_prompt()

    def _echo_user(self, text: str) -> None:
        mode = ui.color_mode()
        reset = ui.RESET if mode else ""
        accent = ui.fg(ui.ACCENT_RGB, mode, True)
        body = ui.fg(ui.HOT_RGB, mode, True)
        width = max(20, shutil.get_terminal_size((100, 40)).columns - 2)
        lines = []
        for i, raw in enumerate(text.rstrip("\n").split("\n")):
            lead = f"{accent}{self._g['prompt']}{reset} {body}" if i == 0 else f"  {body}"
            lines.append(ui.wrap_text(raw, width=width, indent=lead, subsequent_indent="  " + body) + reset)
        sys.stdout.write("\n" + "\n".join(lines) + "\n")

    def _echo_steer(self, text: str) -> None:
        mode = ui.color_mode()
        reset = ui.RESET if mode else ""
        sys.stdout.write(
            f"  {ui.fg(ui.AMBER_RGB, mode, True)}{self._g['steer']} steer{reset} {ui.fg(ui.HOT_RGB, mode)}{' '.join(text.split())}{reset}\n"
        )

    def _flash(self, text: str, seconds: float = 3.0, style: str = "class:notice") -> None:
        self._notice = text
        self._notice_style = style
        self._notice_until = time.monotonic() + seconds
        self._invalidate()

    # ------------------------------------------------------------------ key handling

    def _accept(self) -> str:
        text = self._buffer.text
        self._buffer.reset(append_to_history=bool(text.strip()))
        return text

    def _on_enter(self) -> None:
        buf = self._buffer
        text = buf.text
        now = time.monotonic()
        if text.endswith("\\") and buf.cursor_position == len(text):
            buf.delete_before_cursor(1)
            buf.insert_text("\n")
            return
        waiting = self._waiting
        if waiting == "answer":
            self._accept()
            self._answer_q.put(text)
            return
        if waiting == "main":
            if text.strip():
                self._accept()
                self._echo_user(text)
                self._main_q.put(text)
            elif now - self._last_empty_enter < 0.45:
                self._last_empty_enter = 0.0
                self._main_q.put("/steer")
            else:
                self._last_empty_enter = now
            return
        if text.strip():
            self._accept()
            with self._lock:
                self._queued.append(text)
            self._flash(f"queued {self._g['dot']} press {self._g['enter']} again to steer it into the running turn", 4.0)
        elif self._queued:
            with self._lock:
                directive = self._queued.pop()
            ui.push_steering(directive)
            self._echo_steer(directive)
            self._flash("steering directive sent", 2.5, "class:ok")
        else:
            self._flash(f"hydra is working {self._g['dot']} type to queue {self._g['dot']} {self._g['enter2']} steers {self._g['dot']} escape interrupts")

    def _copy(self) -> None:
        app = self._app
        buf = self._buffer
        if app is None or not buf.selection_state:
            self._flash("select text to copy", 1.5)
            return
        app.clipboard.set_data(buf.copy_selection())
        self._flash("copied", 1.2, "class:ok")

    def _paste(self) -> None:
        app = self._app
        if app is None:
            return
        text = (app.clipboard.get_data().text or "").replace("\r\n", "\n").replace("\r", "\n")
        if not text:
            return
        if self._buffer.selection_state:
            self._buffer.cut_selection()
        self._buffer.insert_text(text)

    def _on_interrupt(self) -> None:
        now = time.monotonic()
        if self._recorder is not None:
            self._recorder.cancel()
            self._recorder = None
            self._flash("dictation cancelled")
            return
        if self._waiting == "answer":
            self._buffer.reset()
            self._answer_q.put(_INTERRUPT)
            return
        if self._buffer.text:
            self._buffer.reset()
            return
        if self._waiting == "main":
            if now - self._last_ctrl_c < 1.5:
                self._main_q.put(_EOF)
            else:
                self._last_ctrl_c = now
                self._flash("press escape again to exit", 1.5)
            return
        self._interrupting = True
        self._flash("interrupt sent · the turn stops at the next safe point", 4.0)
        if os.name == "nt":
            _thread.interrupt_main()
        else:
            os.kill(os.getpid(), signal.SIGINT)

    def _on_eof(self) -> None:
        if self._waiting == "answer":
            self._answer_q.put(_EOF)
        elif self._waiting == "main":
            self._main_q.put(_EOF)

    def _candidates(self) -> Tuple[str, List[str]]:
        text = self._buffer.text
        if not text.startswith("/") or "\n" in text:
            return "", []
        word = text.split(" ")[-1]
        if self._complete_fn is not None:
            try:
                return word, list(dict.fromkeys(self._complete_fn(word, text)))
            except Exception:
                return word, []
        return word, [c for c, _ in self.commands if c.startswith(text)]

    def _complete(self) -> None:
        word, cands = self._candidates()
        buf = self._buffer
        if not cands:
            if not buf.text.startswith("/"):
                buf.insert_text("    ")
            return
        if len(cands) == 1:
            choice = cands[0] + " "
        else:
            common = os.path.commonprefix(cands)
            choice = common if len(common) > len(word) else cands[0]
        buf.text = buf.text[: len(buf.text) - len(word)] + choice
        buf.cursor_position = len(buf.text)

    # ------------------------------------------------------------------ dictation

    def _toggle_dictation(self) -> None:
        if self._transcribing:
            self._flash("still transcribing the last take")
            return
        if self._recorder is not None:
            self._finish_dictation()
            return
        from hydra_cli import dictation

        app = self._app
        if app is None:
            return
        loop = app.loop

        def autostop() -> None:
            if loop is not None:
                loop.call_soon_threadsafe(self._finish_dictation)

        recorder = dictation.Recorder(on_autostop=autostop)
        try:
            recorder.start()
        except dictation.DictationError as exc:
            self._flash(f"dictation: {exc}", 6.0, "class:error")
            return
        self._recorder = recorder
        self._invalidate()

    def _finish_dictation(self) -> None:
        recorder, self._recorder = self._recorder, None
        if recorder is None:
            return
        from hydra_cli import dictation

        wav = recorder.stop()
        peak = max(recorder.levels or [0.0])
        if dictation.wav_duration(wav) < 0.35:
            self._flash("dictation: take too short")
            return
        if not recorder.heard_speech and peak < 0.02:
            self._flash("dictation: no speech detected")
            return
        self._transcribing = True
        app = self._app
        loop = app.loop if app is not None else None

        def work() -> None:
            try:
                text, _backend = dictation.transcribe(wav)
                err = None
            except Exception as exc:
                text, err = "", str(exc)
            if loop is not None:
                loop.call_soon_threadsafe(self._insert_transcript, text, err)

        threading.Thread(target=work, name="hydra-stt", daemon=True).start()
        self._invalidate()

    def _insert_transcript(self, text: str, err: Optional[str]) -> None:
        self._transcribing = False
        if err:
            self._flash(f"dictation: {err}", 8.0, "class:error")
            return
        text = text.strip()
        if not text:
            self._flash("dictation: nothing recognized")
            return
        before = self._buffer.document.text_before_cursor
        sep = "" if not before or before.endswith((" ", "\n")) else " "
        self._buffer.insert_text(sep + text)
        self._invalidate()

    # ------------------------------------------------------------------ rendering

    def _width(self) -> int:
        app = self._app
        try:
            return app.output.get_size().columns if app is not None else 80
        except Exception:
            return 80

    def _state(self) -> str:
        if self._recorder is not None:
            return "rec"
        if self._transcribing:
            return "stt"
        if self._waiting == "answer":
            return "answer"
        if self._waiting == "main":
            return "idle"
        return "busy"

    def _border_rgb(self) -> Tuple[int, int, int]:
        state = self._state()
        t = (math.sin(time.monotonic() * 3.2) + 1) / 2
        if state == "rec":
            return ui.lerp_rgb((150, 32, 32), (255, 84, 84), t)
        if state == "answer":
            return ui.AMBER_RGB
        if state in ("busy", "stt"):
            return ui.lerp_rgb((18, 120, 96), (64, 255, 166), t)
        return (36, 150, 108)

    def _border_style(self) -> str:
        return f"fg:{_hex(self._border_rgb())}"

    def _spin(self) -> str:
        return self._spinner[int(time.monotonic() * 12) % len(self._spinner)]

    def _placeholder_text(self) -> str:
        g = self._g
        state = self._state()
        if state == "rec":
            return "listening… escape to cancel"
        if state == "stt":
            return "transcribing…"
        if state == "answer":
            return f"type your answer {g['dot']} {g['enter']} to send {g['dot']} escape to cancel"
        if state == "busy":
            return f"type to queue {g['dot']} {g['enter2']} steers the running turn"
        return f"ask hydra anything {g['dot']} / for commands {g['dot']} ctrl+space to dictate"

    def _line_prefix(self, lineno: int, wrap_count: int) -> Fragments:
        if lineno == 0 and wrap_count == 0:
            style = "class:prompt.answer" if self._waiting == "answer" else "class:prompt"
            return [(style, self._g["prompt"] + " ")]
        return [("", "  ")]

    def _top_left(self) -> Fragments:
        g = self._g
        border = self._border_style()
        state = self._state()
        if state == "rec":
            title = [("class:title.rec", f" {g['rec']} dictating ")]
        elif state == "answer":
            title = [("class:title.answer", " answer ")]
        elif state in ("busy", "stt"):
            title = [("class:title", " hydra "), ("class:dim", f"{g['dot']} working ")]
        else:
            title = [("class:title", " hydra ")]
        if self._queued:
            title.append(("class:title.answer", f"{g['dot']} {len(self._queued)} queued "))
        return [(border, g["tl"] + g["h"])] + title

    def _top_right(self) -> Fragments:
        g = self._g
        parts = [p for p in ((self.model or "").strip(), (self.dialect or "").strip(), (self.strategy or "").strip()) if p]
        if not parts:
            return []
        sep = " " + g["dot"] + " "
        room = max(0, self._width() - 24)
        return [("class:dim", _trim(" " + sep.join(parts) + " ", room)), (self._border_style(), g["h"])]

    def _status(self) -> Fragments:
        g = self._g
        width = self._width()
        state = self._state()
        now = time.monotonic()
        left: Fragments = []
        if state == "rec" and self._recorder is not None:
            secs = int(self._recorder.elapsed)
            levels = list(self._recorder.levels[-18:])
            bars = "".join(
                self._meter[min(len(self._meter) - 1, int(min(1.0, (lvl / 0.12) ** 0.6) * (len(self._meter) - 1)))]
                for lvl in levels
            )
            left = [("class:status.rec", f" {g['rec']} REC "), ("class:status", f"{secs // 60}:{secs % 60:02d} "), ("class:meter", bars)]
        elif state == "stt":
            left = [("class:status.spin", f" {self._spin()} "), ("class:status", "transcribing")]
        elif state == "answer":
            left = [("class:status.answer", f" {g['diamond']} "), ("class:status", "hydra is waiting on your answer")]
        elif state == "busy":
            left = [("class:status.spin", f" {self._spin()} "), ("class:status", "working...")]
            if self._interrupting:
                left.append(("class:error", f" {g['dot']} interrupting"))
        notice: Fragments = []
        if self._notice and now < self._notice_until:
            notice = [(self._notice_style, self._notice + " ")]
        if not left and notice:
            return [("", " ")] + [(notice[0][0], _trim(notice[0][1], width - 2))]
        if notice:
            room = width - _frag_width(left) - 2
            if room > 12:
                text = _trim(notice[0][1], room)
                pad = width - _frag_width(left) - get_cwidth(text) - 1
                return left + [("", " " * max(1, pad)), (notice[0][0], text)]
        return [(style, _trim(text, width)) for style, text in left]

    def _footer(self) -> Fragments:
        g = self._g
        width = self._width()
        state = self._state()
        word, cands = self._candidates()
        right: Fragments = []
        if self.budget:
            pct = max(0.0, min(1.0, self.tokens / float(self.budget)))
            cells = 8
            filled = int(round(pct * cells))
            right = [
                ("class:dim", "ctx "),
                ("class:gauge.fill", g["fill"] * filled),
                ("class:gauge.empty", g["empty"] * (cells - filled)),
                ("class:dim", f" {pct * 100:.0f}%"),
            ]
        if self.turns:
            right.append(("class:dim", f" {g['dot']} {self.turns} turn{'s' if self.turns != 1 else ''}"))
        if self.step_mode:
            right.append(("class:title.answer", f" {g['dot']} step"))
        right.append(("", " "))

        if cands:
            items: Fragments = [("class:hint.key", " tab "), ("class:dim", " ")]
            descriptions = dict(self.commands)
            for i, cand in enumerate(cands[:8]):
                style = "class:suggest.match" if i == 0 else "class:suggest"
                items.append((style, cand))
                desc = descriptions.get(cand)
                if i == 0 and desc:
                    items.append(("class:dim", f" {desc}"))
                items.append(("", "  "))
            left = items
        else:
            if state == "answer":
                hints = [(g["enter"], "answer"), ("esc", "cancel")]
            elif state in ("busy", "stt"):
                hints = [(g["enter"], "queue"), (g["enter2"], "steer now"), ("esc", "interrupt"), ("ctrl+space", "dictate")]
            elif state == "rec":
                hints = [("ctrl+space", "stop + transcribe"), ("esc", "cancel")]
            else:
                hints = [(g["enter"], "send"), ("ctrl+space", "dictate"), ("/", "commands")]
            left = [("", " ")]
            budget = width - _frag_width(right) - 2
            for key, label in hints:
                piece = [("class:hint.key", key), ("class:hint", f" {label}   ")]
                if _frag_width(left) + _frag_width(piece) > budget:
                    break
                left += piece
        gap = width - _frag_width(left) - _frag_width(right) - 1
        if gap < 1:
            room = width - _frag_width(right) - 2
            trimmed: Fragments = []
            for style, text in left:
                if _frag_width(trimmed) + get_cwidth(text) > room:
                    break
                trimmed.append((style, text))
            left = trimmed
            gap = width - _frag_width(left) - _frag_width(right) - 1
            if gap < 1:
                return left
        return left + [("", " " * gap)] + right

    def _build_app(self) -> Application:
        g = self._g
        kb = KeyBindings()

        @kb.add("enter")
        def _enter(event: Any) -> None:
            self._pin_prompt()
            self._on_enter()

        @kb.add("escape", "enter")
        @kb.add("c-j")
        def _newline(event: Any) -> None:
            self._pin_prompt()
            self._buffer.insert_text("\n")

        @kb.add("c-space", eager=True)
        def _dictate(event: Any) -> None:
            self._pin_prompt()
            self._toggle_dictation()

        @kb.add("escape")
        def _escape(event: Any) -> None:
            self._pin_prompt()
            self._on_interrupt()

        @kb.add("c-c", eager=True)
        def _ctrl_c(event: Any) -> None:
            self._copy()

        @kb.add("c-v", eager=True)
        def _ctrl_v(event: Any) -> None:
            self._pin_prompt()
            self._paste()

        @kb.add("backspace", filter=has_selection)
        @kb.add("delete", filter=has_selection)
        def _delete_selection(event: Any) -> None:
            self._pin_prompt()
            event.current_buffer.cut_selection()

        @kb.add(Keys.Any, filter=has_selection)
        def _replace_selection(event: Any) -> None:
            data = event.data or ""
            if not data:
                return
            self._pin_prompt()
            event.current_buffer.cut_selection()
            event.current_buffer.insert_text(data)

        @kb.add("c-d")
        def _ctrl_d(event: Any) -> None:
            self._pin_prompt()
            if self._buffer.text:
                self._buffer.delete()
            else:
                self._on_eof()

        @kb.add("tab")
        def _tab(event: Any) -> None:
            self._pin_prompt()
            self._complete()

        border = self._border_style
        input_window = Window(
            _PromptControl(
                buffer=self._buffer,
                input_processors=[_Placeholder(self._placeholder_text)],
                on_wheel=self._on_wheel,
            ),
            wrap_lines=True,
            height=Dimension(min=1, max=10),
            dont_extend_height=True,
            get_line_prefix=self._line_prefix,
        )
        top = VSplit([
            Window(FormattedTextControl(self._top_left), dont_extend_width=True, height=1),
            Window(char=g["h"], height=1, style=border),
            Window(FormattedTextControl(self._top_right), dont_extend_width=True, height=1),
            Window(width=1, height=1, char=g["tr"], style=border),
        ])
        middle = VSplit([
            Window(width=1, char=g["v"], style=border),
            Window(width=1, char=" "),
            input_window,
            Window(width=1, char=" "),
            Window(width=1, char=g["v"], style=border),
        ])
        bottom = VSplit([
            Window(width=1, height=1, char=g["bl"], style=border),
            Window(char=g["h"], height=1, style=border),
            Window(width=1, height=1, char=g["br"], style=border),
        ])
        spacer = Window(dont_extend_height=False)
        root = _WheelRoot(
            [
                spacer,
                Window(FormattedTextControl(self._status), height=1),
                top,
                middle,
                bottom,
                Window(FormattedTextControl(self._footer), height=1),
            ],
            on_wheel=self._on_wheel,
        )

        accent = _hex(ui.ACCENT_RGB)
        amber = _hex(ui.AMBER_RGB)
        style = Style.from_dict({
            "title": f"bold {accent}",
            "title.rec": "bold #ff5a5a",
            "title.answer": f"bold {amber}",
            "dim": "#6f8f84",
            "placeholder": "#4d6a60 italic",
            "prompt": f"bold {accent}",
            "prompt.answer": f"bold {amber}",
            "status": "#a9c9bc",
            "status.spin": f"bold {accent}",
            "status.rec": "bold #ff5a5a",
            "status.answer": f"bold {amber}",
            "meter": "#ff8a5a",
            "hint": "#5f7d72",
            "hint.key": "bold #d6efe4",
            "gauge.fill": accent,
            "gauge.empty": "#2c3f38",
            "notice": amber,
            "ok": f"bold {accent}",
            "error": "bold #ff6b6b",
            "suggest": "#7f9f94",
            "suggest.match": f"bold {accent}",
        })
        depth = {ui.COLOR_TRUE: ColorDepth.TRUE_COLOR, ui.COLOR_256: ColorDepth.DEPTH_8_BIT}.get(
            ui.color_mode(), ColorDepth.DEPTH_1_BIT
        )
        app = _ReflowSafeApplication(
            layout=Layout(root, focused_element=input_window),
            key_bindings=merge_key_bindings([load_key_bindings(), kb]),
            style=style,
            full_screen=False,
            mouse_support=self._mouse_support,
            clipboard=self._clipboard,
            erase_when_done=True,
            color_depth=depth,
            refresh_interval=None,
            terminal_size_polling_interval=0.2,
            include_default_pygments_style=False,
            input=self._pt_input,
            output=self._pt_output,
        )
        app.ttimeoutlen = 0.15
        return app


_install_word_wrap()
