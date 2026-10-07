"""
Terminal UI: Lernaean hydra banner, gradient styling, spinner, and TUI bridge hooks.
"""

import os
import queue
import re
import shutil
import sys
import threading
import time
from typing import Any, Callable, List, Optional, Tuple

from hydra_cli._version import __version__

# ANSI Escape Codes for Phosphor Terminal Green
GREEN_BRIGHT = "\033[38;5;46m"
GREEN_MID = "\033[38;5;40m"
GREEN_DARK = "\033[38;5;34m"
GREEN_DIM = "\033[38;5;28m"
GREEN_BOLD = "\033[1;38;5;46m"
RESET = "\033[0m"

HYDRA_LOGO_ASCII = r'''
  ___ ___            .___              
 /   |   \___.__.  __| _/___________   
/    ~    <   |  | / __ |\_  __ \__  \  
\    Y    /\___  |/ /_/ | |  | \// __ \_
 \___|_  / / ____|\____ | |__|  (____  /
       \/  \/          \/            \/ 
'''

HYDRA_ART_LARGE = r"""
                            _/\_     /\               /\     _/\_
                         _-'  _ `.  /  \   _.---._   /  \  .' _  `-_
                       <'_  -@'   \ \   `-'       `-'   / /   `@-  _`>
                        \vv.      |  \  `-._     _.-'  /  |      .vv/
                       <_ )       |  |   (@)     (@)   |  |       ( _>
                        `^'      /   \   `-.     .-'   /   \      `^'
                       _/\_/\_   |    \  \vVVVVVVVv/  /    |   _/\_/\_
                   _.-'  _    `-.\     \  )       (  /     /.-'    _  `-._
                 <'_   -@'       \\     \  `^^^^^'  /     //       `@-   _`>
                  \vVv.          | \     `.       .'     / |          .vVv/
      _/\_/\_    <__  )          | \       |     |       / |          (  __>    _/\_/\_
  _.-'  _    `-.  `^^^'          |  \      |     |      /  |          `^^^'  .-'    _  `-._
<'_   -@'       \      `.       /\   \     |     |     /   /\       .'      /       `@-   _`>
 \vVv.          |       |       | \   \    |     |    /   / |       |       |          .vVv/
<__  )          |       \       \  \   \   |     |   /   /  /       /       |          (  __>
 `^^^'          |        \       \  \   \  |     |  /   /  /       /        |          `^^^'
      `.       /          \       \  \   \ |     | /   /  /       /          \       .'
       \       \           \       \ \   \ |     | /   / /       /           /       /
        \       \           \       \ \   \|     |/   / /       /           /       /
         \       \           \       \ \   |     |   / /       /           /       /
          \       \           \       \|   |     |   |/       /           /       /
           \       \   _.--~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~--._   /       /
            \     _.-~~                                               ~~-._     /
             \_.-~     .--~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~--.     ~-._/
           .-~      .-~                                               ~-.      ~-.
         .'       .'   .-~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~-.   `.       `.
        (        (    (                                               )    )        )
~^~^~^~^~`-.____ `-.__ `-._________________________________________.-' __.-' ____.-'~^~^~^~^~
"""

HYDRA_ART_MEDIUM = r"""
                     /\           /\
                    /  \ _.---._ /  \
      _/\_/\        \   `       '   /        /\_/\_
   _-'  _   `.       |  (@)   (@)  |       .'   _  `-_
 <'_  -@'     \      \   `-. .-'   /      /     `@-  _`>
  \vv.        |       \ \vVVVVVv/ /       |        .vv/
 <_  )        |        \ `^^^^^' /        |        (  _>
  `^^'        |         `.     .'         |        `^^'
      `.     /            |   |            \     .'
      \     \             |   |             /     /
       \     \            |   |            /     /
        \     \           |   |           /     /
         \     \          |   |          /     /
          \     \         |   |         /     /
           \     \        |   |        /     /
            \ _.--~~~~~~~~~~~~~~~~~~~~~--._ /
         _.-~~  .--~~~~~~~~~~~~~~~~~~~--.  ~~-._
      .-~     .~   .-~~~~~~~~~~~~~~~-.   ~.     ~-.
~^~^~^`-.___ `-.__ `-._____________.-' __.-' ___.-'^~^~^~
"""

