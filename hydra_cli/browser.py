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
import collections
import hashlib
import math
import struct
import zlib
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


class ScreenshotGate:
    """Playwright screenshot validation gate enforcing visual bounds, format integrity, and non-blank visual guarantees."""

    def __init__(
        self,
        default_min_bytes: int = 100,
        default_max_bytes: Optional[int] = None,
        allow_solid_color: bool = False,
        **kwargs: Any,
    ):
        """Initialize screenshot gate with validation thresholds."""
        self.default_min_bytes = int(default_min_bytes)
        self.default_max_bytes = int(default_max_bytes) if default_max_bytes is not None else None
        self.allow_solid_color = bool(allow_solid_color)
        self.default_min_width = int(kwargs["default_min_width"]) if "default_min_width" in kwargs and kwargs["default_min_width"] is not None else None
        self.default_min_height = int(kwargs["default_min_height"]) if "default_min_height" in kwargs and kwargs["default_min_height"] is not None else None
        self.default_max_width = int(kwargs["default_max_width"]) if "default_max_width" in kwargs and kwargs["default_max_width"] is not None else None
        self.default_max_height = int(kwargs["default_max_height"]) if "default_max_height" in kwargs and kwargs["default_max_height"] is not None else None
        self._lock = threading.RLock()
        self.reset()

    def reset_metrics(self) -> None:
        """Reset telemetry counters preserving configuration."""
        with getattr(self, "_lock", threading.RLock()):
            self._total_validations: int = 0
            self._passed_validations: int = 0
            self._failed_validations: int = 0
            self._last_validation_status: str = "NONE"

    def reset(self) -> None:
        """Reset validation gate state."""
        self.reset_metrics()

    def get_metrics(self) -> Dict[str, Any]:
        """Return screenshot gate telemetry counters."""
        with self._lock:
            return {
                "default_min_bytes": self.default_min_bytes,
                "default_max_bytes": self.default_max_bytes,
                "default_min_width": self.default_min_width,
                "default_min_height": self.default_min_height,
                "default_max_width": self.default_max_width,
                "default_max_height": self.default_max_height,
                "allow_solid_color": self.allow_solid_color,
                "total_validations": self._total_validations,
                "passed_validations": self._passed_validations,
                "failed_validations": self._failed_validations,
                "last_validation_status": self._last_validation_status,
            }

    def detect_format(self, data: bytes) -> Optional[str]:
        """Detect image format from header magic bytes."""
        if not data:
            return None
        if len(data) >= 8 and data[:8] == b"\x89PNG\r\n\x1a\n":
            return "png"
        if len(data) >= 3 and data[:3] == b"\xff\xd8\xff":
            return "jpeg"
        if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
            return "webp"
        if len(data) >= 6 and (data[:6] == b"GIF87a" or data[:6] == b"GIF89a"):
            return "gif"
        if len(data) >= 2 and data[:2] == b"BM":
            return "bmp"
        return None

    def parse_png_dimensions(self, data: bytes) -> Optional[Tuple[int, int]]:
        """Extract width and height from PNG IHDR chunk."""
        if len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n":
            return None
        w, h = struct.unpack(">II", data[16:24])
        return (w, h)

    def parse_jpeg_dimensions(self, data: bytes) -> Optional[Tuple[int, int]]:
        """Extract width and height from JPEG Start of Frame markers."""
        if len(data) < 4 or data[:2] != b"\xff\xd8":
            return None
        idx = 2
        while idx < len(data):
            if data[idx] != 0xFF:
                idx += 1
                continue
            while idx < len(data) and data[idx] == 0xFF:
                idx += 1
            if idx >= len(data):
                break
            marker = data[idx]
            idx += 1
            if marker in (0xD8, 0xD9) or (0xD0 <= marker <= 0xD7):
                continue
            if idx + 2 > len(data):
                break
            length = struct.unpack(">H", data[idx:idx + 2])[0]
            if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                if idx + 7 <= len(data):
                    h, w = struct.unpack(">HH", data[idx + 3:idx + 7])
                    return (w, h)
            idx += length
        return None

    def parse_webp_dimensions(self, data: bytes) -> Optional[Tuple[int, int]]:
        """Extract width and height from WebP header chunks."""
        if len(data) < 16 or data[:4] != b"RIFF" or data[8:12] != b"WEBP":
            return None
        chunk_type = data[12:16]
        if chunk_type == b"VP8 " and len(data) >= 30:
            w, h = struct.unpack("<HH", data[26:30])
            return (w & 0x3FFF, h & 0x3FFF)
        if chunk_type == b"VP8L" and len(data) >= 25 and data[20] == 0x2F:
            b0, b1, b2, b3 = data[21:25]
            w = 1 + (((b1 & 0x3F) << 8) | b0)
            h = 1 + (((b3 & 0x0F) << 10) | (b2 << 2) | ((b1 & 0xC0) >> 6))
            return (w, h)
        if chunk_type == b"VP8X" and len(data) >= 30:
            w = 1 + int.from_bytes(data[24:27], "little")
            h = 1 + int.from_bytes(data[27:30], "little")
            return (w, h)
        return None

    def parse_gif_dimensions(self, data: bytes) -> Optional[Tuple[int, int]]:
        """Extract width and height from GIF screen descriptor."""
        if len(data) >= 10 and (data[:6] == b"GIF87a" or data[:6] == b"GIF89a"):
            return struct.unpack("<HH", data[6:10])
        return None

    def parse_bmp_dimensions(self, data: bytes) -> Optional[Tuple[int, int]]:
        """Extract width and height from BMP DIB header."""
        if len(data) >= 26 and data[:2] == b"BM":
            w, h = struct.unpack("<ii", data[18:26])
            return (abs(w), abs(h))
        return None

    def parse_dimensions(self, data: bytes) -> Optional[Tuple[int, int]]:
        """Extract width and height across supported image formats."""
        fmt = self.detect_format(data)
        if fmt == "png":
            return self.parse_png_dimensions(data)
        if fmt == "jpeg":
            return self.parse_jpeg_dimensions(data)
        if fmt == "webp":
            return self.parse_webp_dimensions(data)
        if fmt == "gif":
            return self.parse_gif_dimensions(data)
        if fmt == "bmp":
            return self.parse_bmp_dimensions(data)
        return None

    def is_solid_color(self, data: bytes) -> bool:
        """Evaluate whether image contains uniform solid color without visual features."""
        if not data:
            return True
        fmt = self.detect_format(data)
        if fmt == "png":
            idx = 8
            idats: List[bytes] = []
            width, height = 0, 0
            color_type = 2
            bit_depth = 8
            while idx + 8 <= len(data):
                chunk_len = struct.unpack(">I", data[idx:idx + 4])[0]
                chunk_type = data[idx + 4:idx + 8]
                if chunk_type == b"IHDR" and chunk_len >= 13:
                    width, height = struct.unpack(">II", data[idx + 8:idx + 16])
                    bit_depth = data[idx + 16]
                    color_type = data[idx + 17]
                elif chunk_type == b"IDAT":
                    idats.append(data[idx + 8:idx + 8 + chunk_len])
                idx += 12 + chunk_len
            if idats and width > 0 and height > 0:
                try:
                    decomp = zlib.decompress(b"".join(idats))
                    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}.get(color_type, 3)
                    bpp = max(1, (bit_depth * channels) // 8)
                    stride = 1 + width * bpp
                    if len(decomp) == stride * height:
                        first_pixel = decomp[1:1 + bpp]
                        for y in range(height):
                            row_start = y * stride
                            row_filter = decomp[row_start]
                            if row_filter == 0:
                                row_pixels = decomp[row_start + 1:row_start + stride]
                                if row_pixels != first_pixel * width:
                                    return False
                            else:
                                return len(set(decomp)) <= 2
                        return True
                except Exception:
                    pass
        counts = collections.Counter(data)
        total = len(data)
        entropy = 0.0
        for count in counts.values():
            p = count / total
            entropy -= p * math.log2(p)
        return entropy < 1.2

    def compute_image_fingerprint(self, data: bytes) -> str:
        """Return SHA-256 fingerprint hash for binary image payload."""
        return hashlib.sha256(data).hexdigest()

    def compare_baselines(
        self,
        candidate_bytes: bytes,
        baseline_bytes: bytes,
        max_diff_ratio: float = 0.05,
    ) -> Dict[str, Any]:
        """Compare candidate image bytes against baseline bytes within max tolerance ratio."""
        if not candidate_bytes and not baseline_bytes:
            return {
                "match": True,
                "diff_ratio": 0.0,
                "diff_bytes": 0,
                "candidate_bytes": 0,
                "baseline_bytes": 0,
            }
        if not candidate_bytes or not baseline_bytes:
            return {
                "match": False,
                "diff_ratio": 1.0,
                "diff_bytes": max(len(candidate_bytes), len(baseline_bytes)),
                "candidate_bytes": len(candidate_bytes),
                "baseline_bytes": len(baseline_bytes),
            }
        if candidate_bytes == baseline_bytes:
            return {
                "match": True,
                "diff_ratio": 0.0,
                "diff_bytes": 0,
                "candidate_bytes": len(candidate_bytes),
                "baseline_bytes": len(baseline_bytes),
            }

        max_len = max(len(candidate_bytes), len(baseline_bytes))
        min_len = min(len(candidate_bytes), len(baseline_bytes))
        diff_count = abs(len(candidate_bytes) - len(baseline_bytes))

        for i in range(min_len):
            if candidate_bytes[i] != baseline_bytes[i]:
                diff_count += 1

        diff_ratio = round(diff_count / max_len, 4)
        return {
            "match": diff_ratio <= max_diff_ratio,
            "diff_ratio": diff_ratio,
            "diff_bytes": diff_count,
            "candidate_bytes": len(candidate_bytes),
            "baseline_bytes": len(baseline_bytes),
        }

    def validate_image_bytes(
        self,
        data: bytes,
        min_bytes: Optional[int] = None,
        max_bytes: Optional[int] = None,
        check_blank: Optional[bool] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """Validate binary image content against dimensional, size, and blankness thresholds."""
        with self._lock:
            self._total_validations += 1

        errors: List[str] = []
        eff_min_bytes = min_bytes if min_bytes is not None else self.default_min_bytes
        eff_max_bytes = max_bytes if max_bytes is not None else self.default_max_bytes
        eff_min_w = kwargs.get("min_width", self.default_min_width)
        eff_min_h = kwargs.get("min_height", self.default_min_height)
        eff_max_w = kwargs.get("max_width", self.default_max_width)
        eff_max_h = kwargs.get("max_height", self.default_max_height)
        should_check_blank = check_blank if check_blank is not None else (not self.allow_solid_color)

        size = len(data)
        if size == 0:
            errors.append("Empty image payload with 0 bytes")
        elif eff_min_bytes is not None and size < eff_min_bytes:
            errors.append(f"Image byte size {size} below minimum bound {eff_min_bytes}")
        elif eff_max_bytes is not None and size > eff_max_bytes:
            errors.append(f"Image byte size {size} exceeds maximum bound {eff_max_bytes}")

        fmt = self.detect_format(data)
        if fmt is None and size > 0:
            errors.append("Unrecognized image signature; expected PNG, JPEG, WebP, GIF, or BMP")

        dims = self.parse_dimensions(data) if fmt else None
        width = dims[0] if dims else None
        height = dims[1] if dims else None

        if dims is not None:
            w, h = dims
            if eff_min_w is not None and w < eff_min_w:
                errors.append(f"Image width {w} below minimum bound {eff_min_w}")
            if eff_min_h is not None and h < eff_min_h:
                errors.append(f"Image height {h} below minimum bound {eff_min_h}")
            if eff_max_w is not None and w > eff_max_w:
                errors.append(f"Image width {w} exceeds maximum bound {eff_max_w}")
            if eff_max_h is not None and h > eff_max_h:
                errors.append(f"Image height {h} exceeds maximum bound {eff_max_h}")

        is_solid = False
        if size > 0 and should_check_blank:
            is_solid = self.is_solid_color(data)
            if is_solid:
                errors.append("Image detected as solid blank or unfeatured uniform color")

        fingerprint = self.compute_image_fingerprint(data) if size > 0 else ""
        valid = (len(errors) == 0)

        with self._lock:
            if valid:
                self._passed_validations += 1
                self._last_validation_status = "PASSED"
            else:
                self._failed_validations += 1
                self._last_validation_status = "FAILED"

        return {
            "valid": valid,
            "format": fmt,
            "dimensions": dims,
            "width": width,
            "height": height,
            "size_bytes": size,
            "fingerprint": fingerprint,
            "is_solid": is_solid,
            "errors": errors,
        }

    def validate_image_file(
        self,
        path: str,
        min_bytes: Optional[int] = None,
        max_bytes: Optional[int] = None,
        check_blank: Optional[bool] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """Validate image file at specified filesystem path."""
        if not path or not os.path.isfile(path):
            with self._lock:
                self._total_validations += 1
                self._failed_validations += 1
                self._last_validation_status = "FAILED"
            return {
                "valid": False,
                "format": None,
                "dimensions": None,
                "width": None,
                "height": None,
                "size_bytes": 0,
                "fingerprint": "",
                "is_solid": False,
                "errors": [f"Image file target not found: {path}"],
            }
        try:
            with open(path, "rb") as f:
                data = f.read()
            res = self.validate_image_bytes(
                data=data,
                min_bytes=min_bytes,
                max_bytes=max_bytes,
                check_blank=check_blank,
                **kwargs,
            )
            res["path"] = path
            return res
        except Exception as exc:
            with self._lock:
                self._total_validations += 1
                self._failed_validations += 1
                self._last_validation_status = "FAILED"
            return {
                "valid": False,
                "format": None,
                "dimensions": None,
                "width": None,
                "height": None,
                "size_bytes": 0,
                "fingerprint": "",
                "is_solid": False,
                "errors": [f"Error reading image file {path}: {exc}"],
            }


_DEFAULT_SCREENSHOT_GATE = ScreenshotGate()


def get_default_screenshot_gate() -> ScreenshotGate:
    """Return default singleton screenshot gate."""
    return _DEFAULT_SCREENSHOT_GATE


def reset_screenshot_gate() -> None:
    """Reset global screenshot gate state."""
    _DEFAULT_SCREENSHOT_GATE.reset()


def create_screenshot_gate(
    default_min_bytes: int = 100,
    default_max_bytes: Optional[int] = None,
    allow_solid_color: bool = False,
    **kwargs: Any,
) -> ScreenshotGate:
    """Instantiate a new dedicated screenshot gate."""
    return ScreenshotGate(
        default_min_bytes=default_min_bytes,
        default_max_bytes=default_max_bytes,
        allow_solid_color=allow_solid_color,
        **kwargs,
    )


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

    def screenshot_verified(
        self,
        path: Optional[str] = None,
        full_page: bool = False,
        check_blank: bool = True,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """Capture screenshot and verify dimensional, size, and non-blank visual contracts."""
        shot_res = self.screenshot(path=path, full_page=full_page)
        target_path = shot_res.get("path")
        gate = get_default_screenshot_gate()
        val_res = gate.validate_image_file(
            path=target_path or "",
            check_blank=check_blank,
            **kwargs,
        )
        return {
            "path": target_path,
            "url": shot_res.get("url", ""),
            "size_bytes": val_res.get("size_bytes", shot_res.get("size_bytes", 0)),
            "valid": val_res.get("valid", False),
            "format": val_res.get("format"),
            "dimensions": val_res.get("dimensions"),
            "width": val_res.get("width"),
            "height": val_res.get("height"),
            "fingerprint": val_res.get("fingerprint", ""),
            "is_solid": val_res.get("is_solid", False),
            "errors": val_res.get("errors", []),
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
        elif act in ("screenshot_verified", "verified_screenshot", "screenshot_gate"):
            res = session.screenshot_verified(path=path, full_page=full_page)
            if not res.get("valid", False):
                return {
                    "isError": True,
                    "error": f"Screenshot validation rejected: {res.get('errors', [])}",
                    "result": res,
                }
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
