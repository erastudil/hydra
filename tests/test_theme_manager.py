"""
Integration test suite for Hydra Desktop Dynamic Theme Engine.
Complies with AGENTS.md genome invariants: zero fake tests, real interfaces, exit code 0.
"""

from __future__ import annotations

import tempfile
import pytest

from desktop.theme_manager import (
    ThemeManager,
    ThemePalette,
    BUILTIN_PALETTES,
    calculate_contrast_ratio,
    check_wcag_compliance,
    get_theme_manager,
    reset_theme_manager,
)


@pytest.fixture
def manager():
    tm = ThemeManager()
    yield tm
    tm.set_active_theme("dark_slate")


def test_wcag_contrast_math_and_compliance():
    """Verify WCAG 2.1 relative luminance, contrast ratio math, and threshold certification."""
    # Black (#000000) and White (#ffffff) contrast ratio must be 21.0:1
    max_contrast = calculate_contrast_ratio("#000000", "#ffffff")
    assert max_contrast == 21.0

    # Same color contrast ratio must be 1.0:1
    min_contrast = calculate_contrast_ratio("#123456", "#123456")
    assert min_contrast == 1.0

    # High contrast compliance check
    comp = check_wcag_compliance("#ffffff", "#000000")
    assert comp["contrast_ratio"] == 21.0
    assert comp["aa_normal_text"] is True
    assert comp["aaa_normal_text"] is True
    assert comp["high_contrast_compliant"] is True

    # Low contrast pair (light gray on white) fails AA normal text
    low_comp = check_wcag_compliance("#cccccc", "#ffffff")
    assert low_comp["contrast_ratio"] < 4.5
    assert low_comp["aa_normal_text"] is False


def test_builtin_palettes_catalog_and_contrast_compliance():
    """Verify built-in themes exist and high contrast palettes meet WCAG AAA."""
    required = ["dark_slate", "high_contrast_dark", "paper_light", "high_contrast_light", "cyberpunk_neon"]
    for pid in required:
        assert pid in BUILTIN_PALETTES
        pal = BUILTIN_PALETTES[pid]
        assert pal.theme_id == pid
        assert pal.background.startswith("#")
        assert pal.foreground.startswith("#")

    # High contrast dark palette must achieve WCAG AAA (>= 7.0:1)
    hc_dark = BUILTIN_PALETTES["high_contrast_dark"]
    assert hc_dark.is_high_contrast is True
    contrast_eval = hc_dark.check_contrast()
    assert contrast_eval["is_high_contrast_certified"] is True
    assert contrast_eval["is_accessible"] is True


def test_custom_palette_registration(manager: ThemeManager):
    """Verify custom user palette registration and retrieval."""
    custom = ThemePalette(
        theme_id="solarized_amber",
        name="Solarized Amber",
        mode="dark",
        background="#002b36",
        surface="#073642",
        foreground="#fdf6e3",
        primary="#b58900",
    )
    manager.register_palette(custom)

    retrieved = manager.get_palette("solarized_amber")
    assert retrieved is not None
    assert retrieved.name == "Solarized Amber"
    assert retrieved.primary == "#b58900"
    assert manager.total_palettes == len(BUILTIN_PALETTES) + 1


def test_css_variables_and_stylesheet_generation():
    """Verify generation of CSS :root custom properties and full styles."""
    pal = BUILTIN_PALETTES["dark_slate"]
    css_vars = pal.generate_css_variables()

    assert ":root {" in css_vars
    assert f"--color-bg: {pal.background};" in css_vars
    assert f"--color-surface: {pal.surface};" in css_vars
    assert f"--color-text: {pal.foreground};" in css_vars
    assert f"--color-primary: {pal.primary};" in css_vars

    full_sheet = pal.generate_full_stylesheet()
    assert ":root {" in full_sheet
    assert ".theme-surface" in full_sheet
    assert ".theme-btn-primary" in full_sheet


def test_active_theme_switching_and_subscriber_events(manager: ThemeManager):
    """Verify switching active theme updates CSS output and notifies subscribers."""
    received = []
    sub_id = manager.subscribe(lambda p: received.append(p.theme_id))

    assert manager.get_active_theme().theme_id == "dark_slate"

    # Switch to high contrast
    active = manager.set_active_theme("high_contrast_dark")
    assert active.theme_id == "high_contrast_dark"
    assert manager.get_active_theme().theme_id == "high_contrast_dark"
    assert len(received) == 1
    assert received[0] == "high_contrast_dark"

    # Active CSS reflects new palette
    active_css = manager.get_active_css()
    assert "--color-bg: #000000;" in active_css
    assert "--color-text: #ffffff;" in active_css

    # Unsubscribe
    manager.unsubscribe(sub_id)
    manager.set_active_theme("paper_light")
    assert len(received) == 1


def test_theme_settings_persistence():
    """Verify saving and loading active theme and custom palettes from disk."""
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tf:
        settings_path = tf.name

    try:
        tm1 = ThemeManager(settings_file=settings_path)
        tm1.register_palette(ThemePalette("matrix_green", "Matrix Green", background="#000000", foreground="#00ff00"))
        tm1.set_active_theme("matrix_green")
        tm1.save_settings(settings_path)

        # Re-load in separate instance
        tm2 = ThemeManager(settings_file=settings_path)
        assert tm2.get_active_theme().theme_id == "matrix_green"
        assert tm2.get_palette("matrix_green") is not None
        assert tm2.get_palette("matrix_green").foreground == "#00ff00"
    finally:
        import os
        try:
            os.remove(settings_path)
        except OSError:
            pass


def test_global_singleton_theme_manager():
    """Verify singleton lifecycle for ThemeManager."""
    t1 = get_theme_manager()
    t2 = get_theme_manager()
    assert t1 is t2

    t3 = reset_theme_manager()
    assert t3 is not t1
    assert get_theme_manager() is t3