HYDRA_WORDMARK = (
    "██╗  ██╗██╗   ██╗██████╗ ██████╗  █████╗ \n"
    "██║  ██║╚██╗ ██╔╝██╔══██╗██╔══██╗██╔══██╗\n"
    "███████║ ╚████╔╝ ██║  ██║██████╔╝███████║\n"
    "██╔══██║  ╚██╔╝  ██║  ██║██╔══██╗██╔══██║\n"
    "██║  ██║   ██║   ██████╔╝██║  ██║██║  ██║\n"
    "╚═╝  ╚═╝   ╚═╝   ╚═════╝ ╚═╝  ╚═╝╚═╝  ╚═╝"
)

HYDRA_7_HEADS_DETAILED = HYDRA_ART_LARGE
HYDRA_7_HEADS_MONSTER = HYDRA_ART_MEDIUM

TAGLINE = "Sovereign Multi-Headed AI Shell"

RGB = Tuple[int, int, int]

SCALE_STOPS: List[RGB] = [(170, 255, 214), (52, 236, 146), (14, 186, 124), (8, 120, 104)]
SCALE_SHADE: RGB = (6, 78, 72)
EYE_RGB: RGB = (255, 70, 40)
EYE_IMMORTAL_RGB: RGB = (255, 212, 60)
FANG_RGB: RGB = (246, 242, 226)
WATER_RGB: RGB = (30, 104, 170)
WATER_CREST_RGB: RGB = (70, 170, 220)
MARK_FROM_RGB: RGB = (64, 255, 166)
MARK_TO_RGB: RGB = (0, 190, 255)
MARK_SHADOW_RGB: RGB = (12, 92, 84)
HOT_RGB: RGB = (236, 255, 245)
DIM_RGB: RGB = (112, 140, 130)
ACCENT_RGB: RGB = (52, 236, 146)
AMBER_RGB: RGB = (255, 184, 48)

COLOR_NONE, COLOR_256, COLOR_TRUE = 0, 1, 2


def supports_color() -> bool:
    """Check if stdout supports ANSI color output."""
    if os.environ.get("NO_COLOR") or os.environ.get("HYDRA_NO_COLOR"):
        return False
    if os.environ.get("FORCE_COLOR"):
        return True
    return hasattr(sys.stdout, "isatty") and sys.stdout.isatty()


def color_mode() -> int:
    """Resolve the color depth the attached terminal can render."""
    if not supports_color():
        return COLOR_NONE
    forced = os.environ.get("HYDRA_COLOR_DEPTH", "").strip().lower()
    if forced in ("256", "8bit"):
        return COLOR_256
    if forced in ("24bit", "truecolor", "true"):
        return COLOR_TRUE
    if os.environ.get("COLORTERM", "").lower() in ("truecolor", "24bit"):
        return COLOR_TRUE
    if os.environ.get("WT_SESSION") or os.environ.get("TERM_PROGRAM") in ("vscode", "iTerm.app", "WezTerm", "ghostty"):
        return COLOR_TRUE
    if os.name == "nt":
        try:
            if sys.getwindowsversion().build >= 15063:
                return COLOR_TRUE
        except Exception:
            pass
    return COLOR_256


def _rgb_to_256(rgb: RGB) -> int:
    r, g, b = (max(0, min(5, round(v / 255 * 5))) for v in rgb)
    return 16 + 36 * r + 6 * g + b


def fg(rgb: RGB, mode: int, bold: bool = False) -> str:
    """Foreground escape for an RGB color at the given color depth."""
    if mode == COLOR_NONE:
        return ""
    weight = "1;" if bold else "22;"
    if mode == COLOR_TRUE:
        return f"\033[{weight}38;2;{rgb[0]};{rgb[1]};{rgb[2]}m"
    return f"\033[{weight}38;5;{_rgb_to_256(rgb)}m"


def lerp_rgb(a: RGB, b: RGB, t: float) -> RGB:
    t = max(0.0, min(1.0, t))
    return (round(a[0] + (b[0] - a[0]) * t), round(a[1] + (b[1] - a[1]) * t), round(a[2] + (b[2] - a[2]) * t))


def gradient(stops: List[RGB], t: float) -> RGB:
    t = max(0.0, min(1.0, t))
    span = t * (len(stops) - 1)
    i = min(int(span), len(stops) - 2)
    return lerp_rgb(stops[i], stops[i + 1], span - i)


def can_encode(text: str, stream: Optional[Any] = None) -> bool:
    target = stream if stream is not None else sys.stdout
    try:
        text.encode(getattr(target, "encoding", None) or "utf-8")
        return True
    except (UnicodeEncodeError, LookupError):
        return False


