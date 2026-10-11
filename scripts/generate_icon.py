"""
Build Hydra icon assets from the single SVG source assets/hydra_icon.svg.
Writes the multi-size Windows .ico plus the PNG masters the web desk serves.
Needs PySide6 (QtSvg) for rendering and Pillow for the .ico container.
"""

from __future__ import annotations

import io
import os
import sys
from typing import List, Tuple

from PIL import Image

ICO_SIZES = (16, 24, 32, 48, 64, 128, 256)
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SVG_PATH = os.path.join(REPO_ROOT, "assets", "hydra_icon.svg")
ICO_TARGETS = (
    os.path.join(REPO_ROOT, "assets", "hydra_desktop.ico"),
    os.path.join(REPO_ROOT, "desktop", "static", "favicon.ico"),
)
PNG_TARGETS = (
    os.path.join(REPO_ROOT, "assets", "hydra_desktop.png"),
    os.path.join(REPO_ROOT, "desktop", "static", "hydra_desktop.png"),
)
PNG_SIZE = 512


def create_hydra_icon(size: int = 256, svg_path: str = SVG_PATH) -> Image.Image:
    """Render the SVG source at size x size as an RGBA image."""
    from PySide6.QtCore import QBuffer, QByteArray, QIODevice
    from PySide6.QtGui import QGuiApplication, QImage, QPainter
    from PySide6.QtSvg import QSvgRenderer

    app = QGuiApplication.instance() or QGuiApplication(sys.argv[:1] + ["-platform", "offscreen"])
    renderer = QSvgRenderer(svg_path)
    if not renderer.isValid():
        raise ValueError(f"Invalid SVG: {svg_path}")
    image = QImage(size, size, QImage.Format.Format_ARGB32)
    image.fill(0)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    renderer.render(painter)
    painter.end()
    data = QByteArray()
    buf = QBuffer(data)
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buf, "PNG")
    del app
    return Image.open(io.BytesIO(bytes(data))).convert("RGBA")


def generate_assets() -> Tuple[List[str], List[str]]:
    """Write every .ico and .png target from the SVG; returns written paths."""
    frames = [create_hydra_icon(s) for s in ICO_SIZES]
    for path in ICO_TARGETS:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        frames[-1].save(
            path,
            format="ICO",
            sizes=[(s, s) for s in ICO_SIZES],
            append_images=frames[:-1],
        )
    master = create_hydra_icon(PNG_SIZE)
    for path in PNG_TARGETS:
        master.save(path, format="PNG")
    return list(ICO_TARGETS), list(PNG_TARGETS)


if __name__ == "__main__":
    icos, pngs = generate_assets()
    for p in icos + pngs:
        print(f"wrote {os.path.relpath(p, REPO_ROOT)}")
