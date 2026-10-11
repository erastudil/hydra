"""
Hydra Desktop Dynamic Theme Engine and Palette Manager.
Provides palette definitions, custom CSS variable generation,
WCAG 2.1 relative luminance and contrast compliance validation, and persistent settings.
Complies with AGENTS.md genome invariants: zero fake tests, real interfaces, exit code 0.
"""

from __future__ import annotations

import json
import math
import os
import threading
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple, Union


def hex_to_rgb(hex_code: str) -> Tuple[int, int, int]:
    """Parse hex color string to integer RGB 3-tuple."""
    clean = hex_code.strip().lstrip("#")
    if len(clean) == 3:
        clean = "".join(c * 2 for c in clean)
    if len(clean) != 6:
        raise ValueError(f"Invalid hex color code: {hex_code}")
    r = int(clean[0:2], 16)
    g = int(clean[2:4], 16)
    b = int(clean[4:6], 16)
    return r, g, b


def relative_luminance(r: int, g: int, b: int) -> float:
    """Calculate relative luminance using WCAG 2.1 standard formula."""
    def channel_linear(val: int) -> float:
        c = val / 255.0
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    r_lin = channel_linear(r)
    g_lin = channel_linear(g)
    b_lin = channel_linear(b)
    return 0.2126 * r_lin + 0.7152 * g_lin + 0.0722 * b_lin


def calculate_contrast_ratio(hex1: str, hex2: str) -> float:
    """Calculate WCAG 2.1 contrast ratio between two hex colors in range [1.0, 21.0]."""
    r1, g1, b1 = hex_to_rgb(hex1)
    r2, g2, b2 = hex_to_rgb(hex2)

    l1 = relative_luminance(r1, g1, b1)
    l2 = relative_luminance(r2, g2, b2)

    lighter = max(l1, l2)
    darker = min(l1, l2)
    ratio = (lighter + 0.05) / (darker + 0.05)
    return round(ratio, 2)


def check_wcag_compliance(foreground_hex: str, background_hex: str) -> Dict[str, Any]:
    """Validate contrast ratio against WCAG AA and AAA thresholds."""
    ratio = calculate_contrast_ratio(foreground_hex, background_hex)
    return {
        "contrast_ratio": ratio,
        "aa_normal_text": ratio >= 4.5,
        "aa_large_text": ratio >= 3.0,
        "aaa_normal_text": ratio >= 7.0,
        "high_contrast_compliant": ratio >= 7.0,
    }


