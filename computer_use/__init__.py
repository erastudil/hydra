"""
Computer Use Engine for Hydra.
Exposes OS automation, coordinate boundary guards, screen capture,
Playwright browser automation, and browser storage persistence.
"""

from hydra_cli.computer_use import (
    CoordinateBounds,
    OSController,
    ScreenCaptureEngine,
    PlaywrightAutomationBridge,
    ComputerUseEngine,
    get_computer_use_engine,
    reset_computer_use_engine,
)
from computer_use.browser_storage import (
    BrowserStorageManager,
    CookieRecord,
)

__all__ = [
    "CoordinateBounds",
    "OSController",
    "ScreenCaptureEngine",
    "PlaywrightAutomationBridge",
    "ComputerUseEngine",
    "get_computer_use_engine",
    "reset_computer_use_engine",
    "BrowserStorageManager",
    "CookieRecord",
]