def colorize(text: str, color_code: str = GREEN_BRIGHT) -> str:
    """Wrap text in ANSI color if supported."""
    if not supports_color():
        return text
    return f"{color_code}{text}{RESET}"


def _art_lines(art: str) -> List[str]:
    return art.strip("\n").splitlines()


def paint_art(art: str, mode: int, hot: bool = False) -> List[str]:
    """Color the hydra: scale gradient top to bottom, ember eyes, gold eyes on the immortal head, bone fangs, marsh water."""
    lines = _art_lines(art)
    if mode == COLOR_NONE:
        return lines
    height = len(lines)
    width = max(len(line) for line in lines)
    axis = (width - 1) / 2.0
    painted: List[str] = []
    for y, line in enumerate(lines):
        base = gradient(SCALE_STOPS, y / max(1, height - 1))
        water_row = y == height - 1
        out: List[str] = []
        current = ""
        for x, ch in enumerate(line):
            if ch == " ":
                out.append(ch)
                continue
            bold = False
            if hot:
                rgb = HOT_RGB
            elif water_row and ch in "~^":
                rgb = WATER_RGB if ch == "~" else WATER_CREST_RGB
            elif ch == "@":
                rgb = EYE_IMMORTAL_RGB if abs(x - axis) < 9 else EYE_RGB
                bold = True
            elif ch in "vV^":
                rgb = FANG_RGB
            else:
                rgb = lerp_rgb(base, SCALE_SHADE, (abs(x - axis) / max(1.0, axis)) * 0.45)
            code = fg(rgb, mode, bold)
            if code != current:
                out.append(code)
                current = code
            out.append(ch)
        painted.append("".join(out) + RESET)
    return painted


def paint_wordmark(mode: int, shimmer_at: Optional[float] = None) -> List[str]:
    """Color the HYDRA wordmark with a horizontal emerald-to-cyan sweep; shimmer_at adds a moving highlight band."""
    lines = HYDRA_WORDMARK.splitlines()
    if mode == COLOR_NONE:
        return lines
    width = max(len(line) for line in lines)
    painted: List[str] = []
    for line in lines:
        out: List[str] = []
        current = ""
        for x, ch in enumerate(line):
            if ch == " ":
                out.append(ch)
                continue
            if ch == "█":
                rgb = lerp_rgb(MARK_FROM_RGB, MARK_TO_RGB, x / max(1, width - 1))
            else:
                rgb = MARK_SHADOW_RGB
            if shimmer_at is not None:
                d = abs(x - shimmer_at)
                if d < 4:
                    rgb = lerp_rgb(rgb, HOT_RGB, (1.0 - d / 4.0) * (0.9 if ch == "█" else 0.5))
            code = fg(rgb, mode, ch == "█")
            if code != current:
                out.append(code)
                current = code
            out.append(ch)
        painted.append("".join(out) + RESET)
    return painted


def select_art(columns: int, rows: int, detailed: bool = True) -> Optional[str]:
    """Pick the largest hydra that fits the terminal without wrapping."""
    large_w = max(len(line) for line in _art_lines(HYDRA_ART_LARGE))
    medium_w = max(len(line) for line in _art_lines(HYDRA_ART_MEDIUM))
    if detailed and columns >= large_w + 4 and rows >= 38:
        return HYDRA_ART_LARGE
    if columns >= medium_w + 4 and rows >= 28:
        return HYDRA_ART_MEDIUM
    return None


