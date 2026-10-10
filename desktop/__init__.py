"""
Hydra Sovereign Desktop Application package.
Exposes FastAPI application, WebSocket streaming, sovereign Web Desk SPA,
background DesktopServer runner, session playback engine, multi-workspace manager,
and browser storage persistence.
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
from desktop.multi_workspace import (
    MultiWorkspaceManager,
    Workspace,
)
from desktop.browser_storage import (
    BrowserStorageManager,
    BrowserCookie,
)

__all__ = [
    "create_desktop_app",
    "DesktopServer",
    "run_desktop_app",
    "get_desktop_html",
    "SessionTracePlayer",
    "ActionTracer",
    "create_player_from_runner",
    "MultiWorkspaceManager",
    "Workspace",
    "BrowserStorageManager",
    "BrowserCookie",
]
