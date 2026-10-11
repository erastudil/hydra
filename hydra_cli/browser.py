"""
Playwright browser automation engine for Hydra CLI.
Supports browsing, clicking, typing, screenshots, and content extraction.
Safe lazy import ensures clean execution whether Playwright is installed or uninstalled.
"""

from __future__ import annotations

import atexit
import json
import re
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
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

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


class BrowserConsoleEntry:
    """Structured representation of single browser console or page error message."""

    def __init__(
        self,
        level: str,
        text: str,
        timestamp: Optional[float] = None,
        location: Optional[str] = None,
        args: Optional[List[str]] = None,
    ):
        """Initialize console entry record."""
        self.level = level.lower().strip()
        self.text = text
        self.timestamp = timestamp if timestamp is not None else time.time()
        self.location = location
        self.args = list(args) if args else []

    def to_dict(self) -> Dict[str, Any]:
        """Return serializable dictionary representation."""
        return {
            "level": self.level,
            "text": self.text,
            "timestamp": self.timestamp,
            "location": self.location,
            "args": self.args,
        }


_CONSOLE_SEVERITY: Dict[str, int] = {
    "debug": 10,
    "log": 20,
    "info": 20,
    "warn": 30,
    "warning": 30,
    "error": 40,
    "pageerror": 50,
}


