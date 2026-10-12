#!/usr/bin/env python3
"""The one Hydra gate: hooks, turn-start memory check, 5S sweep. Logic lives in hydra_cli/memory.py and hydra_cli/invariants.py."""

import importlib
import os
import sys
import types

PKG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "hydra_cli")
if "hydra_cli" not in sys.modules:
    # A bare package shell: hooks import memory and invariants without running the heavy hydra_cli/__init__.py.
    shell = types.ModuleType("hydra_cli")
    shell.__path__ = [PKG]
    sys.modules["hydra_cli"] = shell
memory = importlib.import_module("hydra_cli.memory")

if __name__ == "__main__":
    sys.exit(memory.cli(sys.argv[1:], os.path.abspath(__file__)))
