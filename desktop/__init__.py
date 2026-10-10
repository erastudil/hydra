"""
Hydra Sovereign Desktop Application package.
Exposes FastAPI application, WebSocket streaming, sovereign Web Desk SPA,
background DesktopServer runner, session playback engine, multi-workspace manager,
browser storage persistence, model performance evaluator, and command registry.
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
from desktop.model_evaluator import (
    ModelPerformanceEvaluator,
    ModelBenchmarkSample,
    ModelMetricSample,
)
from desktop.command_registry import (
    CommandRegistry,
    CommandItem,
    normalize_shortcut,
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
    "ModelPerformanceEvaluator",
    "ModelBenchmarkSample",
    "ModelMetricSample",
    "CommandRegistry",
    "CommandItem",
    "normalize_shortcut",
]
