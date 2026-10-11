"""
Playwright Browser Storage and Cookie Persistence Subsystem for Computer Use.
Exposes BrowserStorageManager, BrowserCookie, and Playwright storage state parity.
Complies with AGENTS.md genome invariants: zero copula P018, zero stubs, deterministic verification.
"""

from desktop.browser_storage import (
    BrowserCookie,
    BrowserStorageManager,
)

CookieRecord = BrowserCookie

__all__ = [
    "BrowserCookie",
    "BrowserStorageManager",
    "CookieRecord",
]