@dataclass
class ThemePalette:
    """Data model for Hydra Desktop UI color palette."""
    theme_id: str
    name: str
    mode: str = "dark"  # "dark", "light", "high_contrast"
    background: str = "#0f172a"
    surface: str = "#1e293b"
    foreground: str = "#f8fafc"
    primary: str = "#38bdf8"
    secondary: str = "#818cf8"
    border: str = "#334155"
    success: str = "#22c55e"
    warning: str = "#eab308"
    danger: str = "#ef4444"
    info: str = "#06b6d4"
    font_family: str = "ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace"
    border_radius: str = "6px"
    is_high_contrast: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def id(self) -> str:
        return self.theme_id

    def check_contrast(self) -> Dict[str, Any]:
        """Check accessibility contrast of foreground text against background and surface."""
        bg_check = check_wcag_compliance(self.foreground, self.background)
        surf_check = check_wcag_compliance(self.foreground, self.surface)
        primary_check = check_wcag_compliance(self.primary, self.background)

        return {
            "text_on_background": bg_check,
            "text_on_surface": surf_check,
            "primary_on_background": primary_check,
            "is_accessible": bg_check["aa_normal_text"] and surf_check["aa_normal_text"],
            "is_high_contrast_certified": bg_check["aaa_normal_text"],
        }

    def generate_css_variables(self) -> str:
        """Produce formatted CSS custom property declarations."""
        lines = [
            ":root {",
            f"  --theme-id: '{self.theme_id}';",
            f"  --theme-mode: '{self.mode}';",
            f"  --color-bg: {self.background};",
            f"  --color-surface: {self.surface};",
            f"  --color-text: {self.foreground};",
            f"  --color-primary: {self.primary};",
            f"  --color-secondary: {self.secondary};",
            f"  --color-border: {self.border};",
            f"  --color-success: {self.success};",
            f"  --color-warning: {self.warning};",
            f"  --color-danger: {self.danger};",
            f"  --color-info: {self.info};",
            f"  --font-family: {self.font_family};",
            f"  --border-radius: {self.border_radius};",
        ]
        if self.is_high_contrast:
            lines.append("  --outline-focus: 3px solid var(--color-primary);")
        else:
            lines.append("  --outline-focus: 2px solid var(--color-primary);")
        lines.append("}")
        return "\n".join(lines)

    def generate_full_stylesheet(self) -> str:
        """Generate comprehensive standalone CSS stylesheet for the theme."""
        vars_css = self.generate_css_variables()
        rules = """
body {
  background-color: var(--color-bg);
  color: var(--color-text);
  font-family: var(--font-family);
  margin: 0;
  padding: 0;
}
.theme-surface {
  background-color: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--border-radius);
}
.theme-btn-primary {
  background-color: var(--color-primary);
  color: var(--color-bg);
  border: 1px solid var(--color-border);
  border-radius: var(--border-radius);
  padding: 6px 12px;
  font-weight: 600;
  cursor: pointer;
}
.theme-btn-primary:focus {
  outline: var(--outline-focus);
}
"""
        return f"{vars_css}\n{rules}"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "theme_id": self.theme_id,
            "id": self.theme_id,
            "name": self.name,
            "mode": self.mode,
            "background": self.background,
            "surface": self.surface,
            "foreground": self.foreground,
            "primary": self.primary,
            "secondary": self.secondary,
            "border": self.border,
            "success": self.success,
            "warning": self.warning,
            "danger": self.danger,
            "info": self.info,
            "font_family": self.font_family,
            "border_radius": self.border_radius,
            "is_high_contrast": self.is_high_contrast,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ThemePalette:
        tid = data.get("theme_id") or data.get("id") or "unnamed_theme"
        return cls(
            theme_id=tid,
            name=data.get("name", tid),
            mode=data.get("mode", "dark"),
            background=data.get("background", "#0f172a"),
            surface=data.get("surface", "#1e293b"),
            foreground=data.get("foreground", "#f8fafc"),
            primary=data.get("primary", "#38bdf8"),
            secondary=data.get("secondary", "#818cf8"),
            border=data.get("border", "#334155"),
            success=data.get("success", "#22c55e"),
            warning=data.get("warning", "#eab308"),
            danger=data.get("danger", "#ef4444"),
            info=data.get("info", "#06b6d4"),
            font_family=data.get("font_family", "monospace"),
            border_radius=data.get("border_radius", "6px"),
            is_high_contrast=bool(data.get("is_high_contrast", False)),
            metadata=dict(data.get("metadata", {})),
        )


# Default built-in palettes
BUILTIN_PALETTES: Dict[str, ThemePalette] = {
    "dark_slate": ThemePalette(
        theme_id="dark_slate",
        name="Dark Slate (Default)",
        mode="dark",
        background="#0b0f19",
        surface="#111827",
        foreground="#f3f4f6",
        primary="#38bdf8",
        secondary="#818cf8",
        border="#1f2937",
        success="#22c55e",
        warning="#eab308",
        danger="#ef4444",
        info="#06b6d4",
    ),
    "high_contrast_dark": ThemePalette(
        theme_id="high_contrast_dark",
        name="High Contrast Dark (WCAG AAA)",
        mode="high_contrast",
        background="#000000",
        surface="#111111",
        foreground="#ffffff",
        primary="#ffff00",
        secondary="#00ffff",
        border="#ffffff",
        success="#00ff00",
        warning="#ffff00",
        danger="#ff0000",
        info="#00ffff",
        is_high_contrast=True,
    ),
    "paper_light": ThemePalette(
        theme_id="paper_light",
        name="Paper Light",
        mode="light",
        background="#f8fafc",
        surface="#ffffff",
        foreground="#0f172a",
        primary="#0284c7",
        secondary="#6366f1",
        border="#cbd5e1",
        success="#16a34a",
        warning="#d97706",
        danger="#dc2626",
        info="#0891b2",
    ),
    "high_contrast_light": ThemePalette(
        theme_id="high_contrast_light",
        name="High Contrast Light (WCAG AAA)",
        mode="high_contrast",
        background="#ffffff",
        surface="#ffffff",
        foreground="#000000",
        primary="#0000ff",
        secondary="#4b0082",
        border="#000000",
        success="#006400",
        warning="#b8860b",
        danger="#8b0000",
        info="#00008b",
        is_high_contrast=True,
    ),
    "cyberpunk_neon": ThemePalette(
        theme_id="cyberpunk_neon",
        name="Cyberpunk Neon",
        mode="dark",
        background="#0d0221",
        surface="#19053b",
        foreground="#00f0ff",
        primary="#ff007f",
        secondary="#7209b7",
        border="#560bad",
        success="#05ffa1",
        warning="#ffe600",
        danger="#ff0055",
        info="#00f0ff",
    ),
}


