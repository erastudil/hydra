"""
Computer Use Engine for Hydra.
Exposes OS automation, coordinate boundary guards, screen capture,
and Playwright browser automation.
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

__all__ = [
    "CoordinateBounds",
    "OSController",
    "ScreenCaptureEngine",
    "PlaywrightAutomationBridge",
    "ComputerUseEngine",
    "get_computer_use_engine",
    "reset_computer_use_engine",
]
