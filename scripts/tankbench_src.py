"""Locate tankbench for the idle-compute and reset-day runners."""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Patrick's checkout. TANKBENCH_SRC overrides this on any other machine.
DEFAULT_SRC = "C:/Users/jpm05/Documents/tankbench/src"


def load_attr(module: str, name: str):
    """Import name from module, searching TANKBENCH_SRC before the default checkout."""
    raw = os.environ.get("TANKBENCH_SRC", "").strip() or DEFAULT_SRC
    src = Path(raw)
    if src.is_dir():
        entry = str(src)
        if entry not in sys.path:
            sys.path.insert(0, entry)
    try:
        imported = __import__(module, fromlist=[name])
        return getattr(imported, name)
    except (ImportError, AttributeError) as exc:
        sys.stderr.write(
            f"{module}.{name} is not importable. "
            "Set TANKBENCH_SRC to the tankbench src directory.\n"
        )
        raise SystemExit(1) from exc