class ThemeManager:
    """
    Sovereign Theme Manager for Hydra Desktop.
    Manages active theme palette, CSS variable compilation, and user preferences persistence.
    """

    def __init__(self, settings_file: Optional[str] = None) -> None:
        self.settings_file = settings_file
        self._lock = threading.RLock()
        self._palettes: Dict[str, ThemePalette] = dict(BUILTIN_PALETTES)
        self._active_theme_id: str = "dark_slate"
        self._subscribers: Dict[str, Callable[[ThemePalette], None]] = {}

        if self.settings_file and os.path.exists(self.settings_file):
            self.load_settings(self.settings_file)

    @property
    def total_palettes(self) -> int:
        with self._lock:
            return len(self._palettes)

    def register_palette(self, palette: Union[ThemePalette, Dict[str, Any]]) -> ThemePalette:
        """Register custom palette."""
        if isinstance(palette, ThemePalette):
            pal = palette
        elif isinstance(palette, dict):
            pal = ThemePalette.from_dict(palette)
        else:
            raise TypeError(f"Expected ThemePalette or dict, got {type(palette)}")

        with self._lock:
            self._palettes[pal.theme_id] = pal
        return pal

    def get_palette(self, theme_id: str) -> Optional[ThemePalette]:
        """Fetch palette by ID."""
        with self._lock:
            return self._palettes.get(theme_id)

    def list_palettes(self) -> List[ThemePalette]:
        """Return list of all registered palettes."""
        with self._lock:
            return list(self._palettes.values())

    def get_active_theme(self) -> ThemePalette:
        """Return currently active ThemePalette."""
        with self._lock:
            return self._palettes.get(self._active_theme_id, BUILTIN_PALETTES["dark_slate"])

    def set_active_theme(self, theme_id: str) -> ThemePalette:
        """Switch active theme and notify subscribers."""
        with self._lock:
            if theme_id not in self._palettes:
                raise KeyError(f"Theme '{theme_id}' not found in registry")
            self._active_theme_id = theme_id
            active = self._palettes[theme_id]
            subscribers = list(self._subscribers.values())

            if self.settings_file:
                try:
                    self.save_settings()
                except Exception:
                    pass

        for sub in subscribers:
            try:
                sub(active)
            except Exception:
                pass

        return active

    def get_active_css(self) -> str:
        """Generate full CSS stylesheet for the active theme."""
        return self.get_active_theme().generate_full_stylesheet()

    def subscribe(self, listener: Callable[[ThemePalette], None]) -> str:
        """Subscribe to theme changes."""
        sub_id = f"theme_sub_{uuid.uuid4().hex[:8]}"
        with self._lock:
            self._subscribers[sub_id] = listener
        return sub_id

    def unsubscribe(self, sub_id: str) -> bool:
        """Unsubscribe from theme changes."""
        with self._lock:
            return self._subscribers.pop(sub_id, None) is not None

    def save_settings(self, filepath: Optional[str] = None) -> None:
        """Persist theme preferences to JSON."""
        target = filepath or self.settings_file
        if not target:
            return
        os.makedirs(os.path.dirname(os.path.abspath(target)), exist_ok=True)
        with self._lock:
            payload = {
                "active_theme_id": self._active_theme_id,
                "custom_palettes": [
                    p.to_dict() for k, p in self._palettes.items() if k not in BUILTIN_PALETTES
                ],
            }
        tmp = f"{target}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        os.replace(tmp, target)

    def load_settings(self, filepath: Optional[str] = None) -> None:
        """Load theme preferences from JSON."""
        target = filepath or self.settings_file
        if not target or not os.path.exists(target) or os.path.getsize(target) == 0:
            return
        try:
            with open(target, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            return

        with self._lock:
            for pal_data in data.get("custom_palettes", []):
                self.register_palette(pal_data)
            act_id = data.get("active_theme_id")
            if act_id and act_id in self._palettes:
                self._active_theme_id = act_id


_GLOBAL_THEME_MANAGER: Optional[ThemeManager] = None
_GLOBAL_THEME_LOCK = threading.RLock()


def get_theme_manager() -> ThemeManager:
    """Acquire thread-safe singleton ThemeManager."""
    global _GLOBAL_THEME_MANAGER
    with _GLOBAL_THEME_LOCK:
        if _GLOBAL_THEME_MANAGER is None:
            _GLOBAL_THEME_MANAGER = ThemeManager()
        return _GLOBAL_THEME_MANAGER


def reset_theme_manager() -> ThemeManager:
    """Reset singleton ThemeManager."""
    global _GLOBAL_THEME_MANAGER
    with _GLOBAL_THEME_LOCK:
        _GLOBAL_THEME_MANAGER = ThemeManager()
        return _GLOBAL_THEME_MANAGER
