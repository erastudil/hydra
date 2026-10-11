"""Visual theme. One palette source: desktop/theme_manager.py. Everything here derives from it."""

from __future__ import annotations

import ctypes
import os
import sys
from typing import Dict

from PySide6.QtCore import QEasingCurve
from PySide6.QtGui import QFont, QIcon
from PySide6.QtWidgets import QApplication

from desktop.theme_manager import BUILTIN_PALETTES, ThemePalette, hex_to_rgb

THEME_ID = "dark_slate"
_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ICON_PATH = os.path.join(_REPO, "assets", "hydra_desktop.ico")

SPACE = {"xs": 4, "sm": 8, "md": 12, "lg": 16, "xl": 24}
RADIUS = {"sm": 6, "md": 8, "lg": 12}
FONTS = {
    "ui": ["Segoe UI Variable Text", "Segoe UI", "Inter", "Noto Sans", "sans-serif"],
    "mono": ["Cascadia Mono", "JetBrains Mono", "Consolas", "DejaVu Sans Mono", "monospace"],
    "ui_pt": 10,
    "mono_pt": 10,
    "small_pt": 9,
    "title_pt": 11,
}


# Motion tokens. Every animation takes its duration and curve from here.
DURATION = {"instant": 0, "fast": 120, "base": 200, "slow": 320}
EASING = {
    "standard": QEasingCurve.Type.OutCubic,      # things entering or settling
    "emphasized": QEasingCurve.Type.InOutCubic,  # things moving across the screen
    "exit": QEasingCurve.Type.InCubic,           # things leaving
}
REDUCED_FACTOR = 0.0  # reduced motion skips animation and jumps to the end state


def reduced_motion() -> bool:
    """True when the user or the system asks for less motion. HYDRA_NO_ANIM=1 forces it on, =0 forces it off."""
    flag = os.environ.get("HYDRA_NO_ANIM")
    if flag is not None:
        return flag.strip().lower() not in ("", "0", "false", "no")
    if sys.platform == "win32":
        SPI_GETCLIENTAREAANIMATION = 0x1042
        enabled = ctypes.c_int(1)
        if ctypes.windll.user32.SystemParametersInfoW(SPI_GETCLIENTAREAANIMATION, 0, ctypes.byref(enabled), 0):
            return not enabled.value
    return False


def duration(name: str) -> int:
    """Milliseconds for a duration token; 0 under reduced motion."""
    return int(DURATION[name] * REDUCED_FACTOR) if reduced_motion() else DURATION[name]


def easing(name: str) -> QEasingCurve:
    return QEasingCurve(EASING[name])


def _mix(a: str, b: str, t: float) -> str:
    """Blend hex a toward hex b by t in [0, 1]."""
    ra, rb = hex_to_rgb(a), hex_to_rgb(b)
    return "#{:02x}{:02x}{:02x}".format(*(round(x + (y - x) * t) for x, y in zip(ra, rb)))


def derive_palette(p: ThemePalette) -> Dict[str, str]:
    """Expand the base palette tokens into the roles the widgets use."""
    bg = _mix(p.background, p.foreground, 0.045)
    panel = _mix(p.surface, p.foreground, 0.07)
    raised = _mix(p.surface, p.foreground, 0.12)
    return {
        "bg": bg,
        "panel": panel,
        "raised": raised,
        "hover": _mix(raised, p.foreground, 0.08),
        "border": _mix(p.border, p.foreground, 0.12),
        "text": _mix(p.foreground, bg, 0.06),
        "muted": _mix(p.foreground, bg, 0.46),
        "accent": _mix(p.primary, bg, 0.12),
        "accent_hover": p.primary,
        "ring": p.primary,
        "on_accent": p.background,
        "danger": _mix(p.danger, p.foreground, 0.12),
        "warning": p.warning,
        "add": _mix(bg, p.success, 0.22),
        "del": _mix(bg, p.danger, 0.28),
        "select": _mix(bg, p.primary, 0.22),
    }


PALETTE: Dict[str, str] = derive_palette(BUILTIN_PALETTES[THEME_ID])


