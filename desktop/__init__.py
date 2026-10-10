"""
Hydra Sovereign Desktop Application package.
Exposes FastAPI application, WebSocket streaming, sovereign Web Desk SPA,
background DesktopServer runner, session playback engine, multi-workspace manager,
browser storage persistence, model performance evaluator, command registry,
audio transcriber, and notification hub.
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
from desktop.audio_transcriber import (
    AudioTranscriber,
    AudioBuffer,
    PTTState,
    TranscriptionResult,
    pcm_to_wav,
    calculate_pcm_rms,
    get_audio_transcriber,
    reset_audio_transcriber,
)
from desktop.notification_hub import (
    NotificationHub,
    Notification,
    NotificationItem,
    NotificationPriority,
    NotificationType,
    get_notification_hub,
    reset_notification_hub,
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
    "AudioTranscriber",
    "AudioBuffer",
    "PTTState",
    "TranscriptionResult",
    "pcm_to_wav",
    "calculate_pcm_rms",
    "get_audio_transcriber",
    "reset_audio_transcriber",
    "NotificationHub",
    "Notification",
    "NotificationItem",
    "NotificationPriority",
    "NotificationType",
    "get_notification_hub",
    "reset_notification_hub",
]