class BrowserConsoleCapture:
    """Playwright browser console message capture and monitoring engine."""

    SECRET_PATTERNS = [
        re.compile(r"Bearer\s+([a-zA-Z0-9_\-\.\=]{16,})", re.IGNORECASE),
        re.compile(r"\b(sk-[a-zA-Z0-9_\-]{16,}|ghp_[a-zA-Z0-9]{16,}|xox[baprs]-[a-zA-Z0-9\-]{16,})\b"),
        re.compile(r"""(?i)(api[_-]?key|secret|token|password)\s*[:=]\s*['"]?([a-zA-Z0-9_\-\.\=]{12,})['"]?"""),
    ]

    def __init__(
        self,
        max_entries: int = 1000,
        scrub_secrets: bool = True,
    ):
        """Initialize console capture engine with buffer bounds."""
        self.max_entries = max(1, int(max_entries))
        self.scrub_secrets = bool(scrub_secrets)
        self._lock = threading.RLock()
        self._entries: collections.deque[BrowserConsoleEntry] = collections.deque(maxlen=self.max_entries)
        self._attached_pages: Set[int] = set()
        self.reset_metrics()

    def reset_metrics(self) -> None:
        """Reset telemetry counters preserving buffer contents."""
        with getattr(self, "_lock", threading.RLock()):
            self._total_captured: int = 0
            self._error_count: int = 0
            self._warning_count: int = 0
            self._info_count: int = 0
            self._page_error_count: int = 0

    def reset(self) -> None:
        """Clear buffer and reset telemetry metrics."""
        with self._lock:
            self._entries.clear()
            self._attached_pages.clear()
            self.reset_metrics()

    def clear(self) -> None:
        """Empty captured console log entries buffer."""
        with self._lock:
            self._entries.clear()

    def _scrub(self, text: str) -> str:
        """Scrub credentials and sensitive tokens from console text."""
        if not self.scrub_secrets or not text:
            return text
        res = text
        for pat in self.SECRET_PATTERNS:
            res = pat.sub("[REDACTED_SECRET]", res)
        return res

    def record_entry(
        self,
        level: str,
        text: str,
        location: Optional[str] = None,
        args: Optional[List[str]] = None,
    ) -> BrowserConsoleEntry:
        """Record single console entry into bounded buffer."""
        lvl = (level or "log").lower().strip()
        scrubbed = self._scrub(str(text or ""))
        entry = BrowserConsoleEntry(
            level=lvl,
            text=scrubbed,
            timestamp=time.time(),
            location=location,
            args=[self._scrub(str(a)) for a in args] if args else None,
        )
        with self._lock:
            self._entries.append(entry)
            self._total_captured += 1
            if lvl in ("error",):
                self._error_count += 1
            elif lvl in ("warn", "warning"):
                self._warning_count += 1
            elif lvl in ("pageerror",):
                self._page_error_count += 1
                self._error_count += 1
            else:
                self._info_count += 1
        return entry

    def record_page_error(
        self,
        error_text: str,
        location: Optional[str] = None,
    ) -> BrowserConsoleEntry:
        """Record unhandled page exception into bounded buffer."""
        return self.record_entry(level="pageerror", text=error_text, location=location)

    def attach_to_page(self, page: Any) -> bool:
        """Attach event listeners to Playwright page target."""
        if page is None or not hasattr(page, "on"):
            return False
        page_id = id(page)
        with self._lock:
            if page_id in self._attached_pages:
                return True
            self._attached_pages.add(page_id)

        try:
            def on_console(msg: Any) -> None:
                try:
                    msg_type = getattr(msg, "type", "log")
                    msg_text = getattr(msg, "text", str(msg))
                    msg_loc = getattr(msg, "location", None)
                    loc_str = f"{msg_loc.get('url', '')}:{msg_loc.get('lineNumber', '')}" if isinstance(msg_loc, dict) else str(msg_loc) if msg_loc else None
                    self.record_entry(level=msg_type, text=msg_text, location=loc_str)
                except Exception:
                    pass

            def on_page_error(exc: Any) -> None:
                try:
                    self.record_page_error(str(exc))
                except Exception:
                    pass

            page.on("console", on_console)
            page.on("pageerror", on_page_error)
            return True
        except Exception:
            return False

    def get_entries(
        self,
        level: Optional[str] = None,
        min_level: Optional[str] = None,
        limit: Optional[int] = None,
        search: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Query captured console entries with optional severity and search filtering."""
        with self._lock:
            entries = list(self._entries)

        res: List[Dict[str, Any]] = []
        filter_lvl = level.lower().strip() if level else None
        min_sev = _CONSOLE_SEVERITY.get(min_level.lower().strip(), 0) if min_level else 0
        query = search.lower().strip() if search else None

        for e in entries:
            if filter_lvl and e.level != filter_lvl:
                continue
            if min_sev > 0 and _CONSOLE_SEVERITY.get(e.level, 20) < min_sev:
                continue
            if query and query not in e.text.lower():
                continue
            res.append(e.to_dict())

        if limit is not None and limit > 0:
            return res[-limit:]
        return res

    def get_errors(self, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """Return captured error and pageerror entries."""
        with self._lock:
            entries = [e.to_dict() for e in self._entries if e.level in ("error", "pageerror")]
        if limit is not None and limit > 0:
            return entries[-limit:]
        return entries

    def has_errors(self) -> bool:
        """Evaluate whether any error or pageerror entries recorded."""
        with self._lock:
            return any(e.level in ("error", "pageerror") for e in self._entries)

    def get_metrics(self) -> Dict[str, Any]:
        """Return console capture telemetry counters."""
        with self._lock:
            return {
                "max_entries": self.max_entries,
                "current_entries": len(self._entries),
                "total_captured": self._total_captured,
                "error_count": self._error_count,
                "warning_count": self._warning_count,
                "info_count": self._info_count,
                "page_error_count": self._page_error_count,
                "attached_pages": len(self._attached_pages),
            }


_DEFAULT_CONSOLE_CAPTURE = BrowserConsoleCapture()


def get_default_console_capture() -> BrowserConsoleCapture:
    """Return default singleton console capture engine."""
    return _DEFAULT_CONSOLE_CAPTURE


def reset_console_capture() -> None:
    """Reset global console capture engine state."""
    _DEFAULT_CONSOLE_CAPTURE.reset()


def create_console_capture(
    max_entries: int = 1000,
    scrub_secrets: bool = True,
) -> BrowserConsoleCapture:
    """Instantiate a new dedicated console capture engine."""
    return BrowserConsoleCapture(max_entries=max_entries, scrub_secrets=scrub_secrets)


class _FormFieldsParser(html.parser.HTMLParser):
    """HTML parser discovering form inputs, textareas, selects, and buttons."""

    def __init__(self, target_form_id: Optional[str] = None) -> None:
        super().__init__()
        self.target_form_id = target_form_id.lstrip("#") if target_form_id else None
        self.fields: List[Dict[str, Any]] = []
        self._inside_target_form = self.target_form_id is None
        self._current_tag: Optional[str] = None
        self._current_attrs: Dict[str, str] = {}
        self._current_text: List[str] = []

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        attr_dict = {k.lower(): (v or "") for k, v in attrs}
        t = tag.lower()
        if t == "form":
            if self.target_form_id:
                form_id = attr_dict.get("id", "")
                form_name = attr_dict.get("name", "")
                if form_id == self.target_form_id or form_name == self.target_form_id:
                    self._inside_target_form = True
            return

        if not self._inside_target_form:
            return

        if t == "input":
            self.fields.append({
                "tag": "input",
                "type": attr_dict.get("type", "text").lower(),
                "name": attr_dict.get("name", ""),
                "id": attr_dict.get("id", ""),
                "value": attr_dict.get("value", ""),
                "required": "required" in attr_dict,
                "placeholder": attr_dict.get("placeholder", ""),
            })
        elif t in ("textarea", "select", "button"):
            self._current_tag = t
            self._current_attrs = attr_dict
            self._current_text = []

    def handle_endtag(self, tag: str) -> None:
        t = tag.lower()
        if t == "form":
            if self.target_form_id:
                self._inside_target_form = False
            return

        if not self._inside_target_form:
            return

        if self._current_tag and t == self._current_tag:
            text_val = "".join(self._current_text).strip()
            self.fields.append({
                "tag": self._current_tag,
                "type": self._current_attrs.get("type", self._current_tag),
                "name": self._current_attrs.get("name", ""),
                "id": self._current_attrs.get("id", ""),
                "value": self._current_attrs.get("value", text_val),
                "required": "required" in self._current_attrs,
                "placeholder": self._current_attrs.get("placeholder", ""),
            })
            self._current_tag = None
            self._current_attrs = {}
            self._current_text = []

    def handle_data(self, data: str) -> None:
        if self._current_tag:
            self._current_text.append(data)


class FormAutofill:
    """Playwright automated form inspection, payload validation, and batch filling engine."""

    def __init__(self, default_timeout_ms: float = 15000.0):
        """Initialize form autofill engine with operational defaults."""
        self.default_timeout_ms = float(default_timeout_ms)
        self._lock = threading.RLock()
        self.reset()

    def reset_metrics(self) -> None:
        """Reset telemetry counters preserving configuration."""
        with getattr(self, "_lock", threading.RLock()):
            self._total_autofills: int = 0
            self._fields_filled: int = 0
            self._forms_submitted: int = 0
            self._failures: int = 0

    def reset(self) -> None:
        """Reset form autofill state and telemetry."""
        self.reset_metrics()

    def parse_form_html(
        self,
        html_source: str,
        form_selector: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Extract structured field descriptors from raw HTML source."""
        if not html_source:
            return []
        parser = _FormFieldsParser(target_form_id=form_selector)
        try:
            parser.feed(html_source)
            return parser.fields
        except Exception:
            return []

    def validate_form_payload(
        self,
        fields: Dict[str, Any],
        required_fields: Optional[Sequence[str]] = None,
    ) -> Dict[str, Any]:
        """Validate field mapping dictionary against required field identifiers."""
        missing: List[str] = []
        empty: List[str] = []

        if required_fields:
            for req in required_fields:
                if req not in fields:
                    missing.append(req)
                elif fields[req] is None or str(fields[req]).strip() == "":
                    empty.append(req)

        valid = (len(missing) == 0 and len(empty) == 0)
        return {
            "valid": valid,
            "missing_fields": missing,
            "empty_fields": empty,
            "provided_count": len(fields),
        }

    def inspect_form(
        self,
        page: Any,
        form_selector: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Inspect page form elements and extract structured field descriptors."""
        if page is None:
            return {
                "isError": True,
                "error": "compliance : not possible. Page target represents None.",
                "fields": [],
                "count": 0,
            }
        try:
            content = page.content() if hasattr(page, "content") else ""
            fields = self.parse_form_html(content, form_selector=form_selector)
            return {
                "isError": False,
                "fields": fields,
                "count": len(fields),
                "form_selector": form_selector,
            }
        except Exception as exc:
            return {
                "isError": True,
                "error": f"Error inspecting form: {exc}",
                "fields": [],
                "count": 0,
            }

    def fill_form(
        self,
        page: Any,
        fields: Dict[str, Any],
        form_selector: Optional[str] = None,
        submit: bool = False,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """Execute batch input population across form controls with optional submission."""
        with self._lock:
            self._total_autofills += 1

        if page is None:
            with self._lock:
                self._failures += 1
            return {
                "isError": True,
                "error": "compliance : not possible. Page target represents None.",
                "fields_filled": 0,
                "submitted": False,
            }

        timeout = kwargs.get("timeout_ms", self.default_timeout_ms)
        submit_selector = kwargs.get("submit_selector")
        filled_count = 0
        errors: List[str] = []

        for selector, value in fields.items():
            target_sel = f"{form_selector} {selector}" if form_selector and not selector.startswith(form_selector) else selector
            try:
                if hasattr(page, "locator"):
                    loc = page.locator(target_sel).first
                    if isinstance(value, bool):
                        if value:
                            loc.check(timeout=timeout) if hasattr(loc, "check") else None
                        else:
                            loc.uncheck(timeout=timeout) if hasattr(loc, "uncheck") else None
                    elif isinstance(value, (list, tuple)):
                        loc.select_option(list(value), timeout=timeout) if hasattr(loc, "select_option") else None
                    else:
                        loc.fill(str(value), timeout=timeout) if hasattr(loc, "fill") else None
                filled_count += 1
            except Exception as exc:
                errors.append(f"Failed to fill control {target_sel}: {exc}")

        submitted = False
        if submit and hasattr(page, "locator"):
            try:
                if submit_selector:
                    sub_loc = page.locator(submit_selector).first
                    sub_loc.click(timeout=timeout)
                elif form_selector:
                    sub_loc = page.locator(f"{form_selector} button[type='submit'], {form_selector} input[type='submit']").first
                    sub_loc.click(timeout=timeout)
                else:
                    sub_loc = page.locator("button[type='submit'], input[type='submit']").first
                    sub_loc.click(timeout=timeout)
                submitted = True
                with self._lock:
                    self._forms_submitted += 1
            except Exception as exc:
                errors.append(f"Failed to submit form: {exc}")

        with self._lock:
            self._fields_filled += filled_count
            if errors:
                self._failures += 1

        return {
            "isError": len(errors) > 0 and filled_count == 0,
            "fields_filled": filled_count,
            "total_requested": len(fields),
            "submitted": submitted,
            "errors": errors,
        }

    def get_metrics(self) -> Dict[str, Any]:
        """Return form autofill telemetry counters."""
        with self._lock:
            return {
                "default_timeout_ms": self.default_timeout_ms,
                "total_autofills": self._total_autofills,
                "fields_filled": self._fields_filled,
                "forms_submitted": self._forms_submitted,
                "failures": self._failures,
            }


_DEFAULT_FORM_AUTOFILL = FormAutofill()


def get_default_form_autofill() -> FormAutofill:
    """Return default singleton form autofill engine."""
    return _DEFAULT_FORM_AUTOFILL


def reset_form_autofill() -> None:
    """Reset global form autofill engine state."""
    _DEFAULT_FORM_AUTOFILL.reset()


def create_form_autofill(default_timeout_ms: float = 15000.0) -> FormAutofill:
    """Instantiate a new dedicated form autofill engine."""
    return FormAutofill(default_timeout_ms=default_timeout_ms)


MUTATION_WAIT_SCRIPT = """
(() => {
    return new Promise((resolve) => {
        const selector = "{selector}";
        const timeoutMs = {timeout_ms};
        const types = {types_json};
        const target = selector === "body" ? (document.body || document.documentElement) : document.querySelector(selector);

        if (!target) {
            resolve({
                success: false,
                mutated: false,
                error: "Target selector element not found: " + selector,
                mutations_observed: 0,
                duration_ms: 0
            });
            return;
        }

        const startTime = Date.now();
        let mutationCount = 0;
        let timer = null;

        function finish(mutated, timedOut) {
            if (observer) observer.disconnect();
            if (timer) clearTimeout(timer);
            resolve({
                success: mutated && !timedOut,
                mutated: mutated,
                timed_out: timedOut,
                mutations_observed: mutationCount,
                duration_ms: Date.now() - startTime
            });
        }

        timer = setTimeout(() => finish(mutationCount > 0, true), timeoutMs);

        const observer = new MutationObserver((mutations) => {
            mutationCount += mutations.length;
            finish(true, false);
        });

        observer.observe(target, {
            childList: types.includes("childList"),
            attributes: types.includes("attributes"),
            characterData: types.includes("characterData"),
            subtree: true
        });
    });
})()
"""


class MutationWaiter:
    """Playwright DOM mutation monitoring engine awaiting targeted structural or attribute changes."""

    def __init__(
        self,
        default_timeout_ms: float = 10000.0,
        default_selector: str = "body",
    ):
        """Initialize mutation waiter with timeout and selector defaults."""
        self.default_timeout_ms = float(default_timeout_ms)
        self.default_selector = str(default_selector)
        self._lock = threading.RLock()
        self.reset()

    def reset_metrics(self) -> None:
        """Reset telemetry counters preserving configuration."""
        with getattr(self, "_lock", threading.RLock()):
            self._total_waits: int = 0
            self._successful_waits: int = 0
            self._timed_out_waits: int = 0
            self._total_wait_ms: float = 0.0

    def reset(self) -> None:
        """Reset mutation waiter state and telemetry."""
        self.reset_metrics()

    def detect_mutations(
        self,
        old_html: str,
        new_html: str,
    ) -> Dict[str, Any]:
        """Evaluate structural and character mutations between sequential HTML snapshots."""
        mutated = (old_html != new_html)
        len_diff = len(new_html) - len(old_html)
        return {
            "mutated": mutated,
            "length_difference": len_diff,
            "old_length": len(old_html),
            "new_length": len(new_html),
        }

    def wait_for_mutation(
        self,
        page: Any,
        selector: Optional[str] = None,
        timeout_ms: Optional[float] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """Wait until targeted DOM element observes specified mutation event."""
        with self._lock:
            self._total_waits += 1

        if page is None:
            return {
                "isError": True,
                "error": "compliance : not possible. Page target represents None.",
                "mutated": False,
                "mutations_observed": 0,
            }

        sel = selector or self.default_selector
        timeout = float(timeout_ms) if timeout_ms is not None else self.default_timeout_ms
        types = list(kwargs.get("mutation_types") or ["childList", "attributes", "characterData"])
        t0 = time.perf_counter()

        if hasattr(page, "evaluate"):
            try:
                script = (
                    MUTATION_WAIT_SCRIPT
                    .replace("{selector}", sel)
                    .replace("{timeout_ms}", str(int(timeout)))
                    .replace("{types_json}", json.dumps(types))
                )
                res = page.evaluate(script)
                dur = round((time.perf_counter() - t0) * 1000.0, 2)
                mutated = bool(res.get("mutated", False)) if isinstance(res, dict) else False
                timed_out = bool(res.get("timed_out", False)) if isinstance(res, dict) else False
                mutations = int(res.get("mutations_observed", 0)) if isinstance(res, dict) else 0

                with self._lock:
                    if mutated and not timed_out:
                        self._successful_waits += 1
                    else:
                        self._timed_out_waits += 1
                    self._total_wait_ms += dur

                return {
                    "isError": False,
                    "mutated": mutated,
                    "timed_out": timed_out,
                    "mutations_observed": mutations,
                    "duration_ms": dur,
                    "selector": sel,
                }
            except Exception as exc:
                dur = round((time.perf_counter() - t0) * 1000.0, 2)
                with self._lock:
                    self._timed_out_waits += 1
                    self._total_wait_ms += dur
                return {
                    "isError": True,
                    "error": f"Mutation wait failed: {exc}",
                    "mutated": False,
                    "duration_ms": dur,
                    "selector": sel,
                }

        dur = round((time.perf_counter() - t0) * 1000.0, 2)
        with self._lock:
            self._successful_waits += 1
            self._total_wait_ms += dur
        return {
            "isError": False,
            "mutated": True,
            "timed_out": False,
            "mutations_observed": 1,
            "duration_ms": dur,
            "selector": sel,
        }

    def get_metrics(self) -> Dict[str, Any]:
        """Return mutation waiter telemetry counters."""
        with self._lock:
            return {
                "default_timeout_ms": self.default_timeout_ms,
                "default_selector": self.default_selector,
                "total_waits": self._total_waits,
                "successful_waits": self._successful_waits,
                "timed_out_waits": self._timed_out_waits,
                "total_wait_ms": self._total_wait_ms,
            }


_DEFAULT_MUTATION_WAITER = MutationWaiter()


def get_default_mutation_waiter() -> MutationWaiter:
    """Return default singleton mutation waiter engine."""
    return _DEFAULT_MUTATION_WAITER


def reset_mutation_waiter() -> None:
    """Reset global mutation waiter engine state."""
    _DEFAULT_MUTATION_WAITER.reset()


def create_mutation_waiter(
    default_timeout_ms: float = 10000.0,
    default_selector: str = "body",
) -> MutationWaiter:
    """Instantiate a new dedicated mutation waiter engine."""
    return MutationWaiter(default_timeout_ms=default_timeout_ms, default_selector=default_selector)


class NetworkIdleLatch:
    """Playwright network activity and idle state latching engine."""

    def __init__(
        self,
        default_idle_ms: float = 500.0,
        default_timeout_ms: float = 10000.0,
        default_max_inflight: int = 0,
        ignored_patterns: Optional[Sequence[str]] = None,
    ):
        """Initialize network idle latch with timing and filter configurations."""
        self.default_idle_ms = float(default_idle_ms)
        self.default_timeout_ms = float(default_timeout_ms)
        self.default_max_inflight = int(default_max_inflight)
        self.ignored_patterns = [re.compile(p) if isinstance(p, str) else p for p in (ignored_patterns or [])]
        self._lock = threading.RLock()
        self._inflight: Dict[str, float] = {}
        self._attached_pages: Set[int] = set()
        self.reset()

    def reset_metrics(self) -> None:
        """Reset telemetry counters preserving configuration."""
        with getattr(self, "_lock", threading.RLock()):
            self._total_requests: int = 0
            self._finished_requests: int = 0
            self._failed_requests: int = 0
            self._total_waits: int = 0
            self._successful_waits: int = 0
            self._timed_out_waits: int = 0
            self._last_wait_ms: float = 0.0

    def reset(self) -> None:
        """Reset network latch state and telemetry."""
        with self._lock:
            self._inflight.clear()
            self._attached_pages.clear()
            self.reset_metrics()

    def _should_ignore(self, url: str) -> bool:
        """Evaluate whether URL matches ignored pattern filters."""
        if not url or not self.ignored_patterns:
            return False
        return any(pat.search(url) for pat in self.ignored_patterns)

    def record_request(self, request_id: str, url: str) -> None:
        """Record outbound network request initiation."""
        if self._should_ignore(url):
            return
        with self._lock:
            self._inflight[request_id] = time.time()
            self._total_requests += 1

    def record_finished(self, request_id: str) -> None:
        """Record completed network request settlement."""
        with self._lock:
            if request_id in self._inflight:
                del self._inflight[request_id]
                self._finished_requests += 1

    def record_failed(self, request_id: str) -> None:
        """Record aborted or failed network request settlement."""
        with self._lock:
            if request_id in self._inflight:
                del self._inflight[request_id]
                self._failed_requests += 1

    def get_inflight_count(self) -> int:
        """Return number of currently active in-flight requests."""
        with self._lock:
            return len(self._inflight)

    def is_idle(self, max_inflight: Optional[int] = None) -> bool:
        """Evaluate whether in-flight request count satisfies idle threshold."""
        limit = max_inflight if max_inflight is not None else self.default_max_inflight
        with self._lock:
            return len(self._inflight) <= limit

    def attach_to_page(self, page: Any) -> bool:
        """Attach network event listeners to Playwright page target."""
        if page is None or not hasattr(page, "on"):
            return False
        page_id = id(page)
        with self._lock:
            if page_id in self._attached_pages:
                return True
            self._attached_pages.add(page_id)

        try:
            def on_req(req: Any) -> None:
                try:
                    req_id = str(id(req))
                    url = getattr(req, "url", "")
                    self.record_request(req_id, url)
                except Exception:
                    pass

            def on_finished(req: Any) -> None:
                try:
                    req_id = str(id(req))
                    self.record_finished(req_id)
                except Exception:
                    pass

            def on_failed(req: Any) -> None:
                try:
                    req_id = str(id(req))
                    self.record_failed(req_id)
                except Exception:
                    pass

            page.on("request", on_req)
            page.on("requestfinished", on_finished)
            page.on("requestfailed", on_failed)
            return True
        except Exception:
            return False

    def wait_until_idle(
        self,
        page: Any,
        idle_time_ms: Optional[float] = None,
        timeout_ms: Optional[float] = None,
        max_inflight: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Wait until page network requests reach idle threshold."""
        with self._lock:
            self._total_waits += 1

        if page is None:
            return {
                "is_idle": False,
                "error": "compliance : not possible. Page target represents None.",
                "duration_ms": 0.0,
                "inflight_count": 0,
            }

        timeout = float(timeout_ms) if timeout_ms is not None else self.default_timeout_ms
        limit = max_inflight if max_inflight is not None else self.default_max_inflight
        t0 = time.perf_counter()

        if hasattr(page, "wait_for_load_state"):
            try:
                page.wait_for_load_state("networkidle", timeout=timeout)
                dur = round((time.perf_counter() - t0) * 1000.0, 2)
                with self._lock:
                    self._successful_waits += 1
                    self._last_wait_ms = dur
                return {
                    "is_idle": True,
                    "duration_ms": dur,
                    "inflight_count": self.get_inflight_count(),
                }
            except Exception as exc:
                dur = round((time.perf_counter() - t0) * 1000.0, 2)
                with self._lock:
                    self._timed_out_waits += 1
                    self._last_wait_ms = dur
                return {
                    "is_idle": False,
                    "error": str(exc),
                    "duration_ms": dur,
                    "inflight_count": self.get_inflight_count(),
                }

        dur = round((time.perf_counter() - t0) * 1000.0, 2)
        idle = self.is_idle(limit)
        with self._lock:
            if idle:
                self._successful_waits += 1
            else:
                self._timed_out_waits += 1
            self._last_wait_ms = dur

        return {
            "is_idle": idle,
            "duration_ms": dur,
            "inflight_count": self.get_inflight_count(),
        }

    def get_metrics(self) -> Dict[str, Any]:
        """Return network idle latch telemetry counters."""
        with self._lock:
            return {
                "default_idle_ms": self.default_idle_ms,
                "default_timeout_ms": self.default_timeout_ms,
                "default_max_inflight": self.default_max_inflight,
                "current_inflight": len(self._inflight),
                "total_requests": self._total_requests,
                "finished_requests": self._finished_requests,
                "failed_requests": self._failed_requests,
                "total_waits": self._total_waits,
                "successful_waits": self._successful_waits,
                "timed_out_waits": self._timed_out_waits,
                "last_wait_ms": self._last_wait_ms,
            }


_DEFAULT_NETWORK_IDLE_LATCH = NetworkIdleLatch()


def get_default_network_idle_latch() -> NetworkIdleLatch:
    """Return default singleton network idle latch engine."""
    return _DEFAULT_NETWORK_IDLE_LATCH


def reset_network_idle_latch() -> None:
    """Reset global network idle latch state."""
    _DEFAULT_NETWORK_IDLE_LATCH.reset()


def create_network_idle_latch(
    default_idle_ms: float = 500.0,
    default_timeout_ms: float = 10000.0,
    default_max_inflight: int = 0,
    ignored_patterns: Optional[Sequence[str]] = None,
) -> NetworkIdleLatch:
    """Instantiate a new dedicated network idle latch engine."""
    return NetworkIdleLatch(
        default_idle_ms=default_idle_ms,
        default_timeout_ms=default_timeout_ms,
        default_max_inflight=default_max_inflight,
        ignored_patterns=ignored_patterns,
    )


class DownloadVerifier:
    """Playwright file download tracking, checksum calculation, and integrity validation engine."""

    def __init__(self, default_download_dir: Optional[str] = None):
        """Initialize download verifier with destination directory configuration."""
        self.default_download_dir = default_download_dir or os.path.join(
            os.path.expanduser("~"), ".hydra", "downloads"
        )
        self._lock = threading.RLock()
        self._downloads: List[Dict[str, Any]] = []
        self._attached_pages: Set[int] = set()
        self.reset()

    def reset_metrics(self) -> None:
        """Reset telemetry counters preserving configuration."""
        with getattr(self, "_lock", threading.RLock()):
            self._total_verifications: int = 0
            self._passed_verifications: int = 0
            self._failed_verifications: int = 0
            self._total_downloads_tracked: int = 0

    def reset(self) -> None:
        """Reset download verifier state and telemetry."""
        with self._lock:
            self._downloads.clear()
            self._attached_pages.clear()
            self.reset_metrics()

    def compute_checksum(self, data: bytes, algorithm: str = "sha256") -> str:
        """Calculate cryptographic hash for binary payload."""
        alg = algorithm.lower().strip()
        if alg == "md5":
            return hashlib.md5(data).hexdigest()
        if alg == "sha1":
            return hashlib.sha1(data).hexdigest()
        return hashlib.sha256(data).hexdigest()

    def detect_magic_type(self, data: bytes) -> str:
        """Detect binary payload content type from magic header signatures."""
        if not data:
            return "empty"
        if len(data) >= 4 and data[:4] == b"%PDF":
            return "pdf"
        if len(data) >= 4 and data[:4] == b"PK\x03\x04":
            return "zip"
        if len(data) >= 2 and data[:2] == b"\x1f\x8b":
            return "gzip"
        if len(data) >= 8 and data[:8] == b"\x89PNG\r\n\x1a\n":
            return "png"
        if len(data) >= 3 and data[:3] == b"\xff\xd8\xff":
            return "jpeg"
        if len(data) >= 4 and data[:4] == b"RIFF":
            return "riff"
        try:
            parsed = json.loads(data.decode("utf-8"))
            if isinstance(parsed, (dict, list)):
                return "json"
        except Exception:
            pass
        try:
            data.decode("utf-8")
            return "text"
        except Exception:
            return "binary"

    def verify_bytes(
        self,
        data: bytes,
        expected_hash: Optional[str] = None,
        min_bytes: int = 1,
        max_bytes: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Validate binary payload against size boundaries and checksum invariants."""
        with self._lock:
            self._total_verifications += 1

        errors: List[str] = []
        size = len(data)

        if size < min_bytes:
            errors.append(f"Payload size {size} below minimum bound {min_bytes}")
        if max_bytes is not None and size > max_bytes:
            errors.append(f"Payload size {size} exceeds maximum bound {max_bytes}")

        actual_sha256 = self.compute_checksum(data, "sha256")
        if expected_hash:
            exp_clean = expected_hash.strip().lower()
            if actual_sha256.lower() != exp_clean:
                errors.append(f"Checksum mismatch: expected {exp_clean} but computed {actual_sha256}")

        magic_type = self.detect_magic_type(data)
        valid = (len(errors) == 0)

        with self._lock:
            if valid:
                self._passed_verifications += 1
            else:
                self._failed_verifications += 1

        return {
            "valid": valid,
            "size_bytes": size,
            "sha256": actual_sha256,
            "magic_type": magic_type,
            "errors": errors,
        }

    def verify_download(
        self,
        file_path: str,
        expected_hash: Optional[str] = None,
        min_bytes: int = 1,
        max_bytes: Optional[int] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """Validate downloaded file at specified path against integrity constraints."""
        if not file_path or not os.path.isfile(file_path):
            with self._lock:
                self._total_verifications += 1
                self._failed_verifications += 1
            return {
                "valid": False,
                "file_path": file_path,
                "size_bytes": 0,
                "sha256": "",
                "magic_type": "none",
                "errors": [f"File not found on disk: {file_path}"],
            }

        expected_ext = kwargs.get("expected_extension")
        if expected_ext:
            clean_exp = expected_ext.lower().lstrip(".")
            _, ext = os.path.splitext(file_path)
            clean_act = ext.lower().lstrip(".")
            if clean_act != clean_exp:
                with self._lock:
                    self._total_verifications += 1
                    self._failed_verifications += 1
                return {
                    "valid": False,
                    "file_path": file_path,
                    "size_bytes": os.path.getsize(file_path),
                    "sha256": "",
                    "magic_type": "none",
                    "errors": [f"Extension mismatch: expected .{clean_exp} but found .{clean_act}"],
                }

        try:
            with open(file_path, "rb") as f:
                data = f.read()
            res = self.verify_bytes(
                data=data,
                expected_hash=expected_hash,
                min_bytes=min_bytes,
                max_bytes=max_bytes,
            )
            res["file_path"] = file_path
            return res
        except Exception as exc:
            with self._lock:
                self._total_verifications += 1
                self._failed_verifications += 1
            return {
                "valid": False,
                "file_path": file_path,
                "size_bytes": 0,
                "sha256": "",
                "magic_type": "none",
                "errors": [f"Read failure: {exc}"],
            }

    def attach_to_page(
        self,
        page: Any,
        download_dir: Optional[str] = None,
    ) -> bool:
        """Attach download event listeners to Playwright page target."""
        if page is None or not hasattr(page, "on"):
            return False
        page_id = id(page)
        with self._lock:
            if page_id in self._attached_pages:
                return True
            self._attached_pages.add(page_id)

        target_dir = download_dir or self.default_download_dir

        try:
            def on_download(download: Any) -> None:
                try:
                    os.makedirs(target_dir, exist_ok=True)
                    suggested_name = getattr(download, "suggested_filename", "download.bin")
                    save_path = os.path.join(target_dir, suggested_name)
                    if hasattr(download, "save_as"):
                        download.save_as(save_path)
                    rec = {
                        "filename": suggested_name,
                        "path": save_path,
                        "url": getattr(download, "url", ""),
                    }
                    with self._lock:
                        self._downloads.append(rec)
                        self._total_downloads_tracked += 1
                except Exception:
                    pass

            page.on("download", on_download)
            return True
        except Exception:
            return False

    def get_downloads(self) -> List[Dict[str, Any]]:
        """Return list of captured download records."""
        with self._lock:
            return list(self._downloads)

    def get_metrics(self) -> Dict[str, Any]:
        """Return download verifier telemetry counters."""
        with self._lock:
            return {
                "default_download_dir": self.default_download_dir,
                "total_verifications": self._total_verifications,
                "passed_verifications": self._passed_verifications,
                "failed_verifications": self._failed_verifications,
                "total_downloads_tracked": self._total_downloads_tracked,
                "active_records_count": len(self._downloads),
            }


_DEFAULT_DOWNLOAD_VERIFIER = DownloadVerifier()


def get_default_download_verifier() -> DownloadVerifier:
    """Return default singleton download verifier engine."""
    return _DEFAULT_DOWNLOAD_VERIFIER


def reset_download_verifier() -> None:
    """Reset global download verifier state."""
    _DEFAULT_DOWNLOAD_VERIFIER.reset()


def create_download_verifier(default_download_dir: Optional[str] = None) -> DownloadVerifier:
    """Instantiate a new dedicated download verifier engine."""
    return DownloadVerifier(default_download_dir=default_download_dir)

class _IframeParser(html.parser.HTMLParser):
    """HTML parser discovering and extracting metadata from iframe tags."""

    def __init__(self) -> None:
        super().__init__()
        self.frames: List[Dict[str, Any]] = []
        self._stack: List[str] = []

    def handle_starttag(self, tag: str, attrs: Sequence[Tuple[str, Optional[str]]]) -> None:
        """Process opening tag to locate iframe elements."""
        tag_lower = tag.lower()
        self._stack.append(tag_lower)
        if tag_lower in ("iframe", "frame"):
            attr_dict = {k.lower(): (v or "") for k, v in attrs}
            current_depth = len([t for t in self._stack if t in ("iframe", "frame")])
            self.frames.append({
                "tag": tag_lower,
                "id": attr_dict.get("id", ""),
                "name": attr_dict.get("name", ""),
                "src": attr_dict.get("src", ""),
                "title": attr_dict.get("title", ""),
                "sandbox": attr_dict.get("sandbox", ""),
                "class": attr_dict.get("class", ""),
                "srcdoc": attr_dict.get("srcdoc", ""),
                "depth": current_depth,
            })

    def handle_endtag(self, tag: str) -> None:
        """Process closing tag and decrement nesting stack."""
        tag_lower = tag.lower()
        if self._stack and self._stack[-1] == tag_lower:
            self._stack.pop()
        elif tag_lower in self._stack:
            idx = len(self._stack) - 1 - self._stack[::-1].index(tag_lower)
            self._stack = self._stack[:idx]


class IframeTraversal:
    """
    Playwright iframe traversal and inspection engine.
    Navigates nested iframe trees, locates frames by identifier or URL,
    and executes actions across frame boundaries.
    """

    def __init__(self, max_depth: int = 10) -> None:
        self.max_depth = int(max_depth)
        self._lock = threading.RLock()
        self.reset()

    def reset(self) -> None:
        """Reset internal metrics and frame registries."""
        with getattr(self, "_lock", threading.RLock()):
            self._total_traversals: int = 0
            self._frames_discovered: int = 0
            self._actions_executed: int = 0
            self._errors_encountered: int = 0

    def reset_metrics(self) -> None:
        """Reset telemetry counters to zero."""
        self.reset()

    def inspect_html(self, html_content: str) -> List[Dict[str, Any]]:
        """Parse HTML string and extract metadata for all iframe elements."""
        if not html_content or not isinstance(html_content, str):
            return []
        parser = _IframeParser()
        try:
            parser.feed(html_content)
        except Exception:
            with self._lock:
                self._errors_encountered += 1
            return []
        with self._lock:
            self._frames_discovered += len(parser.frames)
        return parser.frames

    def get_frame_tree(self, page_or_frame: Any) -> Dict[str, Any]:
        """Construct hierarchical tree representation of frames."""
        with self._lock:
            self._total_traversals += 1

        if page_or_frame is None:
            return {
                "name": "",
                "url": "",
                "is_detached": True,
                "depth": 0,
                "child_count": 0,
                "children": [],
            }

        root = getattr(page_or_frame, "main_frame", page_or_frame)
        return self._build_frame_node(root, depth=0)

    def _build_frame_node(self, frame: Any, depth: int = 0) -> Dict[str, Any]:
        """Recursively build frame node metadata."""
        if frame is None or depth > self.max_depth:
            return {
                "name": "",
                "url": "",
                "is_detached": True,
                "depth": depth,
                "child_count": 0,
                "children": [],
            }

        name = getattr(frame, "name", "")
        url = getattr(frame, "url", "")
        is_detached_fn = getattr(frame, "is_detached", None)
        detached = is_detached_fn() if callable(is_detached_fn) else False

        child_frames = getattr(frame, "child_frames", [])
        if not isinstance(child_frames, (list, tuple)):
            child_frames = []

        with self._lock:
            self._frames_discovered += 1

        children = [
            self._build_frame_node(child, depth=depth + 1)
            for child in child_frames
        ]

        return {
            "name": str(name or ""),
            "url": str(url or ""),
            "is_detached": bool(detached),
            "depth": depth,
            "child_count": len(children),
            "children": children,
        }

    def list_frames(self, page: Any) -> List[Dict[str, Any]]:
        """Return flat collection of all frames attached to page."""
        with self._lock:
            self._total_traversals += 1

        if page is None:
            return []

        frames_seq = getattr(page, "frames", None)
        if isinstance(frames_seq, (list, tuple)):
            res: List[Dict[str, Any]] = []
            main_f = getattr(page, "main_frame", None)
            for f in frames_seq:
                name = getattr(f, "name", "")
                url = getattr(f, "url", "")
                is_det_fn = getattr(f, "is_detached", None)
                detached = is_det_fn() if callable(is_det_fn) else False
                is_main = (f == main_f) if main_f is not None else False
                res.append({
                    "name": str(name or ""),
                    "url": str(url or ""),
                    "is_detached": bool(detached),
                    "is_main": bool(is_main),
                })
            with self._lock:
                self._frames_discovered += len(res)
            return res

        tree = self.get_frame_tree(page)
        flat: List[Dict[str, Any]] = []

        def _flatten(node: Dict[str, Any], is_main: bool = False) -> None:
            flat.append({
                "name": node.get("name", ""),
                "url": node.get("url", ""),
                "is_detached": node.get("is_detached", False),
                "is_main": is_main,
            })
            for c in node.get("children", []):
                _flatten(c, is_main=False)

        _flatten(tree, is_main=True)
        return flat

    def find_frame(
        self,
        page: Any,
        name: Optional[str] = None,
        url_pattern: Optional[str] = None,
        selector: Optional[str] = None,
    ) -> Optional[Any]:
        """Locate frame matching name, URL regex, or CSS selector."""
        if page is None:
            return None

        if name and hasattr(page, "frame"):
            try:
                found = page.frame(name=name)
                if found is not None:
                    return found
            except Exception:
                pass_err = True

        if url_pattern and hasattr(page, "frame"):
            try:
                found = page.frame(url=re.compile(url_pattern))
                if found is not None:
                    return found
            except Exception:
                pass_err = True

        frames = getattr(page, "frames", None)
        if isinstance(frames, (list, tuple)):
            for f in frames:
                f_name = getattr(f, "name", "")
                f_url = getattr(f, "url", "")
                if name and f_name == name:
                    return f
                if url_pattern and (url_pattern in f_url or re.search(url_pattern, f_url)):
                    return f

        if selector and hasattr(page, "frame_locator"):
            try:
                return page.frame_locator(selector)
            except Exception:
                pass_err = True

        return None

    def execute_in_frame(
        self,
        frame: Any,
        action: str,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """Execute interaction or evaluation within target frame."""
        with self._lock:
            self._actions_executed += 1

        if frame is None:
            with self._lock:
                self._errors_encountered += 1
            return {
                "valid": False,
                "action": action,
                "error": "compliance : not possible. Target frame represents None.",
            }

        act = (action or "").strip().lower()

        is_det_fn = getattr(frame, "is_detached", None)
        if callable(is_det_fn) and is_det_fn():
            with self._lock:
                self._errors_encountered += 1
            return {
                "valid": False,
                "action": act,
                "error": "Target frame detached from document.",
            }

        try:
            if act in ("evaluate", "eval", "script"):
                expr = kwargs.get("expression") or kwargs.get("script") or kwargs.get("text", "")
                if not hasattr(frame, "evaluate"):
                    raise AttributeError("Frame does not provide evaluate method")
                val = frame.evaluate(expr)
                return {"valid": True, "action": act, "result": val}

            elif act in ("extract_content", "extract", "content", "text"):
                sel = kwargs.get("selector")
                max_chars = kwargs.get("max_chars", 8000)
                if sel and hasattr(frame, "locator"):
                    loc = frame.locator(sel).first
                    cnt = loc.count() if hasattr(loc, "count") else 1
                    txt = loc.inner_text() if cnt > 0 else ""
                elif hasattr(frame, "inner_text"):
                    txt = frame.inner_text(sel or "body")
                elif hasattr(frame, "content"):
                    txt = frame.content()
                else:
                    txt = ""

                truncated = len(txt) > max_chars
                return {
                    "valid": True,
                    "action": act,
                    "result": txt[:max_chars],
                    "truncated": truncated,
                }

            elif act in ("click", "tap"):
                sel = kwargs.get("selector")
                if not sel:
                    raise ValueError("Selector required for click action")
                if hasattr(frame, "click"):
                    frame.click(sel)
                elif hasattr(frame, "locator"):
                    frame.locator(sel).click()
                else:
                    raise AttributeError("Frame does not support click dispatch")
                return {"valid": True, "action": act, "result": f"Clicked {sel}"}

            elif act in ("type", "fill", "input"):
                sel = kwargs.get("selector")
                txt = kwargs.get("text", "")
                if not sel:
                    raise ValueError("Selector required for type action")
                if hasattr(frame, "fill"):
                    frame.fill(sel, txt)
                elif hasattr(frame, "type"):
                    frame.type(sel, txt)
                elif hasattr(frame, "locator"):
                    frame.locator(sel).fill(txt)
                else:
                    raise AttributeError("Frame does not support type dispatch")
                return {"valid": True, "action": act, "result": f"Filled {sel}"}

            else:
                raise ValueError(f"Unsupported frame action '{action}'")

        except Exception as exc:
            with self._lock:
                self._errors_encountered += 1
            return {
                "valid": False,
                "action": act,
                "error": str(exc),
            }

    def get_metrics(self) -> Dict[str, Any]:
        """Return operational telemetry metrics."""
        with self._lock:
            return {
                "total_traversals": self._total_traversals,
                "frames_discovered": self._frames_discovered,
                "actions_executed": self._actions_executed,
                "errors_encountered": self._errors_encountered,
                "max_depth": self.max_depth,
            }


_DEFAULT_IFRAME_TRAVERSAL = IframeTraversal()


def get_default_iframe_traversal() -> IframeTraversal:
    """Return default singleton iframe traversal engine."""
    return _DEFAULT_IFRAME_TRAVERSAL


def reset_iframe_traversal() -> None:
    """Reset global iframe traversal state."""
    _DEFAULT_IFRAME_TRAVERSAL.reset()


def create_iframe_traversal(max_depth: int = 10) -> IframeTraversal:
    """Instantiate a new dedicated iframe traversal engine."""
    return IframeTraversal(max_depth=max_depth)

DEFAULT_BLOCKED_TYPES: Set[str] = {"image", "media", "font", "imageset", "ping"}
DEFAULT_BLOCKED_EXTENSIONS: Set[str] = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".ico",
    ".mp4", ".webm", ".ogv", ".mp3", ".wav", ".ogg",
    ".woff", ".woff2", ".ttf", ".otf", ".eot"
}


class MediaAbortController:
    """
    Playwright network route interception controller.
    Aborts heavy media and binary asset requests to optimize speed and bandwidth.
    """

    def __init__(
        self,
        blocked_types: Optional[Set[str]] = None,
        blocked_extensions: Optional[Set[str]] = None,
        allow_urls: Optional[Sequence[str]] = None,
        block_urls: Optional[Sequence[str]] = None,
        enabled: bool = True,
    ) -> None:
        self.blocked_types = set(blocked_types) if blocked_types is not None else set(DEFAULT_BLOCKED_TYPES)
        self.blocked_extensions = set(blocked_extensions) if blocked_extensions is not None else set(DEFAULT_BLOCKED_EXTENSIONS)
        self.allow_patterns = [re.compile(p) for p in (allow_urls or [])]
        self.block_patterns = [re.compile(p) for p in (block_urls or [])]
        self.enabled = bool(enabled)
        self._lock = threading.RLock()
        self._attached_pages: Set[int] = set()
        self.reset()

    def reset(self) -> None:
        """Reset internal telemetry metrics and URL buffers."""
        with getattr(self, "_lock", threading.RLock()):
            self._total_intercepted: int = 0
            self._aborted_requests: int = 0
            self._allowed_requests: int = 0
            self._aborted_urls: List[str] = []
            self._attached_pages.clear()

    def reset_metrics(self) -> None:
        """Reset operational telemetry counters."""
        self.reset()

    def enable(self) -> None:
        """Enable media abort interception filter."""
        self.enabled = True

    def disable(self) -> None:
        """Disable media abort interception filter."""
        self.enabled = False

    def should_abort(self, url: str, resource_type: Optional[str] = None) -> bool:
        """Evaluate whether target request qualifies for client abort."""
        if not self.enabled or not url:
            return False

        clean_url = url.split("?")[0].split("#")[0].lower()

        for pat in self.allow_patterns:
            if pat.search(url):
                return False

        for pat in self.block_patterns:
            if pat.search(url):
                return True

        if resource_type:
            clean_res = resource_type.strip().lower()
            if clean_res in self.blocked_types:
                return True

        for ext in self.blocked_extensions:
            if clean_url.endswith(ext.lower()):
                return True

        return False

    def attach_to_page(self, page: Any) -> bool:
        """Register route interception listener on target page."""
        if page is None or not hasattr(page, "route"):
            return False

        page_id = id(page)
        with self._lock:
            if page_id in self._attached_pages:
                return True
            self._attached_pages.add(page_id)

        try:
            def on_route(route: Any) -> None:
                with self._lock:
                    self._total_intercepted += 1

                req = getattr(route, "request", None)
                url = ""
                if req and hasattr(req, "url"):
                    url = req.url
                elif hasattr(route, "url"):
                    url = route.url

                res_type = ""
                if req and hasattr(req, "resource_type"):
                    res_val = req.resource_type
                    res_type = res_val() if callable(res_val) else str(res_val or "")

                if self.should_abort(url, res_type):
                    with self._lock:
                        self._aborted_requests += 1
                        self._aborted_urls.append(url)
                    if hasattr(route, "abort"):
                        route.abort("blockedbyclient")
                else:
                    with self._lock:
                        self._allowed_requests += 1
                    if hasattr(route, "continue_"):
                        route.continue_()

            page.route("**/*", on_route)
            return True
        except Exception:
            return False

    def get_metrics(self) -> Dict[str, Any]:
        """Return operational telemetry metrics."""
        with self._lock:
            return {
                "enabled": self.enabled,
                "total_intercepted": self._total_intercepted,
                "aborted_requests": self._aborted_requests,
                "allowed_requests": self._allowed_requests,
                "blocked_types_count": len(self.blocked_types),
                "blocked_extensions_count": len(self.blocked_extensions),
            }

    def get_aborted_urls(self) -> List[str]:
        """Return copy of aborted request URLs."""
        with self._lock:
            return list(self._aborted_urls)


_DEFAULT_MEDIA_ABORT = MediaAbortController()


def get_default_media_abort() -> MediaAbortController:
    """Return default singleton media abort controller."""
    return _DEFAULT_MEDIA_ABORT


def reset_media_abort() -> None:
    """Reset global media abort controller state."""
    _DEFAULT_MEDIA_ABORT.reset()


def create_media_abort(
    blocked_types: Optional[Set[str]] = None,
    blocked_extensions: Optional[Set[str]] = None,
    allow_urls: Optional[Sequence[str]] = None,
    block_urls: Optional[Sequence[str]] = None,
    enabled: bool = True,
) -> MediaAbortController:
    """Instantiate a new dedicated media abort controller."""
    return MediaAbortController(
        blocked_types=blocked_types,
        blocked_extensions=blocked_extensions,
        allow_urls=allow_urls,
        block_urls=block_urls,
        enabled=enabled,
    )

class _VisibilityHTMLParser(html.parser.HTMLParser):
    """HTML parser extracting visibility attributes and inline styles for elements."""

    def __init__(self, target_selector: str) -> None:
        super().__init__()
        self.target_selector = (target_selector or "").strip()
        self.found_elements: List[Dict[str, Any]] = []

    def handle_starttag(self, tag: str, attrs: Sequence[Tuple[str, Optional[str]]]) -> None:
        """Evaluate opening tag against target selector."""
        attr_dict = {k.lower(): (v or "") for k, v in attrs}
        tag_lower = tag.lower()
        elem_id = attr_dict.get("id", "")
        elem_classes = attr_dict.get("class", "").split()

        matches = False
        sel = self.target_selector
        if sel.startswith("#") and sel[1:] == elem_id:
            matches = True
        elif sel.startswith(".") and sel[1:] in elem_classes:
            matches = True
        elif sel.lower() == tag_lower:
            matches = True
        elif f"{tag_lower}#{elem_id}" == sel.lower() and elem_id:
            matches = True
        elif f"#{elem_id}" == sel or f".{attr_dict.get('class', '')}" == sel:
            matches = True

        if matches:
            style_str = attr_dict.get("style", "").lower()
            styles = {}
            for part in style_str.split(";"):
                if ":" in part:
                    k, v = part.split(":", 1)
                    styles[k.strip()] = v.strip()

            has_hidden_attr = "hidden" in attr_dict
            aria_hidden = attr_dict.get("aria-hidden", "").lower() == "true"
            display = styles.get("display", "block")
            visibility = styles.get("visibility", "visible")
            opacity_val = 1.0
            if "opacity" in styles:
                try:
                    opacity_val = float(styles["opacity"])
                except ValueError:
                    opacity_val = 1.0

            is_visible = (
                not has_hidden_attr
                and not aria_hidden
                and display != "none"
                and visibility != "hidden"
                and opacity_val > 0.0
            )

            self.found_elements.append({
                "tag": tag_lower,
                "id": elem_id,
                "classes": elem_classes,
                "has_hidden_attr": has_hidden_attr,
                "aria_hidden": aria_hidden,
                "display": display,
                "visibility": visibility,
                "opacity": opacity_val,
                "is_visible": is_visible,
                "style": styles,
            })


class VisibilityChecker:
    """
    Playwright element visibility inspection and verification engine.
    Audits DOM visibility, layout bounding boxes, viewport intersections,
    and computed style properties.
    """

    def __init__(self, default_timeout_ms: float = 5000.0) -> None:
        self.default_timeout_ms = float(default_timeout_ms)
        self._lock = threading.RLock()
        self.reset()

    def reset(self) -> None:
        """Reset internal telemetry counters."""
        with getattr(self, "_lock", threading.RLock()):
            self._total_checks: int = 0
            self._visible_count: int = 0
            self._hidden_count: int = 0
            self._missing_count: int = 0

    def reset_metrics(self) -> None:
        """Reset operational telemetry counters."""
        self.reset()

    def inspect_html(self, html_content: str, selector: str) -> Dict[str, Any]:
        """Inspect element visibility in HTML string via static style analysis."""
        with self._lock:
            self._total_checks += 1

        if not html_content or not selector:
            with self._lock:
                self._missing_count += 1
            return {
                "selector": selector,
                "exists": False,
                "is_visible": False,
                "reason": "Empty HTML content or selector parameter.",
            }

        parser = _VisibilityHTMLParser(selector)
        try:
            parser.feed(html_content)
        except Exception:
            with self._lock:
                self._missing_count += 1
            return {
                "selector": selector,
                "exists": False,
                "is_visible": False,
                "reason": "HTML parsing failure.",
            }

        if not parser.found_elements:
            with self._lock:
                self._missing_count += 1
            return {
                "selector": selector,
                "exists": False,
                "is_visible": False,
                "reason": "No element matching selector found in DOM.",
            }

        elem = parser.found_elements[0]
        vis = elem["is_visible"]
        with self._lock:
            if vis:
                self._visible_count += 1
            else:
                self._hidden_count += 1

        return {
            "selector": selector,
            "exists": True,
            "is_visible": vis,
            "tag": elem["tag"],
            "has_hidden_attr": elem["has_hidden_attr"],
            "aria_hidden": elem["aria_hidden"],
            "display": elem["display"],
            "visibility": elem["visibility"],
            "opacity": elem["opacity"],
            "elements_matched": len(parser.found_elements),
        }

    def check_visibility(
        self,
        page: Any,
        selector: str,
        check_viewport: bool = True,
        check_occlusion: bool = False,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """Check live element visibility, bounding boxes, and viewport status."""
        with self._lock:
            self._total_checks += 1

        if page is None or not selector:
            with self._lock:
                self._missing_count += 1
            return {
                "selector": selector,
                "exists": False,
                "is_visible": False,
                "error": "compliance : not possible. Page or selector represents None.",
            }

        if not hasattr(page, "locator"):
            with self._lock:
                self._missing_count += 1
            return {
                "selector": selector,
                "exists": False,
                "is_visible": False,
                "error": "Target page object does not provide locator method.",
            }

        try:
            loc = page.locator(selector).first
            cnt = loc.count() if hasattr(loc, "count") else 1
            if cnt == 0:
                with self._lock:
                    self._missing_count += 1
                return {
                    "selector": selector,
                    "exists": False,
                    "is_visible": False,
                    "in_viewport": False,
                    "bounding_box": None,
                }

            is_vis = loc.is_visible() if hasattr(loc, "is_visible") else True
            is_enabled = loc.is_enabled() if hasattr(loc, "is_enabled") else True
            is_editable = loc.is_editable() if hasattr(loc, "is_editable") else False

            box = loc.bounding_box() if hasattr(loc, "bounding_box") else None

            in_vp = True
            if check_viewport and box:
                vp_width = 1280
                vp_height = 800
                if hasattr(page, "viewport_size") and page.viewport_size:
                    vp_width = page.viewport_size.get("width", 1280)
                    vp_height = page.viewport_size.get("height", 800)

                x = box.get("x", 0)
                y = box.get("y", 0)
                w = box.get("width", 0)
                h = box.get("height", 0)

                if (x + w) <= 0 or (y + h) <= 0 or x >= vp_width or y >= vp_height:
                    in_vp = False
                elif w == 0 or h == 0:
                    in_vp = False

            with self._lock:
                if is_vis:
                    self._visible_count += 1
                else:
                    self._hidden_count += 1

            return {
                "selector": selector,
                "exists": True,
                "is_visible": bool(is_vis),
                "is_enabled": bool(is_enabled),
                "is_editable": bool(is_editable),
                "in_viewport": bool(in_vp),
                "bounding_box": box,
            }
        except Exception as exc:
            with self._lock:
                self._missing_count += 1
            return {
                "selector": selector,
                "exists": False,
                "is_visible": False,
                "error": str(exc),
            }

    def wait_for_visibility(
        self,
        page: Any,
        selector: str,
        visible: bool = True,
        timeout_ms: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Wait until element matches requested visibility state."""
        t_ms = float(timeout_ms) if timeout_ms is not None else self.default_timeout_ms

        if page is None or not selector:
            return {
                "selector": selector,
                "success": False,
                "error": "compliance : not possible. Page or selector represents None.",
            }

        try:
            if hasattr(page, "locator"):
                loc = page.locator(selector).first
                if hasattr(loc, "wait_for"):
                    loc.wait_for(state="visible" if visible else "hidden", timeout=t_ms)
                    return {"selector": selector, "success": True, "target_state": "visible" if visible else "hidden"}
            return {"selector": selector, "success": True, "target_state": "visible" if visible else "hidden"}
        except Exception as exc:
            return {"selector": selector, "success": False, "error": str(exc)}

    def get_metrics(self) -> Dict[str, Any]:
        """Return operational telemetry metrics."""
        with self._lock:
            return {
                "total_checks": self._total_checks,
                "visible_count": self._visible_count,
                "hidden_count": self._hidden_count,
                "missing_count": self._missing_count,
                "default_timeout_ms": self.default_timeout_ms,
            }


_DEFAULT_VISIBILITY_CHECKER = VisibilityChecker()


def get_default_visibility_checker() -> VisibilityChecker:
    """Return default singleton visibility checker engine."""
    return _DEFAULT_VISIBILITY_CHECKER


def reset_visibility_checker() -> None:
    """Reset global visibility checker state."""
    _DEFAULT_VISIBILITY_CHECKER.reset()


def create_visibility_checker(default_timeout_ms: float = 5000.0) -> VisibilityChecker:
    """Instantiate a new dedicated visibility checker engine."""
    return VisibilityChecker(default_timeout_ms=default_timeout_ms)

MODIFIER_MAP: Dict[str, str] = {
    "ctrl": "Control",
    "control": "Control",
    "alt": "Alt",
    "shift": "Shift",
    "meta": "Meta",
    "cmd": "Meta",
    "command": "Meta",
    "win": "Meta",
}

VALID_SPECIAL_KEYS: Set[str] = {
    "Enter", "Escape", "Tab", "Backspace", "Delete", "Insert",
    "ArrowDown", "ArrowUp", "ArrowLeft", "ArrowRight",
    "Home", "End", "PageUp", "PageDown",
    "F1", "F2", "F3", "F4", "F5", "F6", "F7", "F8", "F9", "F10", "F11", "F12",
    "Space", "CapsLock", "NumLock", "ScrollLock", "PrintScreen", "Pause",
}


class KeyboardController:
    """
    Playwright keyboard event dispatching and sequence simulation engine.
    Parses key combinations, standardizes modifier aliases, and simulates key sequences.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.reset()

    def reset(self) -> None:
        """Reset internal telemetry counters."""
        with getattr(self, "_lock", threading.RLock()):
            self._total_presses: int = 0
            self._total_chords: int = 0
            self._characters_typed: int = 0
            self._sequences_dispatched: int = 0
            self._history: List[Dict[str, Any]] = []

    def reset_metrics(self) -> None:
        """Reset operational telemetry counters."""
        self.reset()

    def normalize_chord(self, chord: str) -> str:
        """Normalize keyboard shortcut string with standard Playwright naming."""
        if not chord:
            return ""
        parts = [p.strip() for p in chord.split("+")]
        norm_parts = []
        for p in parts:
            p_lower = p.lower()
            if p_lower in MODIFIER_MAP:
                norm_parts.append(MODIFIER_MAP[p_lower])
            else:
                canonical = next((k for k in VALID_SPECIAL_KEYS if k.lower() == p_lower), None)
                if canonical:
                    norm_parts.append(canonical)
                elif len(p) == 1:
                    norm_parts.append(p.upper() if len(norm_parts) > 0 else p)
                else:
                    norm_parts.append(p)
        return "+".join(norm_parts)

    def press(
        self,
        page: Any,
        key_or_chord: str,
        delay_ms: float = 0.0,
    ) -> Dict[str, Any]:
        """Dispatch single key or chord combination to target page."""
        with self._lock:
            self._total_presses += 1

        if not key_or_chord:
            return {
                "success": False,
                "error": "compliance : not possible. Key parameter empty.",
            }

        norm_key = self.normalize_chord(key_or_chord)
        is_chord = "+" in norm_key

        with self._lock:
            if is_chord:
                self._total_chords += 1
            self._history.append({"type": "press", "key": norm_key, "delay_ms": delay_ms})

        if page is None:
            return {
                "success": False,
                "error": "compliance : not possible. Page represents None.",
                "normalized_key": norm_key,
            }

        try:
            kb = getattr(page, "keyboard", None)
            if kb and hasattr(kb, "press"):
                kb.press(norm_key, delay=delay_ms)
                return {
                    "success": True,
                    "key": norm_key,
                    "is_chord": is_chord,
                    "delay_ms": delay_ms,
                }
            return {
                "success": True,
                "key": norm_key,
                "is_chord": is_chord,
                "note": "Simulated keyboard event without native page driver.",
            }
        except Exception as exc:
            return {
                "success": False,
                "key": norm_key,
                "error": str(exc),
            }

    def type_text(
        self,
        page: Any,
        text: str,
        delay_ms: float = 0.0,
    ) -> Dict[str, Any]:
        """Type character string into page focused element with optional delay."""
        with self._lock:
            self._characters_typed += len(text or "")
            self._history.append({"type": "type", "text": text, "delay_ms": delay_ms})

        if page is None:
            return {
                "success": False,
                "error": "compliance : not possible. Page represents None.",
                "chars_count": len(text or ""),
            }

        try:
            kb = getattr(page, "keyboard", None)
            if kb and hasattr(kb, "type"):
                kb.type(text, delay=delay_ms)
                return {
                    "success": True,
                    "text": text,
                    "chars_count": len(text),
                    "delay_ms": delay_ms,
                }
            return {
                "success": True,
                "text": text,
                "chars_count": len(text),
                "note": "Simulated keyboard typing without native page driver.",
            }
        except Exception as exc:
            return {
                "success": False,
                "text": text,
                "error": str(exc),
            }

    def send_sequence(
        self,
        page: Any,
        sequence: Sequence[str],
        delay_ms: float = 0.0,
    ) -> Dict[str, Any]:
        """Dispatch sequence of keyboard actions or chords sequentially."""
        with self._lock:
            self._sequences_dispatched += 1

        results = []
        for step in (sequence or []):
            if step.startswith("type:"):
                payload = step[5:]
                res = self.type_text(page, payload, delay_ms=delay_ms)
            else:
                res = self.press(page, step, delay_ms=delay_ms)
            results.append(res)

        all_ok = all(r.get("success", False) for r in results) if results else True
        return {
            "success": all_ok,
            "steps_count": len(results),
            "results": results,
        }

    def get_metrics(self) -> Dict[str, Any]:
        """Return operational telemetry metrics."""
        with self._lock:
            return {
                "total_presses": self._total_presses,
                "total_chords": self._total_chords,
                "characters_typed": self._characters_typed,
                "sequences_dispatched": self._sequences_dispatched,
                "history_length": len(self._history),
            }

    def get_history(self) -> List[Dict[str, Any]]:
        """Return copy of keyboard event history."""
        with self._lock:
            return list(self._history)


_DEFAULT_KEYBOARD_CONTROLLER = KeyboardController()


def get_default_keyboard_controller() -> KeyboardController:
    """Return default singleton keyboard controller engine."""
    return _DEFAULT_KEYBOARD_CONTROLLER


def reset_keyboard_controller() -> None:
    """Reset global keyboard controller state."""
    _DEFAULT_KEYBOARD_CONTROLLER.reset()


def create_keyboard_controller() -> KeyboardController:
    """Instantiate a new dedicated keyboard controller engine."""
    return KeyboardController()

class TouchGestureController:
    """
    Playwright touch gesture dispatching and trajectory interpolation engine.
    Supports mobile touchscreen taps, swipes, and multi-point gesture sequences.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.reset()

    def reset(self) -> None:
        """Reset internal telemetry counters."""
        with getattr(self, "_lock", threading.RLock()):
            self._total_taps: int = 0
            self._total_swipes: int = 0
            self._gestures_dispatched: int = 0
            self._history: List[Dict[str, Any]] = []

    def reset_metrics(self) -> None:
        """Reset operational telemetry counters."""
        self.reset()

    def generate_trajectory(
        self,
        start: Tuple[float, float],
        end: Tuple[float, float],
        steps: int = 5,
    ) -> List[Tuple[float, float]]:
        """Calculate interpolated coordinate trajectory points."""
        cnt = max(1, int(steps))
        x0, y0 = float(start[0]), float(start[1])
        x1, y1 = float(end[0]), float(end[1])
        trajectory = []
        for i in range(cnt + 1):
            t = float(i) / float(cnt)
            xi = round(x0 + t * (x1 - x0), 2)
            yi = round(y0 + t * (y1 - y0), 2)
            trajectory.append((xi, yi))
        return trajectory

    def tap(
        self,
        page: Any,
        x: float = 0.0,
        y: float = 0.0,
        selector: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Dispatch touch tap event to coordinates or selector target."""
        with self._lock:
            self._total_taps += 1
            self._gestures_dispatched += 1

        tx, ty = float(x), float(y)

        if page is not None and selector and hasattr(page, "locator"):
            try:
                loc = page.locator(selector).first
                box = loc.bounding_box() if hasattr(loc, "bounding_box") else None
                if box:
                    tx = box.get("x", 0) + box.get("width", 0) / 2.0
                    ty = box.get("y", 0) + box.get("height", 0) / 2.0
            except Exception:
                pass_err = True

        with self._lock:
            self._history.append({"type": "tap", "x": tx, "y": ty, "selector": selector})

        if page is None:
            return {
                "success": False,
                "error": "compliance : not possible. Page represents None.",
                "x": tx,
                "y": ty,
            }

        try:
            ts = getattr(page, "touchscreen", None)
            if ts and hasattr(ts, "tap"):
                ts.tap(tx, ty)
                return {
                    "success": True,
                    "x": tx,
                    "y": ty,
                    "selector": selector,
                }
            return {
                "success": True,
                "x": tx,
                "y": ty,
                "selector": selector,
                "note": "Simulated touch tap without native touchscreen driver.",
            }
        except Exception as exc:
            return {
                "success": False,
                "error": str(exc),
            }

    def swipe(
        self,
        page: Any,
        start_pos: Tuple[float, float],
        end_pos: Tuple[float, float],
        steps: int = 5,
    ) -> Dict[str, Any]:
        """Dispatch touch swipe motion across coordinate trajectory."""
        with self._lock:
            self._total_swipes += 1
            self._gestures_dispatched += 1

        trajectory = self.generate_trajectory(start_pos, end_pos, steps=steps)

        with self._lock:
            self._history.append({
                "type": "swipe",
                "start": start_pos,
                "end": end_pos,
                "steps": len(trajectory),
            })

        if page is None:
            return {
                "success": False,
                "error": "compliance : not possible. Page represents None.",
                "steps_count": len(trajectory),
            }

        try:
            mouse = getattr(page, "mouse", None)
            if mouse and hasattr(mouse, "move") and hasattr(mouse, "down") and hasattr(mouse, "up"):
                mouse.move(start_pos[0], start_pos[1])
                mouse.down()
                for pt in trajectory[1:]:
                    mouse.move(pt[0], pt[1])
                mouse.up()
                return {
                    "success": True,
                    "trajectory_length": len(trajectory),
                    "start": start_pos,
                    "end": end_pos,
                }

            return {
                "success": True,
                "trajectory_length": len(trajectory),
                "start": start_pos,
                "end": end_pos,
                "note": "Simulated touch swipe trajectory without native driver.",
            }
        except Exception as exc:
            return {
                "success": False,
                "error": str(exc),
            }

    def get_metrics(self) -> Dict[str, Any]:
        """Return operational telemetry metrics."""
        with self._lock:
            return {
                "total_taps": self._total_taps,
                "total_swipes": self._total_swipes,
                "gestures_dispatched": self._gestures_dispatched,
                "history_length": len(self._history),
            }

    def get_history(self) -> List[Dict[str, Any]]:
        """Return copy of touch gesture history."""
        with self._lock:
            return list(self._history)


_DEFAULT_TOUCH_GESTURE = TouchGestureController()


def get_default_touch_gesture() -> TouchGestureController:
    """Return default singleton touch gesture controller."""
    return _DEFAULT_TOUCH_GESTURE


def reset_touch_gesture() -> None:
    """Reset global touch gesture controller state."""
    _DEFAULT_TOUCH_GESTURE.reset()


def create_touch_gesture() -> TouchGestureController:
    """Instantiate a new dedicated touch gesture controller."""
    return TouchGestureController()

def parse_rgb_tuple(color_str: str) -> Optional[Tuple[float, float, float]]:
    """Parse color string into normalized RGB float components between 0.0 and 1.0."""
    if not color_str:
        return None
    s = color_str.strip().lower()

    if s.startswith("#"):
        hex_val = s[1:]
        if len(hex_val) == 3:
            hex_val = "".join([c * 2 for c in hex_val])
        if len(hex_val) == 6:
            try:
                r = int(hex_val[0:2], 16) / 255.0
                g = int(hex_val[2:4], 16) / 255.0
                b = int(hex_val[4:6], 16) / 255.0
                return (r, g, b)
            except ValueError:
                return None

    rgb_match = re.match(r"^rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)", s)
    if rgb_match:
        try:
            r = int(rgb_match.group(1)) / 255.0
            g = int(rgb_match.group(2)) / 255.0
            b = int(rgb_match.group(3)) / 255.0
            return (r, g, b)
        except ValueError:
            return None

    named_map = {
        "black": (0.0, 0.0, 0.0),
        "white": (1.0, 1.0, 1.0),
        "red": (1.0, 0.0, 0.0),
        "green": (0.0, 1.0, 0.0),
        "blue": (0.0, 0.0, 1.0),
    }
    return named_map.get(s)


def relative_luminance(rgb: Tuple[float, float, float]) -> float:
    """Calculate WCAG 2.1 relative luminance for RGB tuple."""
    def adjust(c: float) -> float:
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = adjust(rgb[0]), adjust(rgb[1]), adjust(rgb[2])
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


class ColorSchemeTester:
    """
    Playwright color scheme and dark mode testing engine.
    Emulates preferred color schemes, parses dark mode media queries,
    and evaluates WCAG relative luminance contrast ratios.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.reset()

    def reset(self) -> None:
        """Reset internal telemetry counters."""
        with getattr(self, "_lock", threading.RLock()):
            self._total_emulations: int = 0
            self._dark_evaluations: int = 0
            self._contrast_checks: int = 0
            self._last_scheme: str = "light"

    def reset_metrics(self) -> None:
        """Reset operational telemetry counters."""
        self.reset()

    def compute_contrast_ratio(self, fg_color: str, bg_color: str) -> float:
        """Calculate WCAG contrast ratio between foreground and background colors."""
        with self._lock:
            self._contrast_checks += 1

        c1 = parse_rgb_tuple(fg_color)
        c2 = parse_rgb_tuple(bg_color)
        if not c1 or not c2:
            return 1.0

        l1 = relative_luminance(c1)
        l2 = relative_luminance(c2)
        lighter = max(l1, l2)
        darker = min(l1, l2)
        return round((lighter + 0.05) / (darker + 0.05), 2)

    def inspect_css(self, content: str) -> Dict[str, Any]:
        """Detect and extract dark mode media queries and theme rules in CSS or HTML."""
        with self._lock:
            self._dark_evaluations += 1

        if not content:
            return {
                "has_dark_mode": False,
                "prefers_color_scheme_dark": False,
                "prefers_color_scheme_light": False,
                "has_theme_class": False,
                "dark_rules_count": 0,
            }

        text = content.lower()
        has_prefers_dark = "prefers-color-scheme: dark" in text or "prefers-color-scheme:dark" in text
        has_prefers_light = "prefers-color-scheme: light" in text or "prefers-color-scheme:light" in text
        has_theme_class = ".dark" in text or 'data-theme="dark"' in text or "data-theme='dark'" in text

        dark_matches = re.findall(r"@media[^{]*prefers-color-scheme\s*:\s*dark[^{]*\{([^{}]*\{[^{}]*\})+", text)
        dark_rules_count = len(dark_matches)
        if has_theme_class and dark_rules_count == 0:
            dark_rules_count = 1

        has_dark = has_prefers_dark or has_theme_class

        return {
            "has_dark_mode": has_dark,
            "prefers_color_scheme_dark": has_prefers_dark,
            "prefers_color_scheme_light": has_prefers_light,
            "has_theme_class": has_theme_class,
            "dark_rules_count": dark_rules_count,
        }

    def emulate(
        self,
        page: Any,
        scheme: str = "dark",
    ) -> Dict[str, Any]:
        """Emulate color scheme on active page."""
        target_scheme = (scheme or "dark").strip().lower()
        if target_scheme not in ("dark", "light", "no-preference"):
            target_scheme = "dark"

        with self._lock:
            self._total_emulations += 1
            self._last_scheme = target_scheme

        if page is None:
            return {
                "success": False,
                "error": "compliance : not possible. Page represents None.",
                "scheme": target_scheme,
            }

        try:
            if hasattr(page, "emulate_media"):
                page.emulate_media(color_scheme=target_scheme)
                return {
                    "success": True,
                    "scheme": target_scheme,
                }
            return {
                "success": True,
                "scheme": target_scheme,
                "note": "Simulated media emulation without native page driver.",
            }
        except Exception as exc:
            return {
                "success": False,
                "scheme": target_scheme,
                "error": str(exc),
            }

    def get_metrics(self) -> Dict[str, Any]:
        """Return operational telemetry metrics."""
        with self._lock:
            return {
                "total_emulations": self._total_emulations,
                "dark_evaluations": self._dark_evaluations,
                "contrast_checks": self._contrast_checks,
                "last_scheme": self._last_scheme,
            }


_DEFAULT_COLOR_SCHEME_TESTER = ColorSchemeTester()


def get_default_color_scheme_tester() -> ColorSchemeTester:
    """Return default singleton color scheme tester engine."""
    return _DEFAULT_COLOR_SCHEME_TESTER


def reset_color_scheme_tester() -> None:
    """Reset global color scheme tester state."""
    _DEFAULT_COLOR_SCHEME_TESTER.reset()


def create_color_scheme_tester() -> ColorSchemeTester:
    """Instantiate a new dedicated color scheme tester engine."""
    return ColorSchemeTester()


STATUS_TEXT_MAP: Dict[int, str] = {
    100: "Continue",
    101: "Switching Protocols",
    200: "OK",
    201: "Created",
    202: "Accepted",
    204: "No Content",
    301: "Moved Permanently",
    302: "Found",
    304: "Not Modified",
    307: "Temporary Redirect",
    308: "Permanent Redirect",
    400: "Bad Request",
    401: "Unauthorized",
    403: "Forbidden",
    404: "Not Found",
    405: "Method Not Allowed",
    408: "Request Timeout",
    409: "Conflict",
    410: "Gone",
    422: "Unprocessable Entity",
    429: "Too Many Requests",
    500: "Internal Server Error",
    502: "Bad Gateway",
    503: "Service Unavailable",
    504: "Gateway Timeout",
}


class ResponseStatusAssert:
    """
    HTTP response status and header assertion subsystem for Playwright browser automation.
    Enforces status code matching, range classification, header validation, and telemetry tracking.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.reset()

    def reset(self) -> None:
        """Reset internal telemetry counters."""
        with getattr(self, "_lock", threading.RLock()):
            self._total_assertions: int = 0
            self._passed_assertions: int = 0
            self._failed_assertions: int = 0
            self._last_status: int = 0
            self._last_category: str = "unknown"

    def classify_status(self, status_code: int) -> str:
        """Classify HTTP status code into standard category."""
        code = int(status_code)
        if 100 <= code <= 199:
            return "informational"
        if 200 <= code <= 299:
            return "successful"
        if 300 <= code <= 399:
            return "redirection"
        if 400 <= code <= 499:
            return "client_error"
        if 500 <= code <= 599:
            return "server_error"
        return "unknown"

    def is_ok(self, status_code: int) -> bool:
        """Return true when status code represents success."""
        return 200 <= int(status_code) <= 299

    def is_redirect(self, status_code: int) -> bool:
        """Return true when status code represents redirection."""
        return 300 <= int(status_code) <= 399

    def is_client_error(self, status_code: int) -> bool:
        """Return true when status code represents client error."""
        return 400 <= int(status_code) <= 499

    def is_server_error(self, status_code: int) -> bool:
        """Return true when status code represents server error."""
        return 500 <= int(status_code) <= 599

    def is_error(self, status_code: int) -> bool:
        """Return true when status code represents client or server error."""
        return int(status_code) >= 400

    def get_status_text(self, status_code: int) -> str:
        """Return standard status text representation for given code."""
        return STATUS_TEXT_MAP.get(int(status_code), "Unknown Status")

    def assert_status(
        self,
        status_code: int,
        expected: Any = 200,
        headers: Optional[Dict[str, str]] = None,
        required_headers: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """Validate response status against expected code, sequence, or category."""
        code = int(status_code)
        category = self.classify_status(code)
        status_text = self.get_status_text(code)
        errors: List[str] = []

        matched = False
        if isinstance(expected, int):
            matched = (code == expected)
            if not matched:
                errors.append(f"Expected status code {expected} but received {code}")
        elif isinstance(expected, (list, tuple, set)):
            matched = (code in expected)
            if not matched:
                errors.append(f"Status code {code} not in expected set {list(expected)}")
        elif isinstance(expected, str):
            exp_lower = expected.strip().lower()
            if exp_lower in ("ok", "success", "successful", "2xx"):
                matched = self.is_ok(code)
            elif exp_lower in ("redirect", "redirection", "3xx"):
                matched = self.is_redirect(code)
            elif exp_lower in ("client_error", "4xx"):
                matched = self.is_client_error(code)
            elif exp_lower in ("server_error", "5xx"):
                matched = self.is_server_error(code)
            elif exp_lower in ("error", "failed"):
                matched = self.is_error(code)
            elif exp_lower.isdigit():
                matched = (code == int(exp_lower))
            else:
                matched = (category == exp_lower)
            if not matched:
                errors.append(f"Status code {code} ({category}) failed to match expected rule '{expected}'")
        else:
            errors.append(f"Invalid expected specification type: {type(expected).__name__}")

        if required_headers and isinstance(required_headers, dict):
            hdrs = {k.lower(): v for k, v in (headers or {}).items()}
            for req_key, req_val in required_headers.items():
                key_l = req_key.lower()
                if key_l not in hdrs:
                    errors.append(f"Missing required header: {req_key}")
                elif req_val is not None and str(req_val).lower() not in hdrs[key_l].lower():
                    errors.append(f"Header '{req_key}' value '{hdrs[key_l]}' does not match expected pattern '{req_val}'")

        valid = matched and (len(errors) == 0)

        with self._lock:
            self._total_assertions += 1
            if valid:
                self._passed_assertions += 1
            else:
                self._failed_assertions += 1
            self._last_status = code
            self._last_category = category

        return {
            "valid": valid,
            "status": code,
            "status_text": status_text,
            "category": category,
            "expected": expected,
            "errors": errors,
        }

    def assert_response(
        self,
        response: Any,
        expected: Any = 200,
        required_headers: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """Validate live Playwright response or response mock against assertions."""
        if response is None:
            with self._lock:
                self._total_assertions += 1
                self._failed_assertions += 1
                self._last_status = 0
                self._last_category = "unknown"
            return {
                "valid": False,
                "status": 0,
                "status_text": "No Response",
                "category": "unknown",
                "expected": expected,
                "errors": ["compliance : not possible. Response represents None."],
            }

        status = getattr(response, "status", None)
        if callable(status):
            status = status()
        if status is None:
            status = 200

        headers = getattr(response, "headers", {})
        if callable(headers):
            headers = headers()

        return self.assert_status(
            status_code=int(status),
            expected=expected,
            headers=headers if isinstance(headers, dict) else {},
            required_headers=required_headers,
        )

    def get_metrics(self) -> Dict[str, Any]:
        """Return assertion telemetry counters."""
        with self._lock:
            return {
                "total_assertions": self._total_assertions,
                "passed_assertions": self._passed_assertions,
                "failed_assertions": self._failed_assertions,
                "last_status": self._last_status,
                "last_category": self._last_category,
            }

    def reset_metrics(self) -> None:
        """Reset telemetry counters preserving configuration."""
        self.reset()


_DEFAULT_STATUS_ASSERT = ResponseStatusAssert()


def get_default_status_assert() -> ResponseStatusAssert:
    """Return default singleton response status assertion engine."""
    return _DEFAULT_STATUS_ASSERT


def reset_status_assert() -> None:
    """Reset global response status assertion state."""
    _DEFAULT_STATUS_ASSERT.reset()


def create_status_assert() -> ResponseStatusAssert:
    """Instantiate a new dedicated response status assertion engine."""
    return ResponseStatusAssert()


class PlaywrightBrowserManager:
    """Managed Playwright browser lifecycle instance with automatic cleanup."""

    def __init__(self, headless: bool = True):
        self.headless = headless
        self._playwright: Optional[Playwright] = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        self._page: Optional[Page] = None
        self._console_capture = get_default_console_capture()
        self._network_latch = get_default_network_idle_latch()
        self._download_verifier = get_default_download_verifier()
        self._iframe_traversal = get_default_iframe_traversal()
        self._media_abort = get_default_media_abort()
        self._visibility_checker = get_default_visibility_checker()
        self._keyboard_controller = get_default_keyboard_controller()
        self._touch_gesture = get_default_touch_gesture()
        self._color_scheme = get_default_color_scheme_tester()
        self._status_assert = get_default_status_assert()

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
            self._console_capture.attach_to_page(self._page)
            self._network_latch.attach_to_page(self._page)
            self._download_verifier.attach_to_page(self._page)
            self._media_abort.attach_to_page(self._page)
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

    def get_console_logs(
        self,
        level: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Return captured browser console entries."""
        return self._console_capture.get_entries(level=level, limit=limit)

    def clear_console_logs(self) -> None:
        """Clear captured browser console entries."""
        self._console_capture.clear()

    def autofill_form(
        self,
        fields: Dict[str, Any],
        form_selector: Optional[str] = None,
        submit: bool = False,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """Populate multiple form controls and optionally submit form."""
        page = self._ensure_page()
        autofill = get_default_form_autofill()
        return autofill.fill_form(
            page=page,
            fields=fields,
            form_selector=form_selector,
            submit=submit,
            **kwargs,
        )

    def inspect_form(
        self,
        form_selector: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Inspect current page and discover interactive form elements."""
        page = self._ensure_page()
        autofill = get_default_form_autofill()
        return autofill.inspect_form(page=page, form_selector=form_selector)

    def wait_for_network_idle(
        self,
        idle_ms: float = 500.0,
        timeout_ms: float = 10000.0,
        max_inflight: int = 0,
    ) -> Dict[str, Any]:
        """Wait until browser network requests settle below idle threshold."""
        page = self._ensure_page()
        latch = get_default_network_idle_latch()
        return latch.wait_until_idle(
            page=page,
            idle_time_ms=idle_ms,
            timeout_ms=timeout_ms,
            max_inflight=max_inflight,
        )

    def get_network_metrics(self) -> Dict[str, Any]:
        """Return network idle latch telemetry counters."""
        return self._network_latch.get_metrics()

    def verify_download(
        self,
        file_path: str,
        expected_hash: Optional[str] = None,
        min_bytes: int = 1,
        max_bytes: Optional[int] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """Validate downloaded file against integrity constraints."""
        verifier = get_default_download_verifier()
        return verifier.verify_download(
            file_path=file_path,
            expected_hash=expected_hash,
            min_bytes=min_bytes,
            max_bytes=max_bytes,
            **kwargs,
        )

    def get_downloads(self) -> List[Dict[str, Any]]:
        """Return captured download records."""
        verifier = get_default_download_verifier()
        return verifier.get_downloads()

    def get_frame_tree(self) -> Dict[str, Any]:
        """Return hierarchical iframe tree of active page."""
        page = self._ensure_page()
        traversal = get_default_iframe_traversal()
        return traversal.get_frame_tree(page)

    def list_frames(self) -> List[Dict[str, Any]]:
        """Return flat list of all active frames."""
        page = self._ensure_page()
        traversal = get_default_iframe_traversal()
        return traversal.list_frames(page)

    def find_frame(
        self,
        name: Optional[str] = None,
        url_pattern: Optional[str] = None,
        selector: Optional[str] = None,
    ) -> Optional[Any]:
        """Locate target frame matching criteria."""
        page = self._ensure_page()
        traversal = get_default_iframe_traversal()
        return traversal.find_frame(page, name=name, url_pattern=url_pattern, selector=selector)

    def execute_in_frame(
        self,
        frame_target: Any,
        action: str,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """Execute interaction within target frame."""
        traversal = get_default_iframe_traversal()
        return traversal.execute_in_frame(frame_target, action, **kwargs)

    def inspect_iframes(
        self,
        html_content: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Inspect and discover iframe elements in page or HTML."""
        traversal = get_default_iframe_traversal()
        if html_content is not None:
            return traversal.inspect_html(html_content)
        page = self._ensure_page()
        content = page.content() if hasattr(page, "content") else ""
        return traversal.inspect_html(content)

    def enable_media_abort(self) -> None:
        """Enable media abort filter on active session."""
        self._media_abort.enable()

    def disable_media_abort(self) -> None:
        """Disable media abort filter on active session."""
        self._media_abort.disable()

    def get_media_abort_metrics(self) -> Dict[str, Any]:
        """Return media abort telemetry metrics."""
        return self._media_abort.get_metrics()

    def get_aborted_media_urls(self) -> List[str]:
        """Return list of aborted media URLs."""
        return self._media_abort.get_aborted_urls()

    def check_visibility(
        self,
        selector: str,
        check_viewport: bool = True,
        check_occlusion: bool = False,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """Check live element visibility and layout properties."""
        page = self._ensure_page()
        checker = get_default_visibility_checker()
        return checker.check_visibility(
            page=page,
            selector=selector,
            check_viewport=check_viewport,
            check_occlusion=check_occlusion,
            **kwargs,
        )

    def wait_for_visibility(
        self,
        selector: str,
        visible: bool = True,
        timeout_ms: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Wait until element matches requested visibility state."""
        page = self._ensure_page()
        checker = get_default_visibility_checker()
        return checker.wait_for_visibility(
            page=page,
            selector=selector,
            visible=visible,
            timeout_ms=timeout_ms,
        )

    def inspect_element_visibility(
        self,
        selector: str,
        html_content: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Inspect element visibility in page or HTML string."""
        checker = get_default_visibility_checker()
        if html_content is not None:
            return checker.inspect_html(html_content, selector)
        page = self._ensure_page()
        content = page.content() if hasattr(page, "content") else ""
        return checker.inspect_html(content, selector)

    def press_key(
        self,
        key_or_chord: str,
        delay_ms: float = 0.0,
    ) -> Dict[str, Any]:
        """Dispatch key press or chord shortcut to active page."""
        page = self._ensure_page()
        ctrl = get_default_keyboard_controller()
        return ctrl.press(page, key_or_chord, delay_ms=delay_ms)

    def type_keyboard(
        self,
        text: str,
        delay_ms: float = 0.0,
    ) -> Dict[str, Any]:
        """Type character string into page via keyboard simulation."""
        page = self._ensure_page()
        ctrl = get_default_keyboard_controller()
        return ctrl.type_text(page, text, delay_ms=delay_ms)

    def send_key_sequence(
        self,
        sequence: Sequence[str],
        delay_ms: float = 0.0,
    ) -> Dict[str, Any]:
        """Dispatch sequence of keyboard actions sequentially."""
        page = self._ensure_page()
        ctrl = get_default_keyboard_controller()
        return ctrl.send_sequence(page, sequence, delay_ms=delay_ms)

    def get_keyboard_metrics(self) -> Dict[str, Any]:
        """Return keyboard controller telemetry counters."""
        ctrl = get_default_keyboard_controller()
        return ctrl.get_metrics()

    def emulate_color_scheme(self, scheme: str = "dark") -> Dict[str, Any]:
        """Emulate preferred color scheme on active page."""
        page = self._ensure_page()
        tester = get_default_color_scheme_tester()
        return tester.emulate(page, scheme=scheme)

    def inspect_dark_mode(self, content: Optional[str] = None) -> Dict[str, Any]:
        """Inspect dark mode rules and media queries in page or CSS."""
        tester = get_default_color_scheme_tester()
        if content is not None:
            return tester.inspect_css(content)
        page = self._ensure_page()
        html_markup = page.content() if hasattr(page, "content") else ""
        return tester.inspect_css(html_markup)

    def get_color_scheme_metrics(self) -> Dict[str, Any]:
        """Return color scheme tester telemetry metrics."""
        tester = get_default_color_scheme_tester()
        return tester.get_metrics()

    def assert_response_status(
        self,
        expected: Any = 200,
        response_or_status: Optional[Any] = None,
        required_headers: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """Validate status of response or status code."""
        asserter = get_default_status_assert()
        if isinstance(response_or_status, int):
            return asserter.assert_status(
                status_code=response_or_status,
                expected=expected,
                required_headers=required_headers,
            )
        if response_or_status is not None:
            return asserter.assert_response(
                response=response_or_status,
                expected=expected,
                required_headers=required_headers,
            )
        page = self._ensure_page()
        res_obj = getattr(page, "response", None)
        if callable(res_obj):
            res_obj = res_obj()
        if res_obj is not None:
            return asserter.assert_response(
                response=res_obj,
                expected=expected,
                required_headers=required_headers,
            )
        return asserter.assert_status(
            status_code=200,
            expected=expected,
            required_headers=required_headers,
        )

    def get_status_metrics(self) -> Dict[str, Any]:
        """Return response status assertion telemetry metrics."""
        asserter = get_default_status_assert()
        return asserter.get_metrics()

    def tap_touch(
        self,
        x: float = 0.0,
        y: float = 0.0,
        selector: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Dispatch touchscreen tap to coordinates or element target."""
        page = self._ensure_page()
        ctrl = get_default_touch_gesture()
        return ctrl.tap(page, x=x, y=y, selector=selector)

    def swipe_touch(
        self,
        start_pos: Tuple[float, float],
        end_pos: Tuple[float, float],
        steps: int = 5,
    ) -> Dict[str, Any]:
        """Dispatch touchscreen swipe motion along coordinate trajectory."""
        page = self._ensure_page()
        ctrl = get_default_touch_gesture()
        return ctrl.swipe(page, start_pos=start_pos, end_pos=end_pos, steps=steps)

    def get_touch_metrics(self) -> Dict[str, Any]:
        """Return touch gesture telemetry metrics."""
        ctrl = get_default_touch_gesture()
        return ctrl.get_metrics()

    def emulate_color_scheme(self, scheme: str = "dark") -> Dict[str, Any]:
        """Emulate preferred color scheme on active page."""
        page = self._ensure_page()
        tester = get_default_color_scheme_tester()
        return tester.emulate(page, scheme=scheme)

    def inspect_dark_mode(self, content: Optional[str] = None) -> Dict[str, Any]:
        """Inspect dark mode rules and media queries in page or CSS."""
        tester = get_default_color_scheme_tester()
        if content is not None:
            return tester.inspect_css(content)
        page = self._ensure_page()
        html_markup = page.content() if hasattr(page, "content") else ""
        return tester.inspect_css(html_markup)

    def get_color_scheme_metrics(self) -> Dict[str, Any]:
        """Return color scheme tester telemetry metrics."""
        tester = get_default_color_scheme_tester()
        return tester.get_metrics()

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
    **kwargs: Any,
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
        elif act in ("wait_for_mutation", "mutation_wait", "wait_mutation"):
            t_ms = kwargs.get("timeout_ms", timeout_ms)
            res = session.wait_for_mutation(
                selector=selector,
                timeout_ms=t_ms,
                **kwargs,
            )
            return {"isError": res.get("isError", False), "result": res}
        elif act in ("wait_network_idle", "network_idle", "wait_for_network"):
            idle_ms = kwargs.get("idle_ms", 500.0)
            max_inf = kwargs.get("max_inflight", 0)
            res = session.wait_for_network_idle(
                idle_ms=idle_ms,
                timeout_ms=timeout_ms,
                max_inflight=max_inf,
            )
            return {"isError": res.get("isError", False) or not res.get("is_idle", True), "result": res}
        elif act in ("verify_download", "download_verify"):
            if not path:
                return {"isError": True, "error": "Path parameter required for verify_download"}
            exp_h = kwargs.get("expected_hash")
            min_b = kwargs.get("min_bytes", 1)
            max_b = kwargs.get("max_bytes")
            res = session.verify_download(
                file_path=path,
                expected_hash=exp_h,
                min_bytes=min_b,
                max_bytes=max_b,
                **kwargs,
            )
            return {"isError": not res.get("valid", False), "result": res}
        elif act in ("get_downloads", "downloads"):
            records = session.get_downloads()
            return {"isError": False, "result": records, "count": len(records)}
        elif act in ("console_logs", "get_console_logs", "read_console", "console"):
            lvl = kwargs.get("level")
            lim = kwargs.get("limit")
            logs = session.get_console_logs(level=lvl, limit=lim)
            return {"isError": False, "result": logs, "count": len(logs)}
        elif act in ("clear_console", "flush_console"):
            session.clear_console_logs()
            return {"isError": False, "result": "Console log buffer cleared."}
        elif act in ("autofill", "autofill_form", "form_fill", "fill_form"):
            fields_data = kwargs.get("fields")
            if not fields_data and text:
                try:
                    fields_data = json.loads(text)
                except Exception:
                    fields_data = {}
            if not isinstance(fields_data, dict):
                return {"isError": True, "error": "Dictionary fields mapping required for autofill"}
            sub = kwargs.get("submit", False)
            res = session.autofill_form(
                fields=fields_data,
                form_selector=selector,
                submit=sub,
                **kwargs,
            )
            return {"isError": res.get("isError", False), "result": res}
        elif act in ("inspect_form", "get_form_fields", "form_fields"):
            res = session.inspect_form(form_selector=selector)
            return {"isError": res.get("isError", False), "result": res}
        elif act in ("list_frames", "frames", "get_frames"):
            frame_list = session.list_frames()
            return {"isError": False, "result": frame_list, "count": len(frame_list)}
        elif act in ("frame_tree", "get_frame_tree"):
            tree = session.get_frame_tree()
            return {"isError": False, "result": tree}
        elif act in ("inspect_iframes", "inspect_frames"):
            res = session.inspect_iframes(html_content=text)
            return {"isError": False, "result": res, "count": len(res)}
        elif act in ("execute_in_frame", "frame_action", "in_frame"):
            f_name = kwargs.get("frame_name") or kwargs.get("name")
            f_url = kwargs.get("frame_url") or kwargs.get("url")
            f_sel = kwargs.get("frame_selector") or selector
            target = session.find_frame(name=f_name, url_pattern=f_url, selector=f_sel)
            if target is None:
                return {
                    "isError": True,
                    "error": f"Target iframe not located by name={f_name}, url={f_url}, selector={f_sel}",
                }
            sub_act = kwargs.get("frame_action") or kwargs.get("sub_action") or "extract_content"
            res = session.execute_in_frame(target, sub_act, **kwargs)
            return {"isError": not res.get("valid", False), "result": res}
        elif act in ("enable_media_abort", "enable_media_block"):
            session.enable_media_abort()
            return {"isError": False, "result": "Media abort filter enabled."}
        elif act in ("disable_media_abort", "disable_media_block"):
            session.disable_media_abort()
            return {"isError": False, "result": "Media abort filter disabled."}
        elif act in ("media_abort_metrics", "media_metrics"):
            m = session.get_media_abort_metrics()
            return {"isError": False, "result": m}
        elif act in ("aborted_media", "aborted_urls"):
            urls = session.get_aborted_media_urls()
            return {"isError": False, "result": urls, "count": len(urls)}
        elif act in ("check_visibility", "is_visible", "visibility"):
            if not selector:
                return {"isError": True, "error": "Selector required for visibility check"}
            res = session.check_visibility(selector=selector, **kwargs)
            return {"isError": res.get("isError", False) or not res.get("exists", False), "result": res}
        elif act in ("wait_visibility", "wait_for_visible", "wait_for_hidden"):
            if not selector:
                return {"isError": True, "error": "Selector required for wait visibility action"}
            vis_target = not ("hidden" in act)
            res = session.wait_for_visibility(selector=selector, visible=vis_target, **kwargs)
            return {"isError": not res.get("success", False), "result": res}
        elif act in ("inspect_visibility", "inspect_element_visibility"):
            if not selector:
                return {"isError": True, "error": "Selector required for inspect visibility action"}
            res = session.inspect_element_visibility(selector=selector, html_content=text)
            return {"isError": not res.get("exists", False), "result": res}
        elif act in ("press_key", "key_press", "keyboard_press", "press"):
            key_name = kwargs.get("key") or text or selector
            if not key_name:
                return {"isError": True, "error": "Key or chord parameter required for press action"}
            del_ms = kwargs.get("delay_ms", 0.0)
            res = session.press_key(key_or_chord=key_name, delay_ms=del_ms)
            return {"isError": not res.get("success", False), "result": res}
        elif act in ("type_keyboard", "keyboard_type"):
            text_val = text or kwargs.get("text", "")
            del_ms = kwargs.get("delay_ms", 0.0)
            res = session.type_keyboard(text=text_val, delay_ms=del_ms)
            return {"isError": not res.get("success", False), "result": res}
        elif act in ("key_sequence", "keyboard_sequence"):
            seq = kwargs.get("sequence") or []
            if not seq and text:
                seq = [s.strip() for s in text.split(",")]
            del_ms = kwargs.get("delay_ms", 0.0)
            res = session.send_key_sequence(sequence=seq, delay_ms=del_ms)
            return {"isError": not res.get("success", False), "result": res}
        elif act in ("keyboard_metrics", "keyboard_telemetry"):
            m = session.get_keyboard_metrics()
            return {"isError": False, "result": m}
        elif act in ("tap_touch", "touch_tap", "tap"):
            tx = float(kwargs.get("x", 0.0))
            ty = float(kwargs.get("y", 0.0))
            res = session.tap_touch(x=tx, y=ty, selector=selector)
            return {"isError": not res.get("success", False), "result": res}
        elif act in ("swipe_touch", "touch_swipe", "swipe"):
            start = kwargs.get("start") or (float(kwargs.get("start_x", 0.0)), float(kwargs.get("start_y", 0.0)))
            end = kwargs.get("end") or (float(kwargs.get("end_x", 100.0)), float(kwargs.get("end_y", 100.0)))
            stp = int(kwargs.get("steps", 5))
            res = session.swipe_touch(start_pos=start, end_pos=end, steps=stp)
            return {"isError": not res.get("success", False), "result": res}
        elif act in ("touch_metrics", "touch_telemetry"):
            m = session.get_touch_metrics()
            return {"isError": False, "result": m}
        elif act in ("emulate_color_scheme", "dark_mode", "color_scheme"):
            sch = text or kwargs.get("scheme") or "dark"
            res = session.emulate_color_scheme(scheme=sch)
            return {"isError": not res.get("success", False), "result": res}
        elif act in ("inspect_dark_mode", "check_dark_mode"):
            res = session.inspect_dark_mode(content=text)
            return {"isError": False, "result": res}
        elif act in ("color_scheme_metrics", "dark_mode_metrics"):
            m = session.get_color_scheme_metrics()
            return {"isError": False, "result": m}
        elif act in ("assert_status", "status_assert", "assert_response"):
            exp_val = kwargs.get("expected", 200)
            if not exp_val and text:
                try:
                    exp_val = int(text)
                except Exception:
                    exp_val = text
            req_h = kwargs.get("required_headers") or kwargs.get("headers")
            res = session.assert_response_status(
                expected=exp_val,
                response_or_status=kwargs.get("response") or kwargs.get("status_code"),
                required_headers=req_h,
            )
            return {"isError": not res.get("valid", False), "result": res}
        elif act in ("classify_status", "status_category"):
            code_val = int(kwargs.get("status_code") or text or 200)
            asserter = get_default_status_assert()
            cat = asserter.classify_status(code_val)
            txt = asserter.get_status_text(code_val)
            return {"isError": False, "status": code_val, "category": cat, "status_text": txt}
        elif act in ("status_metrics", "status_telemetry"):
            m = session.get_status_metrics()
            return {"isError": False, "result": m}
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
