"""Build dist/Hydra/Hydra.exe with PyInstaller. Native Qt only: QtWebEngine and other unused Qt modules are excluded.

Run: python scripts/build_exe.py
"""

from __future__ import annotations

import os
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENTRY = os.path.join(REPO, "scripts", "hydra_gui_entry.py")
ICON = os.path.join(REPO, "assets", "hydra_desktop.ico")

EXCLUDES = [
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineQuick",
    "PySide6.QtWebChannel", "PySide6.QtWebSockets", "PySide6.QtWebView",
    "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtQuickWidgets", "PySide6.QtQuick3D",
    "PySide6.Qt3DCore", "PySide6.QtMultimedia", "PySide6.QtPdf", "PySide6.QtPdfWidgets",
    "PySide6.QtCharts", "PySide6.QtDataVisualization", "PySide6.QtSql", "PySide6.QtTest",
    "PySide6.QtBluetooth", "PySide6.QtNfc", "PySide6.QtSensors", "PySide6.QtDesigner",
    "tkinter",
]


def build() -> str:
    sep = ";" if os.name == "nt" else ":"
    cmd = [
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--windowed",
        "--name", "Hydra", "--icon", ICON,
        "--distpath", os.path.join(REPO, "dist"), "--workpath", os.path.join(REPO, "build"),
        "--specpath", os.path.join(REPO, "build"),
        "--paths", REPO,
        "--add-data", f"{os.path.join(REPO, 'hydra_cli', 'catalog.json')}{sep}hydra_cli",
        "--add-data", f"{os.path.join(REPO, 'hydra_cli', 'mcp_servers.default.json')}{sep}hydra_cli",
        "--add-data", f"{os.path.join(REPO, 'hydra_cli', 'skills')}{sep}hydra_cli/skills",
        "--add-data", f"{os.path.join(REPO, 'hydra_cli', 'data')}{sep}hydra_cli/data",
        "--add-data", f"{os.path.join(REPO, 'assets')}{sep}assets",
    ]
    for mod in EXCLUDES:
        cmd += ["--exclude-module", mod]
    cmd.append(ENTRY)
    subprocess.run(cmd, check=True, cwd=REPO)
    exe = os.path.join(REPO, "dist", "Hydra", "Hydra.exe" if os.name == "nt" else "Hydra")
    if not os.path.isfile(exe):
        raise SystemExit(f"build finished without {exe}")
    print(f"built : {exe}")
    return exe


if __name__ == "__main__":
    build()
