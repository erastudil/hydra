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
import html.parser
import threading
from typing import Any, Dict, List, Optional, Sequence, Tuple

try:
    from playwright.sync_api import sync_playwright, Playwright, Browser, BrowserContext, Page, Error as PlaywrightError
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False
    PlaywrightError = Exception


DOM_IDLE_SCRIPT = """
(() => {
    return new Promise((resolve) => {
        const idleMs = {idle_ms};
        const maxMs = {max_ms};
        const startTime = Date.now();
        let timer = null;
        let mutationCount = 0;

        function finish(timedOut) {
            if (observer) observer.disconnect();
            if (timer) clearTimeout(timer);
            if (backupTimer) clearTimeout(backupTimer);
            resolve({
                is_idle: !timedOut,
                settle_time_ms: Date.now() - startTime,
                mutations_observed: mutationCount,
                ready_state: document.readyState
            });
        }

        const backupTimer = setTimeout(() => finish(true), maxMs);

        const observer = new MutationObserver((mutations) => {
            mutationCount += mutations.length;
            if (timer) clearTimeout(timer);
            timer = setTimeout(() => finish(false), idleMs);
        });

        const target = document.documentElement || document.body;
        if (target) {
            observer.observe(target, {
                childList: true,
                subtree: true,
                attributes: true,
                characterData: true
            });
        }

        timer = setTimeout(() => finish(false), idleMs);
    });
})()
"""


class _DOMDepthParser(html.parser.HTMLParser):
    """Internal HTML parser measuring maximum element tree depth."""

    def __init__(self) -> None:
        super().__init__()
        self.max_depth = 0
        self.current_depth = 0
        self.tag_counts: Dict[str, int] = {}

    def handle_starttag(self, tag: str, attrs: Any) -> None:
        """Record tag entrance and increment tree depth."""
        self.current_depth += 1
        if self.current_depth > self.max_depth:
            self.max_depth = self.current_depth
        self.tag_counts[tag] = self.tag_counts.get(tag, 0) + 1

    def handle_endtag(self, tag: str) -> None:
        """Record tag exit and decrement tree depth."""
        self.current_depth = max(0, self.current_depth - 1)


