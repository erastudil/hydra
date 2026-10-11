#!/usr/bin/env python3
"""The one Hydra gate: hooks, turn-start memory check, 5S sweep. Logic lives in hydra_cli/memory.py."""

import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("hydra_memory", os.path.join(os.path.dirname(HERE), "hydra_cli", "memory.py"))
memory = importlib.util.module_from_spec(spec)
sys.modules["hydra_memory"] = memory
spec.loader.exec_module(memory)

if __name__ == "__main__":
    sys.exit(memory.cli(sys.argv[1:], os.path.abspath(__file__)))
