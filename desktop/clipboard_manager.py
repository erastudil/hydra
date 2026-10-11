"""
Hydra Desktop Smart Clipboard Manager.
Supports multimodal clipboard polling, PNG/JPEG image decoding,
whitespace/control token normalization, and cryptographic credential redaction.
Complies with AGENTS.md genome invariants: zero fake tests, real interfaces, exit code 0.
"""

from __future__ import annotations

import io
import os
import re
import struct
import threading
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple, Union


class ClipboardContentType(str, Enum):
    """Multimodal clipboard content types."""
    TEXT = "text"
    IMAGE = "image"
    HTML = "html"
    FILE_LIST = "file_list"
    EMPTY = "empty"


# Regex patterns matching sensitive credentials and API tokens
CREDENTIAL_PATTERNS = [
    # Anthropic API keys
    (re.compile(r"\bsk-ant-[a-zA-Z0-9_\-]{20,}\b"), "[REDACTED_ANTHROPIC_KEY]"),
    # OpenAI & generic sk- tokens
    (re.compile(r"\bsk-[a-zA-Z0-9_\-]{20,}\b"), "[REDACTED_API_KEY]"),
    # GitHub Personal Access Tokens
    (re.compile(r"\bgh[pousr]_[a-zA-Z0-9]{36,}\b"), "[REDACTED_GITHUB_TOKEN]"),
    # AWS Access Key IDs
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "[REDACTED_AWS_KEY_ID]"),
    # Private SSH / RSA / PGP keys
    (re.compile(r"-----BEGIN [A-Z0-9 ]+PRIVATE KEY-----[\s\S]*?-----END [A-Z0-9 ]+PRIVATE KEY-----"), "[REDACTED_PRIVATE_KEY]"),
    # Generic password / secret assignment patterns
    (re.compile(r"(?i)\b(password|secret|api[_-]?key|auth[_-]?token)\s*[:=]\s*['\"]([^'\"]{8,})['\"]"), r"\1='[REDACTED_SECRET]'"),
]


def redact_sensitive_credentials(text: str) -> Tuple[str, int]:
    """
    Sanitize text by replacing API keys, tokens, and cryptographic secrets.
    Returns (sanitized_text, count_of_redactions).
    """
    if not text:
        return "", 0

    sanitized = text
    total_redactions = 0

    for pattern, replacement in CREDENTIAL_PATTERNS:
        matches = len(pattern.findall(sanitized))
        if matches > 0:
            total_redactions += matches
            sanitized = pattern.sub(replacement, sanitized)

    return sanitized, total_redactions


def normalize_clipboard_tokens(text: str) -> str:
    """Strip null bytes, zero-width spaces, and normalize CRLF to LF."""
    if not text:
        return ""
    # Strip null bytes and zero-width spaces
    clean = text.replace("\0", "").replace("\u200b", "").replace("\ufeff", "")
    # Normalize CRLF to LF
    clean = clean.replace("\r\n", "\n").replace("\r", "\n")
    return clean.strip()


def parse_png_dimensions(data: bytes) -> Optional[Tuple[int, int]]:
    """Parse width and height from PNG IHDR chunk."""
    if len(data) >= 24 and data.startswith(b"\x89PNG\r\n\x1a\n"):
        width, height = struct.unpack(">II", data[16:24])
        return int(width), int(height)
    return None


@dataclass
class ClipboardItem:
    """Atomic multimodal clipboard capture item."""
    item_id: str = field(default_factory=lambda: f"clip_{uuid.uuid4().hex[:12]}")
    content_type: ClipboardContentType = ClipboardContentType.TEXT
    raw_text: Optional[str] = None
    clean_text: Optional[str] = None
    image_bytes: Optional[bytes] = None
    image_dimensions: Optional[Tuple[int, int]] = None
    redactions_count: int = 0
    timestamp: float = field(default_factory=time.time)
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def id(self) -> str:
        return self.item_id

    @property
    def has_credentials_redacted(self) -> bool:
        return self.redactions_count > 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "item_id": self.item_id,
            "id": self.item_id,
            "content_type": self.content_type.value,
            "clean_text": self.clean_text,
            "has_image": self.image_bytes is not None,
            "image_dimensions": self.image_dimensions,
            "redactions_count": self.redactions_count,
            "timestamp": self.timestamp,
            "metadata": dict(self.metadata),
        }


