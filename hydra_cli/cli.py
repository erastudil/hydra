"""
Main entry point for Hydra CLI.
"""

import os
import sys
from hydra_cli.router import route_command


def main():
    from hydra_cli.memory import GateError, launch_sweep
    if sys.argv[1:2] == ["stdio"]:
        sys.exit(route_command(sys.argv[1:]))  # the app owns the project dir, no sweep on spawn
    try:
        swept = launch_sweep(os.getcwd())
        if swept and swept["moved"]:
            print(f"gate : sweep moved {len(swept['moved'])} file(s) to purgatory, undo with gate.py restore <id>", file=sys.stderr)
    except GateError as exc:
        print(f"gate : sweep skipped ({exc})", file=sys.stderr)
    sys.exit(route_command(sys.argv[1:]))


if __name__ == "__main__":
    main()
