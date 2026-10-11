"""PyInstaller entry point for the native Hydra desktop app."""

import multiprocessing

from hydra_cli.gui.app import main

if __name__ == "__main__":
    multiprocessing.freeze_support()
    raise SystemExit(main())
