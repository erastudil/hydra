"""
Hydra Sovereign Desktop Application package.
Exposes FastAPI application, WebSocket streaming, sovereign Web Desk SPA,
background DesktopServer runner, and session playback engine.
"""

from hydra_cli.desktop import (
    create_desktop_app,
    DesktopServer,
    run_desktop_app,
    get_desktop_html,
)
from desktop.session_player import (
    SessionTracePlayer,
    ActionTracer,
    create_player_from_runner,
)

__all__ = [
    "create_desktop_app",
    "DesktopServer",
    "run_desktop_app",
    "get_desktop_html",
    "SessionTracePlayer",
    "ActionTracer",
    "create_player_from_runner",
]