class DomIdleLatch:
    """
    Playwright DOM idle latching engine evaluating stability before action dispatch.
    Observes DOM mutations, document readyState, and settling thresholds.
    """

    def __init__(
        self,
        default_idle_ms: float = 200.0,
        default_max_timeout_ms: float = 10000.0,
    ) -> None:
        self.default_idle_ms = float(default_idle_ms)
        self.default_max_timeout_ms = float(default_max_timeout_ms)
        self._lock = threading.RLock()
        self.reset()

    def reset(self) -> None:
        """Reset latch telemetry metrics and counters."""
        with getattr(self, "_lock", threading.RLock()):
            self._total_latches: int = 0
            self._successful_latches: int = 0
            self._timed_out_latches: int = 0
            self._total_wait_ms: float = 0.0
            self._last_settle_time_ms: float = 0.0

    def wait_until_idle(
        self,
        page: Any,
        idle_timeout_ms: Optional[float] = None,
        max_timeout_ms: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Wait until page DOM mutations settle and document readyState reaches complete."""
        idle_ms = float(idle_timeout_ms) if idle_timeout_ms is not None else self.default_idle_ms
        max_ms = float(max_timeout_ms) if max_timeout_ms is not None else self.default_max_timeout_ms
        t0 = time.perf_counter()

        with self._lock:
            self._total_latches += 1

        if page is None:
            return {
                "is_idle": False,
                "error": "compliance : not possible. Page target represents None.",
                "settle_time_ms": 0.0,
                "mutations_observed": 0,
            }

        if hasattr(page, "evaluate"):
            try:
                script = DOM_IDLE_SCRIPT.replace("{idle_ms}", str(int(idle_ms))).replace("{max_ms}", str(int(max_ms)))
                result = page.evaluate(script)
                duration_ms = round((time.perf_counter() - t0) * 1000.0, 2)
                is_idle = bool(result.get("is_idle", True)) if isinstance(result, dict) else True
                mutations = int(result.get("mutations_observed", 0)) if isinstance(result, dict) else 0

                with self._lock:
                    if is_idle:
                        self._successful_latches += 1
                    else:
                        self._timed_out_latches += 1
                    self._total_wait_ms += duration_ms
                    self._last_settle_time_ms = duration_ms

                return {
                    "is_idle": is_idle,
                    "settle_time_ms": duration_ms,
                    "mutations_observed": mutations,
                    "ready_state": result.get("ready_state", "complete") if isinstance(result, dict) else "complete",
                }
            except Exception as exc:
                duration_ms = round((time.perf_counter() - t0) * 1000.0, 2)
                with self._lock:
                    self._timed_out_latches += 1
                    self._total_wait_ms += duration_ms
                return {
                    "is_idle": False,
                    "error": str(exc),
                    "settle_time_ms": duration_ms,
                    "mutations_observed": 0,
                }

        duration_ms = round((time.perf_counter() - t0) * 1000.0, 2)
        with self._lock:
            self._successful_latches += 1
            self._total_wait_ms += duration_ms
            self._last_settle_time_ms = duration_ms

        return {
            "is_idle": True,
            "settle_time_ms": duration_ms,
            "mutations_observed": 0,
            "ready_state": "complete",
        }

    def evaluate_stability(self, html_snapshots: Sequence[str]) -> bool:
        """Return true when sequential HTML snapshots remain identical."""
        if not html_snapshots:
            return True
        first = html_snapshots[0]
        return all(s == first for s in html_snapshots[1:])

    def measure_dom_depth(self, html_source: str) -> int:
        """Calculate element nesting depth in HTML string."""
        parser = _DOMDepthParser()
        try:
            parser.feed(html_source)
        except Exception:
            return 0
        return parser.max_depth

    def count_elements(self, html_source: str, tag: Optional[str] = None) -> int:
        """Count element occurrences in HTML string."""
        parser = _DOMDepthParser()
        try:
            parser.feed(html_source)
        except Exception:
            return 0
        if tag is not None:
            return parser.tag_counts.get(tag.lower(), 0)
        return sum(parser.tag_counts.values())

    def get_metrics(self) -> Dict[str, Any]:
        """Return DOM idle latch telemetry counters."""
        with self._lock:
            return {
                "default_idle_ms": self.default_idle_ms,
                "default_max_timeout_ms": self.default_max_timeout_ms,
                "total_latches": self._total_latches,
                "successful_latches": self._successful_latches,
                "timed_out_latches": self._timed_out_latches,
                "total_wait_ms": self._total_wait_ms,
                "last_settle_time_ms": self._last_settle_time_ms,
            }

    def reset_metrics(self) -> None:
        """Reset telemetry counters preserving configuration."""
        with self._lock:
            self._total_latches = 0
            self._successful_latches = 0
            self._timed_out_latches = 0
            self._total_wait_ms = 0.0
            self._last_settle_time_ms = 0.0


_DEFAULT_DOM_IDLE_LATCH = DomIdleLatch()


def get_default_dom_idle_latch() -> DomIdleLatch:
    """Return default singleton DOM idle latch."""
    return _DEFAULT_DOM_IDLE_LATCH


def reset_dom_idle_latch() -> None:
    """Reset global DOM idle latch state."""
    _DEFAULT_DOM_IDLE_LATCH.reset()


def create_dom_idle_latch(
    default_idle_ms: float = 200.0,
    default_max_timeout_ms: float = 10000.0,
) -> DomIdleLatch:
    """Instantiate a new dedicated DOM idle latch."""
    return DomIdleLatch(default_idle_ms=default_idle_ms, default_max_timeout_ms=default_max_timeout_ms)


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

    def wait_for_dom_idle(
        self,
        idle_timeout_ms: float = 200.0,
        timeout_ms: float = 10000.0,
    ) -> Dict[str, Any]:
        """Wait until DOM mutations settle and document reaches complete state."""
        page = self._ensure_page()
        latch = get_default_dom_idle_latch()
        return latch.wait_until_idle(page, idle_timeout_ms=idle_timeout_ms, max_timeout_ms=timeout_ms)

    def browse(self, url: str, wait_until: str = "load", timeout_ms: int = 30000) -> Dict[str, Any]:
        """Navigate to a URL and return page metadata and content preview."""
        page = self._ensure_page()
        target_url = url if "://" in url else f"https://{url}"
        pw_wait = "load" if wait_until == "dom_idle" else wait_until
        response = page.goto(target_url, wait_until=pw_wait, timeout=timeout_ms)
        if wait_until == "dom_idle":
            self.wait_for_dom_idle(timeout_ms=timeout_ms)
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
        elif act in ("wait_idle", "dom_idle", "settle"):
            res = session.wait_for_dom_idle(timeout_ms=timeout_ms)
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
