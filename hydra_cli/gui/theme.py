"""Visual theme hook. Yoshi owns this file: edit PALETTE, FONTS and build_qss only."""

from __future__ import annotations

import os
from typing import Dict

from PySide6.QtGui import QFont, QIcon
from PySide6.QtWidgets import QApplication

PALETTE: Dict[str, str] = {
    "bg": "#0b0d10",
    "panel": "#11141a",
    "raised": "#171b22",
    "border": "#232833",
    "text": "#e6e9ef",
    "muted": "#8b93a1",
    "accent": "#3ddc97",
    "danger": "#ff5c6c",
    "add": "#1f6f4a",
    "del": "#7a2a33",
}

FONTS = {"ui": "Segoe UI", "mono": "Cascadia Mono", "ui_pt": 10, "mono_pt": 10}

ICON_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "assets", "hydra_desktop.ico")


def build_qss(p: Dict[str, str] = PALETTE) -> str:
    return f"""
QWidget {{ background: {p['bg']}; color: {p['text']}; }}
QMainWindow, QSplitter::handle {{ background: {p['border']}; }}
QListWidget, QTreeWidget, QPlainTextEdit, QTextEdit, QLineEdit, QComboBox {{
    background: {p['panel']}; border: 1px solid {p['border']}; border-radius: 6px; padding: 4px; }}
QListWidget::item {{ padding: 6px 8px; border-radius: 4px; }}
QListWidget::item:selected {{ background: {p['raised']}; color: {p['accent']}; }}
QPushButton {{ background: {p['raised']}; border: 1px solid {p['border']}; border-radius: 6px; padding: 6px 14px; }}
QPushButton:hover {{ border-color: {p['accent']}; }}
QPushButton:disabled {{ color: {p['muted']}; }}
QPushButton#primary {{ background: {p['accent']}; color: {p['bg']}; font-weight: 600; }}
QPushButton#danger {{ border-color: {p['danger']}; color: {p['danger']}; }}
QLabel#muted {{ color: {p['muted']}; }}
QTabWidget::pane {{ border: 1px solid {p['border']}; }}
QTabBar::tab {{ background: {p['panel']}; padding: 6px 14px; }}
QTabBar::tab:selected {{ color: {p['accent']}; }}
"""


def apply(app: QApplication) -> None:
    app.setStyle("Fusion")
    app.setFont(QFont(FONTS["ui"], FONTS["ui_pt"]))
    app.setStyleSheet(build_qss())
    if os.path.isfile(ICON_PATH):
        app.setWindowIcon(QIcon(ICON_PATH))


def mono() -> QFont:
    f = QFont(FONTS["mono"], FONTS["mono_pt"])
    f.setStyleHint(QFont.Monospace)
    return f
