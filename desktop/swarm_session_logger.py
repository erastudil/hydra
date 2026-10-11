"""
Desktop integration and re-export for SwarmSessionLogger.
Adheres to AGENTS.md genome invariants: zero copula P018, deterministic verification gates.
"""

from __future__ import annotations

from hydra_cli.session_logger import (
    SwarmHeadDispatch,
    SwarmToolExecution,
    SwarmHeadSummary,
    SwarmSessionSummary,
    SwarmSessionLogger,
    get_swarm_session_logger,
    reset_swarm_session_logger,
)

__all__ = [
    "SwarmHeadDispatch",
    "SwarmToolExecution",
    "SwarmHeadSummary",
    "SwarmSessionSummary",
    "SwarmSessionLogger",
    "get_swarm_session_logger",
    "reset_swarm_session_logger",
]