def _banner_parts(detailed: bool, version: str, columns: int, rows: int, mode: int, hot_art: bool = False) -> Tuple[List[str], List[str], str]:
    art = select_art(columns, rows, detailed)
    art_lines = paint_art(art, mode, hot=hot_art) if art else []
    art_w = max((len(line) for line in _art_lines(art)), default=0) if art else 0
    if can_encode(HYDRA_WORDMARK) and columns >= 46:
        mark = paint_wordmark(mode)
        mark_w = max(len(line) for line in HYDRA_WORDMARK.splitlines())
    else:
        raw = HYDRA_LOGO_ASCII.strip("\n").splitlines()
        mark_w = max(len(line) for line in raw)
        mark = [fg(ACCENT_RGB, mode, True) + line + (RESET if mode else "") for line in raw]
    block_w = max(art_w, mark_w)
    pad = " " * max(0, (block_w - mark_w) // 2)
    mark = [pad + line for line in mark]
    dot = "·" if can_encode("·") else "-"
    tagline_plain = f"{TAGLINE} {dot} v{version}"
    tag_pad = " " * max(0, (block_w - len(tagline_plain)) // 2)
    if mode:
        tagline = f"{tag_pad}{fg(DIM_RGB, mode)}{TAGLINE} {dot} {fg(ACCENT_RGB, mode, True)}v{version}{RESET}"
    else:
        tagline = tag_pad + tagline_plain
    return art_lines, mark, tagline


def get_terminal_banner(detailed: bool = True, version: Optional[str] = None,
                        columns: Optional[int] = None, rows: Optional[int] = None) -> str:
    """Construct the Lernaean hydra banner sized to the terminal."""
    version = version or __version__
    size = shutil.get_terminal_size((100, 40))
    cols = columns or size.columns
    rws = rows or size.lines
    art, mark, tagline = _banner_parts(detailed, version, cols, rws, color_mode())
    block = art + ([""] if art else []) + mark + ["", tagline]
    return "\n".join(block) + "\n"


def print_banner(detailed: bool = True, version: Optional[str] = None) -> None:
    """Print the hydra banner to stdout."""
    print(get_terminal_banner(detailed=detailed, version=version))


def _key_pressed() -> bool:
    try:
        if os.name == "nt":
            import msvcrt
            hit = False
            while msvcrt.kbhit():
                msvcrt.getwch()
                hit = True
            return hit
        import select
        ready, _, _ = select.select([sys.stdin], [], [], 0)
        return bool(ready)
    except Exception:
        return False


def play_launch_banner(version: Optional[str] = None, info: Optional[List[Tuple[str, str]]] = None,
                       stream: Optional[Any] = None) -> None:
    """Animated launch: scanline reveal of the hydra, shimmer across the wordmark, then the session card.

    Every frame rewrites only the current line or the wordmark rows directly above
    the cursor, so the effect never depends on scrollback position or window size.
    Set HYDRA_NO_ANIM=1 to print the final frame immediately.
    """
    out = stream if stream is not None else sys.stdout
    version = version or __version__
    size = shutil.get_terminal_size((100, 40))
    mode = color_mode()
    animate = (
        mode != COLOR_NONE
        and not os.environ.get("HYDRA_NO_ANIM")
        and hasattr(out, "isatty")
        and out.isatty()
    )
    art, mark, tagline = _banner_parts(True, version, size.columns, size.lines, mode)
    hot_art = _banner_parts(True, version, size.columns, size.lines, mode, hot_art=True)[0] if animate else art

    def emit(text: str) -> None:
        out.write(text)
        out.flush()

    skipped = not animate
    emit("\n")
    for idx, line in enumerate(art):
        if not skipped and _key_pressed():
            skipped = True
        if skipped:
            emit(line + "\n")
            continue
        emit(hot_art[idx])
        time.sleep(0.007)
        emit("\r" + line + "\n")
        time.sleep(0.004)
    if art:
        emit("\n")
    for line in mark:
        emit(line + "\n")
    if not skipped and mark and size.lines > len(mark) + 1 and can_encode(HYDRA_WORDMARK):
        pad = len(mark[0]) - len(mark[0].lstrip(" "))
        mark_w = max(len(line) for line in HYDRA_WORDMARK.splitlines())
        x = -4.0
        while x <= mark_w + 4:
            if _key_pressed():
                break
            frame = [" " * pad + line for line in paint_wordmark(mode, shimmer_at=x)]
            emit(f"\033[{len(mark)}A" + "".join("\r" + line + "\n" for line in frame))
            time.sleep(0.016)
            x += 2.5
        emit(f"\033[{len(mark)}A" + "".join("\r" + line + "\n" for line in mark))
    emit("\n" + tagline + "\n")
    if info:
        emit("\n" + render_session_card(info, mode) + "\n")


def glyph(name: str) -> str:
    """Unicode UI glyph with an ASCII fallback for terminals that cannot encode it."""
    table = {
        "prompt": ("❯", ">"),
        "diamond": ("◆", "*"),
        "hollow": ("◇", "o"),
        "bar": ("│", "|"),
        "enter": ("⏎", "enter"),
        "dot": ("·", "-"),
        "steer": ("⤷", "->"),
        "rec": ("●", "*"),
    }
    uni, ascii_ = table[name]
    return uni if can_encode(uni) else ascii_


def render_session_card(info: List[Tuple[str, str]], mode: Optional[int] = None) -> str:
    """Left-ruled key/value card; carries no right border, so terminal reflow on resize never breaks it."""
    mode = color_mode() if mode is None else mode
    bar = glyph("bar")
    c_bar = fg(SCALE_STOPS[2], mode)
    c_key = fg(DIM_RGB, mode)
    c_val = fg(HOT_RGB, mode)
    reset = RESET if mode else ""
    key_w = max((len(k) for k, _ in info), default=0)
    rows = []
    for key, value in info:
        if not key:
            rows.append(f"  {c_bar}{bar}{reset}  {c_key}{value}{reset}")
        else:
            rows.append(f"  {c_bar}{bar}{reset}  {c_key}{key.ljust(key_w)}{reset}  {c_val}{value}{reset}")
    return "\n".join(rows)

CR: str = chr(13)
ESC: str = chr(27)
CLEAR_LINE: str = CR + ESC + "[K"

BRAILLE_SPINNER_FRAMES: List[str] = [
    "\u280b",
    "\u2819",
    "\u2839",
    "\u2838",
    "\u283c",
    "\u2834",
    "\u2826",
    "\u2827",
    "\u2807",
    "\u280f",
]
ASCII_SPINNER_FRAMES: List[str] = ["|", "/", "-", "\\\\"]
DEFAULT_SPINNER_STATUS_MESSAGES: List[str] = [
    "Reasoning...",
    "Synthesizing plan...",
    "Evaluating invariants...",
    "Inspecting context...",
    "Planning next action...",
]


_STATUS_SINK: Optional[Callable[[Optional[str]], None]] = None
_PROMPT_HANDLER: Optional[Callable[[str], str]] = None
_STEER_QUEUE: "queue.Queue[str]" = queue.Queue()


def set_status_sink(sink: Optional[Callable[[Optional[str]], None]]) -> None:
    """Route ThinkingSpinner status text to a live TUI instead of carriage-return frames."""
    global _STATUS_SINK
    _STATUS_SINK = sink


def set_prompt_handler(handler: Optional[Callable[[str], str]]) -> None:
    """Route agent confirmation prompts to a live TUI composer."""
    global _PROMPT_HANDLER
    _PROMPT_HANDLER = handler


def prompt_input(prompt: str = "") -> str:
    """input() replacement that answers through the TUI composer when one is active."""
    if _PROMPT_HANDLER is not None:
        return _PROMPT_HANDLER(prompt)
    return input(prompt)


def push_steering(directive: str) -> None:
    """Queue an in-flight steering directive for the running agent loop."""
    if directive.strip():
        _STEER_QUEUE.put(directive.strip())


def drain_steering() -> List[str]:
    """Pop every pending steering directive in arrival order."""
    items: List[str] = []
    while True:
        try:
            items.append(_STEER_QUEUE.get_nowait())
        except queue.Empty:
            return items


def can_render_braille(stream: Optional[Any] = None) -> bool:
    """Check if stream encoding supports Unicode braille spinner frames."""
    target_stream = stream if stream is not None else sys.stderr
    encoding = getattr(target_stream, "encoding", None) or "utf-8"
    try:
        "\u280b".encode(encoding)
        return True
    except (UnicodeEncodeError, LookupError):
        return False


class ThinkingSpinner:
    """ThinkingSpinner context manager with dynamic rotating status messages and zero-artifact clean exit."""

    BRAILLE_FRAMES: List[str] = BRAILLE_SPINNER_FRAMES
    ASCII_FRAMES: List[str] = ASCII_SPINNER_FRAMES
    DEFAULT_STATUS_MESSAGES: List[str] = DEFAULT_SPINNER_STATUS_MESSAGES

    _active_spinner: Optional["ThinkingSpinner"] = None
    _active_lock: threading.Lock = threading.Lock()

    def __init__(
        self,
        message: str = "Thinking...",
        status_messages: Optional[List[str]] = None,
        stream: Optional[Any] = None,
        interval: float = 0.08,
        rotate_interval: float = 2.0,
        use_braille: Optional[bool] = None,
        color: Optional[bool] = None,
    ):
        self.message: str = message
        self.status_messages: List[str] = (
            list(status_messages)
            if status_messages is not None
            else list(self.DEFAULT_STATUS_MESSAGES)
        )
        self.stream: Any = stream if stream is not None else sys.stderr
        self.interval: float = interval
        self.rotate_interval: float = rotate_interval

        if use_braille is True:
            self.frames = list(self.BRAILLE_FRAMES)
        elif use_braille is False:
            self.frames = list(self.ASCII_FRAMES)
        else:
            self.frames = (
                list(self.BRAILLE_FRAMES)
                if can_render_braille(self.stream)
                else list(self.ASCII_FRAMES)
            )

        if color is not None:
            self.color: bool = bool(color)
        else:
            self.color = supports_color()

        self._running: bool = False
        self._paused: bool = False
        self._parent_spinner: Optional["ThinkingSpinner"] = None
        self._start_time: float = 0.0
        self._frame_idx: int = 0
        self._manual_status: Optional[str] = None
        self._lock: threading.RLock = threading.RLock()
        self._stop_event: threading.Event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._last_len: int = 0

    @property
    def is_running(self) -> bool:
        """Return running status flag."""
        return self._running

    def update_status(self, status: Optional[str] = None) -> None:
        """Update active status message override."""
        with self._lock:
            self._manual_status = status

    def set_status_messages(self, messages: List[str]) -> None:
        """Update rotating status messages list."""
        with self._lock:
            self.status_messages = list(messages)

    def format_line(self, elapsed: Optional[float] = None) -> str:
        """Construct formatted status line string."""
        if elapsed is None:
            elapsed = time.time() - self._start_time if self._start_time else 0.0

        frame = self.frames[self._frame_idx % len(self.frames)]

        with self._lock:
            if self._manual_status is not None:
                current_status = self._manual_status
            elif self.status_messages:
                s_idx = int(elapsed / self.rotate_interval) % len(self.status_messages)
                current_status = self.status_messages[s_idx]
            else:
                current_status = ""

        parts = [frame]
        if self.message:
            parts.append(self.message)
        parts.append(f"({elapsed:.1f}s)")
        if current_status:
            parts.append(f"\u00b7 {current_status}")
        return " ".join(parts)

    def sink_text(self) -> str:
        """Status text handed to a TUI sink: message plus the current rotating status."""
        elapsed = time.time() - self._start_time if self._start_time else 0.0
        with self._lock:
            if self._manual_status is not None:
                status = self._manual_status
            elif self.status_messages:
                status = self.status_messages[int(elapsed / self.rotate_interval) % len(self.status_messages)]
            else:
                status = ""
        message = self.message.rstrip(". ").rstrip("…")
        status = status.rstrip(". ").rstrip("…")
        return f"{message} · {status}" if message and status else (message or status)

    def _render_frame(self) -> None:
        """Render single spinner frame to stream."""
        sink = _STATUS_SINK
        if sink is not None:
            try:
                sink(self.sink_text())
            except Exception:
                pass
            return
        elapsed = time.time() - self._start_time if self._start_time else 0.0
        line = self.format_line(elapsed=elapsed)
        self._frame_idx += 1

        if self.color:
            rendered = f"{GREEN_BRIGHT}{line}{RESET}"
        else:
            rendered = line

        pad = max(0, self._last_len - len(line))
        self._last_len = len(line)

        try:
            if pad > 0:
                self.stream.write(CR + rendered + (" " * pad) + CR + rendered)
            else:
                self.stream.write(CR + rendered)
            self.stream.flush()
        except UnicodeEncodeError:
            self.frames = list(self.ASCII_FRAMES)
            try:
                line = self.format_line(elapsed=elapsed)
                rendered = f"{GREEN_BRIGHT}{line}{RESET}" if self.color else line
                self.stream.write(CR + rendered)
                self.stream.flush()
            except Exception:
                pass
        except Exception:
            pass

    def _spin(self) -> None:
        """Background thread execution loop."""
        if not self._paused:
            self._render_frame()

        while not self._stop_event.is_set():
            if not self._paused:
                self._render_frame()
            self._stop_event.wait(self.interval)

    def start(self) -> "ThinkingSpinner":
        """Start background spinner thread."""
        with self._lock:
            if self._running:
                return self
            self._running = True
            self._start_time = time.time()
            self._stop_event.clear()
            self._frame_idx = 0
            self._last_len = 0

        with ThinkingSpinner._active_lock:
            if ThinkingSpinner._active_spinner is not None and ThinkingSpinner._active_spinner is not self:
                self._parent_spinner = ThinkingSpinner._active_spinner
                self._parent_spinner._paused = True
            ThinkingSpinner._active_spinner = self

        self._thread = threading.Thread(target=self._spin, daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        """Stop background spinner thread and clear line with escape sequence."""
        with self._lock:
            if not self._running:
                return
            self._running = False
            self._stop_event.set()

        if self._thread and self._thread.is_alive():
            if self._thread != threading.current_thread():
                self._thread.join(timeout=1.0)

        with ThinkingSpinner._active_lock:
            if ThinkingSpinner._active_spinner is self:
                ThinkingSpinner._active_spinner = self._parent_spinner
                if self._parent_spinner is not None:
                    self._parent_spinner._paused = False

        sink = _STATUS_SINK
        if sink is not None:
            try:
                sink(self._parent_spinner.sink_text() if self._parent_spinner is not None else None)
            except Exception:
                pass
            return

        try:
            self.stream.write(CLEAR_LINE)
            self.stream.flush()
        except Exception:
            pass

    def write_line(self, text: str) -> None:
        """Clear active line, write message with newline, and redisplay frame."""
        with self._lock:
            if _STATUS_SINK is not None:
                try:
                    self.stream.write(text + "\n")
                    self.stream.flush()
                except Exception:
                    pass
                return
            try:
                self.stream.write(CLEAR_LINE + text + "\n")
                self.stream.flush()
                self._last_len = 0
                if self._running and not self._paused:
                    self._render_frame()
            except Exception:
                pass

    def __enter__(self) -> "ThinkingSpinner":
        """Enter context manager and start spinner."""
        self.start()
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Exit context manager and stop spinner."""
        self.stop()


def wrap_text(text: str, width: Optional[int] = None, indent: str = "", subsequent_indent: str = "") -> str:
    if width is None:
        width = shutil.get_terminal_size((80, 24)).columns

    def ansi_len(s: str) -> int:
        import re
        return len(re.sub(r'\x1b\[[0-9;]*m', '', s))

    lines = text.split("\n")
    wrapped_lines = []
    in_code_block = False
    
    for line in lines:
        if line.strip().startswith("```"):
            in_code_block = not in_code_block
        if in_code_block or line.strip().startswith("```") or line.strip().startswith("|"):
            wrapped_lines.append(line)
            continue
            
        if not line.strip():
            wrapped_lines.append(line)
            continue

        current_line = []
        current_len = 0
        words = line.split(" ")
        is_first_line = True
        
        for word in words:
            word_len = ansi_len(word)
            current_indent = indent if is_first_line else subsequent_indent
            indent_len = ansi_len(current_indent)
            
            if current_len + word_len + (1 if current_line else 0) > width - indent_len:
                if current_line:
                    wrapped_lines.append(current_indent + " ".join(current_line))
                    current_line = [word]
                    current_len = word_len
                    is_first_line = False
                else:
                    wrapped_lines.append(current_indent + word)
                    current_line = []
                    current_len = 0
            else:
                current_line.append(word)
                current_len += word_len + (1 if current_line else 0)
                
        if current_line:
            current_indent = indent if is_first_line else subsequent_indent
            wrapped_lines.append(current_indent + " ".join(current_line))
            
    return "\n".join(wrapped_lines)


class StreamWrap:
    """Word-wrap a token stream to the live terminal width."""

    def __init__(self, emit: Callable[[str], None], width_fn: Optional[Callable[[], int]] = None) -> None:
        self._emit = emit
        self._width_fn = width_fn
        self._hold = ""
        self._col = 0

    def _limit(self) -> int:
        if self._width_fn is not None:
            width = self._width_fn()
        else:
            width = shutil.get_terminal_size((100, 40)).columns
        return max(20, width - 2)

    def feed(self, text: str) -> None:
        if not text:
            return
        data = self._hold + text
        self._hold = ""
        limit = self._limit()
        out: List[str] = []
        col = self._col
        i = 0
        while i < len(data):
            if data[i] == "\n":
                out.append("\n")
                col = 0
                i += 1
                continue
            if data[i].isspace():
                j = i + 1
                while j < len(data) and data[j].isspace() and data[j] != "\n":
                    j += 1
                spaces = data[i:j]
                if col > 0 and col + len(spaces) > limit:
                    out.append("\n")
                    col = 0
                else:
                    out.append(spaces)
                    col += len(spaces)
                i = j
                continue
            j = i
            while j < len(data) and not data[j].isspace():
                j += 1
            if j == len(data):
                self._hold = data[i:]
                break
            word = data[i:j]
            if len(word) > limit:
                if col > 0:
                    out.append("\n")
                    col = 0
                while len(word) > limit:
                    out.append(word[:limit] + "\n")
                    word = word[limit:]
                out.append(word)
                col = len(word)
            elif col > 0 and col + len(word) > limit:
                out.append("\n" + word)
                col = len(word)
            else:
                out.append(word)
                col += len(word)
            i = j
        self._col = col
        if out:
            self._emit("".join(out))

    def finish(self) -> None:
        if not self._hold:
            return
        pending = self._hold
        self._hold = ""
        self.feed(pending + "\n")


def print_wrapped(text: str, width: Optional[int] = None, indent: str = "", stream: Optional[Any] = None) -> None:
    target_stream = stream if stream is not None else sys.stdout
    target_stream.write(wrap_text(text, width=width, indent=indent, subsequent_indent=indent) + "\n")
    target_stream.flush()

def render_prompt_box(model: str, version: str, est_tokens: int, max_budget: int, turns: int, queued_steer: Optional[str] = None, width: Optional[int] = None) -> str:
    if width is None:
        width = shutil.get_terminal_size((80, 24)).columns

    box_width = width - 2
    if box_width < 10: box_width = 10
    header = f" {model} v{version} | {est_tokens}/{max_budget} tok | {turns} turns "
    
    top_border = "╭" + header.center(box_width, "─") + "╮"
    bottom_border = "╰" + "─" * box_width + "╯"
    
    lines = [top_border]
    if queued_steer:
        steer_lines = wrap_text(f"Steer: {queued_steer}", width=box_width).split("\n")
        for line in steer_lines:
            lines.append("│ " + line.ljust(box_width - 1) + "│")
        lines.append("├" + "─" * box_width + "┤")
    
    return "\n".join(lines) + "\n" + bottom_border

def clean_pasted_text(text: str) -> str:
    import re
    text = re.sub(r'\x1b\[200~', '', text)
    text = re.sub(r'\x1b\[201~', '', text)
    text = text.replace('\r\n', '\n').replace('\r', '\n')
    return text


_MD_BOLD = re.compile(r"\*\*(.+?)\*\*")
_MD_CODE = re.compile(r"`([^`]+)`")
_MD_LIST = re.compile(r"^(\s*)([-*+]|\d+[.)])\s+(.*)$")
_MD_HEAD = re.compile(r"^(#{1,6})\s+(.*)$")


def _md_inline(text: str, mode: int, base: str) -> str:
    if mode == COLOR_NONE:
        return _MD_BOLD.sub(lambda m: m.group(1), text)
    text = _MD_CODE.sub(lambda m: f"{fg((120, 220, 255), mode)}{m.group(1)}{base}", text)
    return _MD_BOLD.sub(lambda m: f"\033[1m{m.group(1)}\033[22m{base}", text)


def render_answer(text: str, width: Optional[int] = None, header: Optional[str] = None) -> str:
    """Render an agent answer for scrollback: markdown-lite styling, hanging-indent wrap, no right-edge borders."""
    mode = color_mode()
    width = width or shutil.get_terminal_size((100, 40)).columns
    wrap_w = max(20, width)
    reset = RESET if mode else ""
    body = fg((222, 232, 228), mode)
    code_bar = fg(SCALE_SHADE, mode)
    code_fg = fg((180, 230, 205), mode)
    bar = glyph("bar")
    out: List[str] = []
    if header:
        out.append(f"{fg(ACCENT_RGB, mode, True)}{glyph('diamond')} hydra{reset}{fg(DIM_RGB, mode)} {header}{reset}")
    in_code = False
    for raw in text.strip("\n").split("\n"):
        stripped = raw.strip()
        if stripped.startswith("```"):
            in_code = not in_code
            lang = stripped[3:].strip()
            if in_code and lang:
                out.append(f"  {code_bar}{bar} {lang}{reset}")
            continue
        if in_code:
            out.append(f"  {code_bar}{bar}{reset} {code_fg}{raw}{reset}")
            continue
        if not stripped:
            out.append("")
            continue
        if stripped.startswith("|"):
            out.append(f"  {body}{raw}{reset}")
            continue
        head = _MD_HEAD.match(stripped)
        if head:
            out.append(f"  {fg(ACCENT_RGB, mode, True)}{head.group(2)}{reset}")
            continue
        item = _MD_LIST.match(raw)
        if item:
            indent = "  " + " " * len(item.group(1))
            marker = "•" if item.group(2) in "-*+" and can_encode("•") else item.group(2)
            first = f"{indent}{fg(SCALE_STOPS[1], mode)}{marker}{reset}{body} "
            hang = indent + " " * (len(marker) + 1)
            wrapped = wrap_text(_md_inline(item.group(3), mode, body), width=wrap_w, indent=first, subsequent_indent=hang + body)
            out.append(wrapped + reset)
            continue
        wrapped = wrap_text(_md_inline(stripped, mode, body), width=wrap_w, indent="  " + body, subsequent_indent="  " + body)
        out.append(wrapped + reset)
    return "\n".join(out)

