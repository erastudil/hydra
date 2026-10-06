"""
Hydra · Sovereign Multi-Headed AI Summoning CLI
"""

from hydra_cli._version import __version__
__author__ = "erastudil"

from hydra_cli.agent import run_agent_loop
from hydra_cli.agent_runners import (
    hermes_available,
    pi_available,
    run_hermes,
    run_pi,
)
from hydra_cli.mcp import McpSubprocessClient
from hydra_cli.mcp_registry import McpRegistry
from hydra_cli.providers import complete
from hydra_cli.serve import run_server
from hydra_cli.ui import print_banner

__all__ = [
    "complete",
    "run_server",
    "run_agent_loop",
    "run_hermes",
    "run_pi",
    "hermes_available",
    "pi_available",
    "McpSubprocessClient",
    "McpRegistry",
    "print_banner",
    "__version__",
]
