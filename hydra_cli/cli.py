"""
Main entry point for Hydra CLI.
"""

import sys
from hydra_cli.router import route_command


def main():
    sys.exit(route_command(sys.argv[1:]))


if __name__ == "__main__":
    main()
