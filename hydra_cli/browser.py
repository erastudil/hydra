"""
Playwright browser automation engine for Hydra CLI.
Supports browsing, clicking, typing, screenshots, and content extraction.
Safe lazy import ensures clean execution whether Playwright is installed or uninstalled.
"""

from __future__ import annotations

import atexit
import os
import time
import uuid
from typing import Any, Dict, Optional

try:
    from playwright.sync_api import sync_playwright, Playwright, Browser, BrowserContext, Page, Error as PlaywrightError
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False
    PlaywrightError = Exception


class PlaywrightBrowserManager:
    """Managed Playwright browser lifecycle instance with automatic cleanup."""

    def __init__(self, headless: bool = True):
        self.headless = headless
        self._playwright: Optional[Playwright] = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        self._page: Optional[Page] = None

    def _ensure_page(self) -> Page:
        if not PLAYWRIGHT_AVAILABLE:
            raise RuntimeError(
                "Playwright uninstalled; run 'pip install playwright && playwright install' to enable browser automation."
            )
        if self._page is None or self._page.is_closed():
            if self._playwright is None:
                self._playwright = sync_playwright().start()
            if self._browser is None or not self._browser.is_connected():
                self._browser = self._playwright.chromium.launch(headless=self.headless)
            if self._context is None:
                self._context = self._browser.new_context(
                    viewport={"width": 1280, "height": 800},
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) HydraBrowser/1.2",
                )
            self._page = self._context.new_page()
        return self._page

    def browse(self, url: str, wait_until: str = "load", timeout_ms: int = 30000) -> Dict[str, Any]:
        """Navigate to a URL and return page metadata and content preview."""
        page = self._ensure_page()
        target_url = url if "://" in url else f"https://{url}"
        response = page.goto(target_url, wait_until=wait_until, timeout=timeout_ms)
        status = response.status if response else 200
        title = page.title()
        current_url = page.url
        body_text = page.inner_text("body") if page.locator("body").count() > 0 else ""
        snippet = " ".join(body_text.split()[:200])
        return {
            "status": status,
            "url": current_url,
            "title": title,
            "snippet": snippet,
        }

    def click(self, selector: str, timeout_ms: int = 10000) -> Dict[str, Any]:
        """Click an element matching the given CSS or text selector."""
        page = self._ensure_page()
        page.locator(selector).first.click(timeout=timeout_ms)
        return {
            "clicked": selector,
            "url": page.url,
            "title": page.title(),
        }

    def type_text(
        self,
        selector: str,
        text: str,
        timeout_ms: int = 10000,
        clear: bool = False,
        press_enter: bool = False,
    ) -> Dict[str, Any]:
        """Type text into an input element."""
        page = self._ensure_page()
        locator = page.locator(selector).first
        if clear:
            locator.fill("", timeout=timeout_ms)
        locator.fill(text, timeout=timeout_ms)
        if press_enter:
            locator.press("Enter")
        return {
            "typed": text,
            "selector": selector,
            "url": page.url,
        }

    def screenshot(
        self,
        path: Optional[str] = None,
        full_page: bool = False,
    ) -> Dict[str, Any]:
        """Capture screenshot to path or temporary file."""
        page = self._ensure_page()
        if not path:
            screenshots_dir = os.path.join(os.path.expanduser("~"), ".hydra", "screenshots")
            os.makedirs(screenshots_dir, exist_ok=True)
            shot_id = uuid.uuid4().hex[:6]
            path = os.path.join(screenshots_dir, f"shot_{int(time.time())}_{shot_id}.png")
        page.screenshot(path=path, full_page=full_page)
        size = os.path.getsize(path) if os.path.isfile(path) else 0
        return {
            "path": path,
            "size_bytes": size,
            "url": page.url,
        }

    def extract_content(
        self,
        selector: Optional[str] = None,
        max_chars: int = 8000,
    ) -> Dict[str, Any]:
        """Extract text content from page or specific selector."""
        page = self._ensure_page()
        if selector:
            loc = page.locator(selector).first
            text = loc.inner_text() if loc.count() > 0 else ""
        else:
            text = page.inner_text("body") if page.locator("body").count() > 0 else ""

        truncated = len(text) > max_chars
        content = text[:max_chars]
        if truncated:
            content += f"\n[OUTPUT TRUNCATED: {len(text) - max_chars} characters withheld]"

        return {
            "url": page.url,
            "title": page.title(),
            "content": content,
            "truncated": truncated,
        }

    def close(self) -> None:
        """Close browser context and stop Playwright runner cleanly."""
        try:
            if self._page and not self._page.is_closed():
                self._page.close()
        except Exception:
            pass
        self._page = None

        try:
            if self._context:
                self._context.close()
        except Exception:
            pass
        self._context = None

        try:
            if self._browser and self._browser.is_connected():
                self._browser.close()
        except Exception:
            pass
        self._browser = None

        try:
            if self._playwright:
                self._playwright.stop()
        except Exception:
            pass
        self._playwright = None


_GLOBAL_BROWSER: Optional[PlaywrightBrowserManager] = None


def get_browser_session() -> PlaywrightBrowserManager:
    global _GLOBAL_BROWSER
    if _GLOBAL_BROWSER is None:
        _GLOBAL_BROWSER = PlaywrightBrowserManager()
    return _GLOBAL_BROWSER


def close_browser_session() -> None:
    global _GLOBAL_BROWSER
    if _GLOBAL_BROWSER is not None:
        _GLOBAL_BROWSER.close()
        _GLOBAL_BROWSER = None


atexit.register(close_browser_session)


def dispatch_browser_action(
    action: str,
    url: Optional[str] = None,
    selector: Optional[str] = None,
    text: Optional[str] = None,
    path: Optional[str] = None,
    full_page: bool = False,
    timeout_ms: int = 15000,
) -> Dict[str, Any]:
    """Execute browser action returning structured observation."""
    if not PLAYWRIGHT_AVAILABLE:
        return {
            "isError": True,
            "error": "Playwright uninstalled; run 'pip install playwright && playwright install' to enable browser automation.",
        }

    act = (action or "").strip().lower()
    session = get_browser_session()

    try:
        if act in ("browse", "navigate", "goto", "open"):
            if not url:
                return {"isError": True, "error": "URL required for browse action"}
            res = session.browse(url, timeout_ms=timeout_ms)
            return {"isError": False, "result": res}
        elif act in ("click", "tap"):
            if not selector:
                return {"isError": True, "error": "Selector required for click action"}
            res = session.click(selector, timeout_ms=timeout_ms)
            return {"isError": False, "result": res}
        elif act in ("type", "input", "fill"):
            if not selector:
                return {"isError": True, "error": "Selector required for type action"}
            res = session.type_text(selector, text or "", timeout_ms=timeout_ms)
            return {"isError": False, "result": res}
        elif act in ("screenshot", "capture"):
            res = session.screenshot(path=path, full_page=full_page)
            return {"isError": False, "result": res}
        elif act in ("extract_content", "extract", "content", "read"):
            res = session.extract_content(selector=selector)
            return {"isError": False, "result": res}
        elif act in ("close", "exit", "quit"):
            close_browser_session()
            return {"isError": False, "result": "Browser session closed."}
        else:
            return {
                "isError": True,
                "error": f"Unknown browser action '{action}'; supported: browse, click, type, screenshot, extract_content, close",
            }
    except Exception as exc:
        return {"isError": True, "error": f"Browser action '{action}' failed: {exc}"}
