"""PyInstaller entry point for the native Hydra desktop app."""

import multiprocessing
import os
import sys


def gate_startup(cwd: str) -> str:
    """Scheduled 5S sweep and one memory check at launch; returns one report line.

    hydra_cli.memory arrives with the atmem gate PR. Without it this reports
    that the gate is absent instead of pretending it ran.
    """
    try:
        from hydra_cli.memory import GateError, launch_sweep, preturn_block
    except ImportError:
        return "gate : hydra_cli.memory is not in this build, sweep and memory check did not run"
    try:
        swept = launch_sweep(cwd)
        block = preturn_block(cwd)
    except GateError as exc:
        return f"gate : startup skipped ({exc})"
    moved = len(swept["moved"]) if swept else 0
    dropped = block.count("memory said ")
    return f"gate : startup ok, sweep moved {moved}, dropped {dropped} stale fact(s)"


if __name__ == "__main__":
    multiprocessing.freeze_support()
    line = gate_startup(os.getcwd())
    if sys.stderr:
        print(line, file=sys.stderr)
    from hydra_cli.gui.app import main

    raise SystemExit(main())