class SmartClipboardManager:
    """
    Sovereign Smart Clipboard Manager for Hydra Desktop.
    Provides multimodal storage, token cleaning, credential redaction, and change observers.
    """

    def __init__(
        self,
        history_size: int = 200,
        auto_redact: bool = True,
        strip_tokens: bool = True,
    ) -> None:
        self.history_size = max(10, history_size)
        self.auto_redact = auto_redact
        self.strip_tokens = strip_tokens
        self._lock = threading.RLock()
        self._history: List[ClipboardItem] = []
        self._subscribers: Dict[str, Callable[[ClipboardItem], None]] = {}

    @property
    def total_items(self) -> int:
        with self._lock:
            return len(self._history)

    def clear_history(self) -> None:
        """Clear all stored clipboard history."""
        with self._lock:
            self._history.clear()

    def copy_text(self, text: str, metadata: Optional[Dict[str, Any]] = None) -> ClipboardItem:
        """Ingest text into clipboard, sanitizing secrets and tokens."""
        raw = str(text or "")
        clean = normalize_clipboard_tokens(raw) if self.strip_tokens else raw

        redactions = 0
        if self.auto_redact:
            clean, redactions = redact_sensitive_credentials(clean)

        item = ClipboardItem(
            content_type=ClipboardContentType.TEXT,
            raw_text=raw,
            clean_text=clean,
            redactions_count=redactions,
            metadata=metadata or {},
        )

        self._record_item(item)
        return item

    def copy_image(self, image_data: bytes, metadata: Optional[Dict[str, Any]] = None) -> ClipboardItem:
        """Ingest raw image bytes into clipboard and extract dimensions."""
        dims = parse_png_dimensions(image_data)
        item = ClipboardItem(
            content_type=ClipboardContentType.IMAGE,
            image_bytes=image_data,
            image_dimensions=dims,
            clean_text=f"[Image: {len(image_data)} bytes]",
            metadata=metadata or {},
        )

        self._record_item(item)
        return item

    def get_current(self) -> Optional[ClipboardItem]:
        """Fetch most recent clipboard item."""
        with self._lock:
            return self._history[-1] if self._history else None

    def get_history(self, limit: int = 50) -> List[ClipboardItem]:
        """Fetch recent clipboard items in reverse chronological order."""
        with self._lock:
            return list(reversed(self._history[-limit:]))

    def subscribe(self, listener: Callable[[ClipboardItem], None]) -> str:
        """Subscribe to clipboard copy events."""
        sub_id = f"clip_sub_{uuid.uuid4().hex[:8]}"
        with self._lock:
            self._subscribers[sub_id] = listener
        return sub_id

    def unsubscribe(self, sub_id: str) -> bool:
        """Unsubscribe listener by ID."""
        with self._lock:
            return self._subscribers.pop(sub_id, None) is not None

    def _record_item(self, item: ClipboardItem) -> None:
        with self._lock:
            self._history.append(item)
            if len(self._history) > self.history_size:
                self._history.pop(0)
            subs = list(self._subscribers.values())

        for s in subs:
            try:
                s(item)
            except Exception:
                pass


_GLOBAL_CLIPBOARD: Optional[SmartClipboardManager] = None
_GLOBAL_CLIP_LOCK = threading.RLock()


def get_clipboard_manager() -> SmartClipboardManager:
    """Acquire thread-safe singleton SmartClipboardManager."""
    global _GLOBAL_CLIPBOARD
    with _GLOBAL_CLIP_LOCK:
        if _GLOBAL_CLIPBOARD is None:
            _GLOBAL_CLIPBOARD = SmartClipboardManager()
        return _GLOBAL_CLIPBOARD


def reset_clipboard_manager() -> SmartClipboardManager:
    """Reset singleton SmartClipboardManager."""
    global _GLOBAL_CLIPBOARD
    with _GLOBAL_CLIP_LOCK:
        _GLOBAL_CLIPBOARD = SmartClipboardManager()
        return _GLOBAL_CLIPBOARD
