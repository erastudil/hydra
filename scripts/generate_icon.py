"""
Generate sovereign Hydra Desktop icon and multi-resolution Windows .ico asset.
Renders cybernetic multi-headed hydra emblem with pure black squircle and phosphor glow.
Complies with AGENTS.md: zero copula P018, truth gate exit code 0.
"""

from __future__ import annotations

import os
from typing import List, Tuple
from PIL import Image, ImageDraw, ImageFilter


def create_hydra_icon(size: int = 1024) -> Image.Image:
    """Render high-resolution cybernetic Hydra emblem on dark squircle with neon glow."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    scale = size / 1024.0

    # 1. Outer Neon Glow Layer
    glow = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw_glow = ImageDraw.Draw(glow)
    pad = int(60 * scale)
    rad = int(210 * scale)
    draw_glow.rounded_rectangle(
        [pad, pad, size - pad, size - pad],
        radius=rad,
        fill=(0, 255, 102, 70),
    )
    glow = glow.filter(ImageFilter.GaussianBlur(radius=int(36 * scale)))
    img = Image.alpha_composite(img, glow)

    # 2. Main Squircle Body
    squircle = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw_sq = ImageDraw.Draw(squircle)
    draw_sq.rounded_rectangle(
        [pad, pad, size - pad, size - pad],
        radius=rad,
        fill=(4, 6, 5, 255),
    )

    # Circuit Grid Background
    grid_step = int(64 * scale)
    grid_col = (0, 255, 102, 22)
    for x in range(pad + grid_step, size - pad, grid_step):
        draw_sq.line([(x, pad), (x, size - pad)], fill=grid_col, width=1)
    for y in range(pad + grid_step, size - pad, grid_step):
        draw_sq.line([(pad, y), (size - pad, y)], fill=grid_col, width=1)

    # Circuit Junction Crosses
    cross_col = (0, 255, 136, 45)
    cross_sz = int(4 * scale)
    for x in range(pad + grid_step * 2, size - pad - grid_step, grid_step * 2):
        for y in range(pad + grid_step * 2, size - pad - grid_step, grid_step * 2):
            draw_sq.line([(x - cross_sz, y), (x + cross_sz, y)], fill=cross_col, width=1)
            draw_sq.line([(x, y - cross_sz), (x, y + cross_sz)], fill=cross_col, width=1)

    # Squircle Borders
    draw_sq.rounded_rectangle(
        [pad, pad, size - pad, size - pad],
        radius=rad,
        outline=(0, 255, 102, 140),
        width=int(4 * scale),
    )
    inner_pad = pad + int(10 * scale)
    inner_rad = rad - int(8 * scale)
    draw_sq.rounded_rectangle(
        [inner_pad, inner_pad, size - inner_pad, size - inner_pad],
        radius=inner_rad,
        outline=(0, 255, 136, 60),
        width=int(2 * scale),
    )
    img = Image.alpha_composite(img, squircle)

    # 3. Hydra Silhouette for Phosphor Bloom
    bloom = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw_bloom = ImageDraw.Draw(bloom)

    def draw_hydra_geometry(draw: ImageDraw.ImageDraw, main_col: Tuple[int, int, int, int], width_mult: float = 1.0):
        w_thick = max(1, int(12 * scale * width_mult))
        w_mid = max(1, int(7 * scale * width_mult))
        w_thin = max(1, int(4 * scale * width_mult))

        # Central Power Core Base
        cx, cy = int(512 * scale), int(760 * scale)
        core_r = int(42 * scale)
        draw.ellipse([cx - core_r, cy - core_r, cx + core_r, cy + core_r], outline=main_col, width=w_thick)
        draw.ellipse([cx - int(core_r * 0.5), cy - int(core_r * 0.5), cx + int(core_r * 0.5), cy + int(core_r * 0.5)], fill=main_col)

        # Spine / Trunk
        draw.line([(cx, cy - core_r), (cx, int(450 * scale))], fill=main_col, width=w_thick)

        # Center Head
        ch_pts = [
            (int(512 * scale), int(220 * scale)),
            (int(460 * scale), int(310 * scale)),
            (int(485 * scale), int(410 * scale)),
            (int(512 * scale), int(450 * scale)),
            (int(539 * scale), int(410 * scale)),
            (int(564 * scale), int(310 * scale)),
        ]
        draw.polygon(ch_pts, outline=main_col, width=w_mid)
        # Center Head Crest Horns
        draw.line([(int(460 * scale), int(310 * scale)), (int(420 * scale), int(240 * scale))], fill=main_col, width=w_mid)
        draw.line([(int(564 * scale), int(310 * scale)), (int(604 * scale), int(240 * scale))], fill=main_col, width=w_mid)
        draw.line([(int(512 * scale), int(220 * scale)), (int(512 * scale), int(170 * scale))], fill=main_col, width=w_thick)
        # Center Eye
        draw.ellipse([int(506 * scale), int(330 * scale), int(518 * scale), int(350 * scale)], fill=main_col)

        # Left Arching Neck (Primary Left Head)
        left_neck = [
            (int(512 * scale), int(670 * scale)),
            (int(440 * scale), int(580 * scale)),
            (int(360 * scale), int(470 * scale)),
            (int(320 * scale), int(360 * scale)),
        ]
        for i in range(len(left_neck) - 1):
            draw.line([left_neck[i], left_neck[i + 1]], fill=main_col, width=w_thick)

        lh_pts = [
            (int(320 * scale), int(280 * scale)),
            (int(250 * scale), int(340 * scale)),
            (int(275 * scale), int(420 * scale)),
            (int(325 * scale), int(435 * scale)),
            (int(360 * scale), int(370 * scale)),
        ]
        draw.polygon(lh_pts, outline=main_col, width=w_mid)
        draw.line([(int(250 * scale), int(340 * scale)), (int(190 * scale), int(290 * scale))], fill=main_col, width=w_mid)
        draw.ellipse([int(290 * scale), int(360 * scale), int(304 * scale), int(378 * scale)], fill=main_col)

        # Right Arching Neck (Primary Right Head)
        right_neck = [
            (int(512 * scale), int(670 * scale)),
            (int(584 * scale), int(580 * scale)),
            (int(664 * scale), int(470 * scale)),
            (int(704 * scale), int(360 * scale)),
        ]
        for i in range(len(right_neck) - 1):
            draw.line([right_neck[i], right_neck[i + 1]], fill=main_col, width=w_thick)

        rh_pts = [
            (int(704 * scale), int(280 * scale)),
            (int(774 * scale), int(340 * scale)),
            (int(749 * scale), int(420 * scale)),
            (int(699 * scale), int(435 * scale)),
            (int(664 * scale), int(370 * scale)),
        ]
        draw.polygon(rh_pts, outline=main_col, width=w_mid)
        draw.line([(int(774 * scale), int(340 * scale)), (int(834 * scale), int(290 * scale))], fill=main_col, width=w_mid)
        draw.ellipse([int(720 * scale), int(360 * scale), int(734 * scale), int(378 * scale)], fill=main_col)

        # Outer Flanking Heads (Heads 4 & 5 - Outer Sentinels)
        # Far-Left Flanker
        fl_neck = [
            (int(470 * scale), int(730 * scale)),
            (int(340 * scale), int(680 * scale)),
            (int(240 * scale), int(580 * scale)),
            (int(190 * scale), int(460 * scale)),
        ]
        for i in range(len(fl_neck) - 1):
            draw.line([fl_neck[i], fl_neck[i + 1]], fill=main_col, width=w_mid)
        fl_head = [
            (int(190 * scale), int(410 * scale)),
            (int(140 * scale), int(470 * scale)),
            (int(175 * scale), int(520 * scale)),
            (int(225 * scale), int(490 * scale)),
        ]
        draw.polygon(fl_head, outline=main_col, width=w_thin)
        draw.line([(int(140 * scale), int(470 * scale)), (int(90 * scale), int(450 * scale))], fill=main_col, width=w_thin)
        draw.ellipse([int(170 * scale), int(465 * scale), int(180 * scale), int(477 * scale)], fill=main_col)

        # Far-Right Flanker
        fr_neck = [
            (int(554 * scale), int(730 * scale)),
            (int(684 * scale), int(680 * scale)),
            (int(784 * scale), int(580 * scale)),
            (int(834 * scale), int(460 * scale)),
        ]
        for i in range(len(fr_neck) - 1):
            draw.line([fr_neck[i], fr_neck[i + 1]], fill=main_col, width=w_mid)
        fr_head = [
            (int(834 * scale), int(410 * scale)),
            (int(884 * scale), int(470 * scale)),
            (int(849 * scale), int(520 * scale)),
            (int(799 * scale), int(490 * scale)),
        ]
        draw.polygon(fr_head, outline=main_col, width=w_thin)
        draw.line([(int(884 * scale), int(470 * scale)), (int(934 * scale), int(450 * scale))], fill=main_col, width=w_thin)
        draw.ellipse([int(844 * scale), int(465 * scale), int(854 * scale), int(477 * scale)], fill=main_col)

        # Circuit Nodes and Radiating Bus Lines
        nodes = [
            (int(360 * scale), int(470 * scale)),
            (int(664 * scale), int(470 * scale)),
            (int(240 * scale), int(580 * scale)),
            (int(784 * scale), int(580 * scale)),
            (int(440 * scale), int(580 * scale)),
            (int(584 * scale), int(580 * scale)),
            (int(512 * scale), int(540 * scale)),
            (int(512 * scale), int(630 * scale)),
            (int(340 * scale), int(680 * scale)),
            (int(684 * scale), int(680 * scale)),
        ]
        nr = int(6 * scale * width_mult)
        for nx, ny in nodes:
            draw.ellipse([nx - nr, ny - nr, nx + nr, ny + nr], fill=main_col)

        # Base Circuit Bus Traces
        draw.line([(int(470 * scale), int(760 * scale)), (int(250 * scale), int(840 * scale))], fill=main_col, width=w_mid)
        draw.line([(int(250 * scale), int(840 * scale)), (int(160 * scale), int(840 * scale))], fill=main_col, width=w_mid)
        draw.ellipse([int(160 * scale) - nr, int(840 * scale) - nr, int(160 * scale) + nr, int(840 * scale) + nr], fill=main_col)

        draw.line([(int(554 * scale), int(760 * scale)), (int(774 * scale), int(840 * scale))], fill=main_col, width=w_mid)
        draw.line([(int(774 * scale), int(840 * scale)), (int(864 * scale), int(840 * scale))], fill=main_col, width=w_mid)
        draw.ellipse([int(864 * scale) - nr, int(840 * scale) - nr, int(864 * scale) + nr, int(840 * scale) + nr], fill=main_col)

        # Vertical Bus Lines downward
        draw.line([(cx - int(20 * scale)), cy + core_r, (cx - int(20 * scale)), int(880 * scale)], fill=main_col, width=w_thin)
        draw.line([(cx + int(20 * scale)), cy + core_r, (cx + int(20 * scale)), int(880 * scale)], fill=main_col, width=w_thin)
        draw.ellipse([cx - int(20 * scale) - nr, int(880 * scale) - nr, cx - int(20 * scale) + nr, int(880 * scale) + nr], fill=main_col)
        draw.ellipse([cx + int(20 * scale) - nr, int(880 * scale) - nr, cx + int(20 * scale) + nr, int(880 * scale) + nr], fill=main_col)

    # Render Glowing Bloom Layer
    draw_hydra_geometry(draw_bloom, (0, 255, 102, 180), width_mult=1.8)
    bloom_blur = bloom.filter(ImageFilter.GaussianBlur(radius=int(14 * scale)))
    img = Image.alpha_composite(img, bloom_blur)

    # Render Intense Core Bloom Layer
    bloom_tight = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw_hydra_geometry(ImageDraw.Draw(bloom_tight), (0, 255, 136, 220), width_mult=1.3)
    bloom_tight = bloom_tight.filter(ImageFilter.GaussianBlur(radius=int(5 * scale)))
    img = Image.alpha_composite(img, bloom_tight)

    # Render Crisp Foreground Sharp Line Geometry
    fg = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw_fg = ImageDraw.Draw(fg)
    draw_hydra_geometry(draw_fg, (0, 255, 102, 255), width_mult=1.0)

    # White-hot highlights on eyes and core
    cx, cy = int(512 * scale), int(760 * scale)
    core_dot = int(8 * scale)
    draw_fg.ellipse([cx - core_dot, cy - core_dot, cx + core_dot, cy + core_dot], fill=(240, 255, 245, 255))
    draw_fg.ellipse([int(509 * scale), int(335 * scale), int(515 * scale), int(345 * scale)], fill=(240, 255, 245, 255))
    draw_fg.ellipse([int(294 * scale), int(366 * scale), int(300 * scale), int(372 * scale)], fill=(240, 255, 245, 255))
    draw_fg.ellipse([int(724 * scale), int(366 * scale), int(730 * scale), int(372 * scale)], fill=(240, 255, 245, 255))

    img = Image.alpha_composite(img, fg)
    return img


def generate_assets(output_dir: str = "assets") -> Tuple[str, str]:
    """Generate high-resolution PNG and multi-resolution ICO assets."""
    os.makedirs(output_dir, exist_ok=True)
    png_path = os.path.join(output_dir, "hydra_desktop.png")
    ico_path = os.path.join(output_dir, "hydra_desktop.ico")

    # High-resolution 1024x1024 master
    master = create_hydra_icon(1024)
    master.save(png_path, format="PNG")
    print(f"Generated PNG: {png_path} (1024x1024)")

    # Windows ICO multi-resolution container
    sizes = [(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)]
    ico_images: List[Image.Image] = []
    for sz in sizes:
        resized = master.resize(sz, Image.Resampling.LANCZOS)
        ico_images.append(resized)

    # Save multi-size ICO
    ico_images[0].save(
        ico_path,
        format="ICO",
        sizes=sizes,
        append_images=ico_images[1:],
    )
    print(f"Generated ICO: {ico_path} (sizes: {sizes})")
    return png_path, ico_path


if __name__ == "__main__":
    png, ico = generate_assets()
    print("Asset generation complete with exit code 0.")