def build_qss(p: Dict[str, str] = PALETTE) -> str:
    r, s = RADIUS, SPACE
    mono = ", ".join(f'"{f}"' for f in FONTS["mono"][:-1])
    return f"""
* {{ outline: 0; }}
QWidget {{ background: {p['bg']}; color: {p['text']}; font-size: {FONTS['ui_pt']}pt; }}
QMainWindow, QSplitter {{ background: {p['bg']}; }}
QSplitter::handle {{ background: {p['border']}; }}
QSplitter::handle:horizontal {{ width: 1px; }}
QToolTip {{ background: {p['raised']}; color: {p['text']}; border: 1px solid {p['border']}; padding: {s['xs']}px {s['sm']}px; }}

QLabel {{ background: transparent; }}
QLabel#section {{ color: {p['muted']}; font-size: {FONTS['small_pt']}pt; font-weight: 600; letter-spacing: 1px; padding: {s['sm']}px {s['xs']}px; }}
QLabel#muted {{ color: {p['muted']}; }}
QLabel#title {{ font-size: {FONTS['title_pt']}pt; font-weight: 600; }}

QWidget#sidebar {{ background: {p['panel']}; }}
QWidget#sidebar QLabel, QWidget#sidebar QWidget#sideRow {{ background: transparent; }}
QWidget#sidebar QListWidget {{ background: transparent; border: 0; padding: 0; }}
QListWidget::item {{ padding: {s['sm']}px {s['md']}px; margin: 1px 0; border-radius: {r['sm']}px; color: {p['text']}; }}
QListWidget::item:hover {{ background: {p['raised']}; }}
QListWidget::item:selected {{ background: {p['select']}; color: {p['text']}; }}

QPlainTextEdit, QTextEdit, QLineEdit {{
    background: {p['bg']}; border: 0; padding: {s['sm']}px {s['md']}px;
    selection-background-color: {p['select']}; selection-color: {p['text']}; }}
QPlainTextEdit#input {{
    background: {p['panel']}; border: 1px solid {p['border']}; border-radius: {r['lg']}px; padding: {s['md']}px {s['lg']}px; }}
QPlainTextEdit#input:focus {{ border: 1px solid {p['ring']}; }}
QPlainTextEdit#diff, QPlainTextEdit#approvalBody {{ background: {p['bg']}; border: 0; font-family: {mono}; }}
QPlainTextEdit#approvalBody {{ background: {p['panel']}; border: 1px solid {p['border']}; border-radius: {r['md']}px; }}
QWidget#approval QLabel#title {{ padding: {s['sm']}px 0; }}

QPushButton {{ background: {p['raised']}; color: {p['text']}; border: 1px solid {p['border']};
    border-radius: {r['md']}px; padding: {s['sm']}px {s['lg']}px; min-height: 16px; }}
QPushButton:hover {{ background: {p['hover']}; }}
QPushButton:pressed {{ background: {p['panel']}; }}
QPushButton:focus {{ border: 1px solid {p['ring']}; }}
QPushButton:checked {{ background: {p['select']}; border-color: {p['accent']}; color: {p['text']}; }}
QPushButton:disabled {{ background: {p['panel']}; color: {p['muted']}; border-color: {p['border']}; }}
QPushButton#primary {{ background: {p['accent']}; color: {p['on_accent']}; border: 1px solid {p['accent']}; font-weight: 600; }}
QPushButton#primary:hover {{ background: {p['accent_hover']}; border-color: {p['accent_hover']}; }}
QPushButton#primary:disabled {{ background: {p['raised']}; color: {p['muted']}; border-color: {p['border']}; }}
QPushButton#danger {{ background: transparent; color: {p['danger']}; border: 1px solid {p['danger']}; }}
QPushButton#danger:hover {{ background: {p['del']}; }}
QPushButton#danger:disabled {{ color: {p['muted']}; border-color: {p['border']}; background: transparent; }}
QPushButton#icon {{ padding: 0; min-width: 28px; max-width: 28px; min-height: 28px; max-height: 28px; font-size: {FONTS['title_pt']}pt; }}

QComboBox {{ background: {p['panel']}; border: 1px solid {p['border']}; border-radius: {r['md']}px; padding: {s['xs']}px {s['md']}px; min-height: 22px; }}
QComboBox:hover {{ background: {p['raised']}; }}
QComboBox:focus, QComboBox:on {{ border-color: {p['ring']}; }}
QComboBox::drop-down {{ border: 0; width: 24px; }}
QComboBox QAbstractItemView {{ background: {p['panel']}; border: 1px solid {p['border']}; selection-background-color: {p['select']}; padding: {s['xs']}px; outline: 0; }}
QComboBox QLineEdit {{ background: transparent; padding: 0; }}

QTabWidget::pane {{ border: 0; border-top: 1px solid {p['border']}; }}
QTabBar {{ background: {p['bg']}; }}
QTabBar::tab {{ background: transparent; color: {p['muted']}; padding: {s['md']}px {s['lg']}px; border-bottom: 2px solid transparent; }}
QTabBar::tab:hover {{ color: {p['text']}; }}
QTabBar::tab:selected {{ color: {p['text']}; border-bottom: 2px solid {p['accent']}; }}

QScrollBar:vertical {{ background: transparent; width: 10px; margin: 0; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 0; }}
QScrollBar::handle {{ background: {p['border']}; border-radius: 3px; min-height: 28px; min-width: 28px; margin: 2px; }}
QScrollBar::handle:hover {{ background: {p['muted']}; }}
QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; border: 0; width: 0; height: 0; }}
"""


def apply(app: QApplication) -> None:
    app.setStyle("Fusion")
    font = QFont()
    font.setFamilies(FONTS["ui"])
    font.setPointSize(FONTS["ui_pt"])
    app.setFont(font)
    app.setStyleSheet(build_qss())
    if os.path.isfile(ICON_PATH):
        app.setWindowIcon(QIcon(ICON_PATH))


def mono() -> QFont:
    f = QFont()
    f.setFamilies(FONTS["mono"])
    f.setPointSize(FONTS["mono_pt"])
    f.setStyleHint(QFont.Monospace)
    return f
