"""Entry point: python -m hydra_cli.gui, or the packaged Hydra.exe."""

from __future__ import annotations

import os
import sys
from typing import List, Optional


def _fix_windowless_stdio() -> None:
    """pythonw and a windowed exe have no stdout; give them a log file instead of crashing."""
    if sys.stdout is None or sys.stderr is None:
        log_dir = os.path.join(os.path.expanduser("~"), ".hydra")
        os.makedirs(log_dir, exist_ok=True)
        log = open(os.path.join(log_dir, "desktop.log"), "a", encoding="utf-8", buffering=1)
        sys.stdout = sys.stdout or log
        sys.stderr = sys.stderr or log


def main(argv: Optional[List[str]] = None) -> int:
    _fix_windowless_stdio()
    from PySide6.QtWidgets import QApplication

    from hydra_cli.gui import theme
    from hydra_cli.gui.window import MainWindow

    app = QApplication.instance() or QApplication(list(argv if argv is not None else sys.argv))
    app.setApplicationName("Hydra")
    theme.apply(app)
    win = MainWindow()
    win.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
