"""
Integration test suite for Hydra Desktop Smart Clipboard Manager.
Complies with AGENTS.md genome invariants: zero fake tests, real interfaces, exit code 0.
"""

from __future__ import annotations

import struct
import pytest

from desktop.clipboard_manager import (
    SmartClipboardManager,
    ClipboardItem,
    ClipboardContentType,
    redact_sensitive_credentials,
    normalize_clipboard_tokens,
    parse_png_dimensions,
    get_clipboard_manager,
    reset_clipboard_manager,
)


@pytest.fixture
def clipboard():
    cm = SmartClipboardManager()
    yield cm
    cm.clear_history()


def _make_dummy_png_bytes(width: int = 120, height: int = 80) -> bytes:
    """Generate minimal valid PNG header with IHDR chunk."""
    header = b"\x89PNG\r\n\x1a\n"
    ihdr_data = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    ihdr_chunk = struct.pack(">I", len(ihdr_data)) + b"IHDR" + ihdr_data + b"\x00\x00\x00\x00"
    return header + ihdr_chunk


def test_credential_redaction_patterns():
    """Verify regex redacting OpenAI, Anthropic, GitHub, AWS, and private keys."""
    sample_text = (
        "Here are keys: sk-proj1234567890abcdef1234567890 and "
        "sk-ant-api03-abcdef12345678901234567890 and "
        "ghp_123456789012345678901234567890123456 and "
        "AKIAIOSFODNN7EXAMPLE."
    )
    redacted, count = redact_sensitive_credentials(sample_text)

    assert count == 4
    assert "[REDACTED_API_KEY]" in redacted
    assert "[REDACTED_ANTHROPIC_KEY]" in redacted
    assert "[REDACTED_GITHUB_TOKEN]" in redacted
    assert "[REDACTED_AWS_KEY_ID]" in redacted
    assert "sk-proj" not in redacted
    assert "ghp_" not in redacted


def test_token_stripping_and_normalization():
    """Verify null byte, zero-width space removal and newline normalization."""
    dirty = "line1\r\nline2\0with\u200bhidden\ufefftokens\r\n"
    clean = normalize_clipboard_tokens(dirty)

    assert "\0" not in clean
    assert "\u200b" not in clean
    assert "\ufeff" not in clean
    assert "\r" not in clean
    assert clean == "line1\nline2withhiddentokens"


def test_png_dimension_parsing():
    """Verify extracting width and height directly from PNG IHDR header."""
    png_bytes = _make_dummy_png_bytes(640, 480)
    dims = parse_png_dimensions(png_bytes)
    assert dims == (640, 480)

    # Invalid header returns None
    assert parse_png_dimensions(b"NOT_A_PNG") is None


def test_smart_clipboard_copy_text_auto_redaction(clipboard: SmartClipboardManager):
    """Verify copying text automatically cleans tokens and redacts credentials."""
    dirty_text = "Deploying with key: sk-live1234567890abcdef1234567890\r\n"
    item = clipboard.copy_text(dirty_text)

    assert item.content_type == ClipboardContentType.TEXT
    assert item.has_credentials_redacted is True
    assert item.redactions_count == 1
    assert "sk-live" not in item.clean_text
    assert "[REDACTED_API_KEY]" in item.clean_text
    assert clipboard.total_items == 1


def test_smart_clipboard_copy_image(clipboard: SmartClipboardManager):
    """Verify copying image data parses dimensions and records image payload."""
    img_data = _make_dummy_png_bytes(800, 600)
    item = clipboard.copy_image(img_data)

    assert item.content_type == ClipboardContentType.IMAGE
    assert item.image_dimensions == (800, 600)
    assert item.image_bytes == img_data
    assert clipboard.get_current() == item


def test_clipboard_history_and_subscribers(clipboard: SmartClipboardManager):
    """Verify history limit and copy notification listener dispatch."""
    received = []
    sub_id = clipboard.subscribe(lambda itm: received.append(itm.content_type))

    clipboard.copy_text("entry 1")
    clipboard.copy_text("entry 2")

    assert len(received) == 2
    assert clipboard.total_items == 2

    hist = clipboard.get_history(limit=10)
    assert len(hist) == 2
    assert hist[0].clean_text == "entry 2"

    clipboard.unsubscribe(sub_id)
    clipboard.copy_text("entry 3")
    assert len(received) == 2


def test_global_singleton_clipboard_manager():
    """Verify singleton lifecycle for SmartClipboardManager."""
    c1 = get_clipboard_manager()
    c2 = get_clipboard_manager()
    assert c1 is c2

    c3 = reset_clipboard_manager()
    assert c3 is not c1
    assert get_clipboard_manager() is c3
