"""
Thumbnail generator using HTML/CSS + Playwright screenshot.

Renders thumbnails via a headless browser for pixel-perfect CSS control
over fonts, letter-spacing, line-height, and highlight styling.

EVERY visual property is driven by config. The defaults here are generic —
brand-specific values live in .podcli/thumbnail-config.json.
"""

import json
import os
import subprocess
import sys
import tempfile
from typing import Optional


def _load_config() -> dict:
    """Load thumbnail config, merged with defaults.

    Defaults are intentionally generic so each project gets its own look
    via .podcli/thumbnail-config.json.
    """
    defaults = {
        "width": 1080,
        "height": 1920,

        # Colors
        "bg_color": "#1A1A2E",
        "text_color": "#FFFFFF",
        "accent_color": "#E94560",

        # Frame border (around entire thumbnail)
        "frame_border_width": 3,
        "frame_border_color": None,  # None = accent

        # Text box
        "box_x": "40px",        # CSS left — near edge with safe area
        "box_y": "73%",         # CSS top (no photo)
        "box_y_with_photo": "76%",
        "box_width": "1000px",  # CSS width — max width with safe areas
        "box_min_height": "180px",
        "box_border_width": 3,
        "box_border_color": None,  # None = accent
        "box_fill_color": "rgba(26,26,46,0.90)",
        "box_padding": "24px 32px",

        # Line 1 (top line — typically white text)
        "line1_font_size": "56px",
        "line1_font_weight": "600",
        "line1_letter_spacing": "1px",
        "line1_line_height": 1.15,
        "line1_margin_bottom": "8px",
        "line1_uppercase": True,
        "line1_color": "#FFFFFF",
        "line1_nowrap": True,

        # Line 2 (bottom line — highlighted)
        "line2_font_size": "52px",
        "line2_font_weight": "400",
        "line2_font_style": "italic",
        "line2_letter_spacing": "1px",
        "line2_line_height": 1.15,
        "line2_uppercase": True,
        "line2_highlight_color": None,  # None = accent
        "line2_text_color": "#1A1A2E",
        "line2_highlight_padding": "4px 16px",

        # Photo
        "photo_brightness": 0.85,

        # Gradients
        "gradient_top_height": "25%",
        "gradient_top_start_color": "rgba(0,0,0,0.5)",
        "gradient_top_end_color": "transparent",
        "gradient_bottom_start": "50%",
        "gradient_bottom_end_color": "#1A1A2E",
        "gradient_bottom_fade_point": "70%",

        # Logo
        "logo_position": "bottom-center",  # top-left, top-right, top-center, bottom-center, none
        "logo_height": "50px",
        "logo_margin": "50px",
        "logo_opacity": 0.35,

        # Font
        "font_family": "'Inter', 'Helvetica Neue', 'Arial', sans-serif",
        "font_import_url": "https://fonts.googleapis.com/css2?family=Inter:ital,wght@0,400;0,500;0,600;0,700;1,400;1,500;1,600;1,700&display=swap",

        # Variations
        "variations": 3,
        "variation_offset_up": "3%",
        "variation_offset_down": "2%",
    }

    config_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "..", ".podcli", "thumbnail-config.json"
    )
    if os.path.exists(config_path):
        try:
            with open(config_path) as f:
                user = json.load(f)
            defaults.update(user)
        except Exception:
            pass
    return defaults


def _build_html(
    line1: str,
    line2: str,
    photo_path: Optional[str] = None,
    logo_path: Optional[str] = None,
    config: Optional[dict] = None,
    variation: int = 0,
    face_info: Optional[dict] = None,
) -> str:
    """Build the HTML for a single thumbnail — all values from config.

    Args:
        face_info: Dict with face_y_pct, face_h_pct etc. from frame extraction.
                   Used to auto-position the text box below the face.
    """
    cfg = _load_config()
    if config:
        cfg.update(config)

    w = cfg["width"]
    h = cfg["height"]
    bg = cfg["bg_color"]
    accent = cfg.get("frame_border_color") or cfg["accent_color"]
    box_border_color = cfg.get("box_border_color") or cfg["accent_color"]
    hl_color = cfg.get("line2_highlight_color") or cfg["accent_color"]

    # Clean any stray slashes from title split
    line1 = line1.strip().strip("/").strip()
    line2 = line2.strip().strip("/").strip()
    l1 = line1.upper() if cfg.get("line1_uppercase", True) else line1
    l2 = line2.upper() if cfg.get("line2_uppercase", True) else line2

    has_photo = photo_path and os.path.exists(str(photo_path))

    # Auto-position text box below face if we have face data
    if face_info and has_photo:
        face_y = face_info.get("face_y_pct", 50)
        face_h = face_info.get("face_h_pct", 20)
        # Bottom edge of face as percentage
        face_bottom = face_y + face_h / 2
        # Place box center 15% below the face bottom, clamped to 65-85%
        auto_y = min(85, max(65, face_bottom + 15))
        default_y = f"{auto_y:.0f}%"
    elif has_photo:
        default_y = cfg.get("box_y_with_photo", cfg.get("box_y", "76%"))
    else:
        default_y = cfg.get("box_y", "73%")

    box_y = default_y
    offset_up = cfg.get("variation_offset_up", "3%")
    offset_down = cfg.get("variation_offset_down", "2%")
    if variation == 1:
        box_y = f"calc({default_y} - {offset_up})"
    elif variation == 2:
        box_y = f"calc({default_y} + {offset_down})"

    # Photo CSS
    photo_css = ""
    photo_uri = ""
    if has_photo:
        photo_uri = f"file://{os.path.abspath(photo_path)}"
        brightness = cfg.get("photo_brightness", 0.85)
        photo_css = f"""
        .photo {{
            position: absolute; top: 0; left: 0; width: {w}px; height: {h}px;
            overflow: hidden;
            z-index: 1;
        }}
        .photo img {{
            width: 100%; height: 100%;
            object-fit: cover;
            object-position: center center;
            filter: brightness({brightness});
        }}
        .photo-vignette {{
            position: absolute; top: 0; left: 0; width: 100%; height: 100%;
            z-index: 1;
        }}
        """

    # Logo
    logo_html = ""
    logo_pos = cfg.get("logo_position", "none")
    if logo_path and os.path.exists(logo_path) and logo_pos != "none":
        logo_uri = f"file://{os.path.abspath(logo_path)}"
        logo_h = cfg.get("logo_height", "50px")
        logo_margin = cfg.get("logo_margin", "50px")
        logo_opacity = cfg.get("logo_opacity", 0.35)
        logo_style = f"position:absolute; height:{logo_h}; width:auto; opacity:{logo_opacity}; z-index:8;"
        if logo_pos == "bottom-center":
            logo_style += f"bottom:{logo_margin}; left:50%; transform:translateX(-50%);"
        elif logo_pos == "top-left":
            logo_style += f"top:{logo_margin}; left:{logo_margin};"
        elif logo_pos == "top-right":
            logo_style += f"top:{logo_margin}; right:{logo_margin};"
        elif logo_pos == "top-center":
            logo_style += f"top:{logo_margin}; left:50%; transform:translateX(-50%);"
        logo_html = f'<img src="{logo_uri}" style="{logo_style}" />'

    # Config-driven values
    frame_w = cfg.get("frame_border_width", 3)
    box_x = cfg.get("box_x", "110px")
    box_width = cfg.get("box_width", "860px")
    box_min_h = cfg.get("box_min_height", "220px")
    box_border_w = cfg.get("box_border_width", 3)
    box_fill = cfg.get("box_fill_color", f"rgba(26,26,46,0.90)")
    box_padding = cfg.get("box_padding", "28px 36px")

    # Base font sizes from config — extract int from "64px", "1.5rem", or bare "64"
    def _px(val, default):
        import re
        m = re.search(r"(\d+)", str(val))
        return int(m.group(1)) if m else default

    l1_base_size = _px(cfg.get("line1_font_size", "56px"), 56)
    l2_base_size = _px(cfg.get("line2_font_size", "52px"), 52)
    l1_base_spacing = _px(cfg.get("line1_letter_spacing", "1px"), 1)
    l2_base_spacing = _px(cfg.get("line2_letter_spacing", "1px"), 1)

    # Auto-shrink: estimate text width vs box width, scale down if needed.
    box_w_px = int(str(box_width).replace("px", "")) if "px" in str(box_width) else 860
    # Parse horizontal padding (format: "Vpx Hpx" or "Apx")
    pad_parts = [int(p.replace("px", "")) for p in str(box_padding).split() if "px" in p]
    h_pad = pad_parts[1] if len(pad_parts) >= 2 else pad_parts[0] if pad_parts else 36
    usable_w = box_w_px - h_pad * 2  # Both sides

    def _fit_size(text, base_size, base_spacing):
        """Shrink font size so text fits on one line."""
        # Character width varies by font. Use conservative estimate:
        # uppercase chars ~0.68× font_size, lowercase ~0.52×, average ~0.60×
        # Add safety margin of 5% to prevent edge-case overflow
        is_upper = text == text.upper()
        char_ratio = 0.68 if is_upper else 0.58
        char_w = char_ratio * base_size + base_spacing
        est_width = len(text) * char_w
        if est_width <= usable_w * 0.95:  # 5% safety margin
            return base_size, base_spacing
        scale = (usable_w * 0.95) / est_width
        new_size = max(32, int(base_size * scale))
        new_spacing = max(0, int(base_spacing * scale))
        return new_size, new_spacing

    l1_px, l1_sp = _fit_size(l1, l1_base_size, l1_base_spacing)
    l2_px, l2_sp = _fit_size(l2, l2_base_size, l2_base_spacing)

    l1_size = f"{l1_px}px"
    l1_spacing = f"{l1_sp}px"
    l2_size = f"{l2_px}px"
    l2_spacing = f"{l2_sp}px"

    l1_weight = cfg.get("line1_font_weight", "600")
    l1_lh = cfg.get("line1_line_height", 1.15)
    l1_mb = cfg.get("line1_margin_bottom", "10px")
    l1_color = cfg.get("line1_color", "#FFFFFF")
    l1_nowrap = "nowrap"  # Always nowrap — we shrink to fit instead

    l2_weight = cfg.get("line2_font_weight", "500")
    l2_style = cfg.get("line2_font_style", "italic")
    l2_lh = cfg.get("line2_line_height", 1.15)
    l2_text_color = cfg.get("line2_text_color", "#1A1A2E")
    l2_hl_pad = cfg.get("line2_highlight_padding", "4px 16px")

    grad_top_h = cfg.get("gradient_top_height", "25%")
    grad_top_start = cfg.get("gradient_top_start_color", "rgba(0,0,0,0.5)")
    grad_top_end = cfg.get("gradient_top_end_color", "transparent")
    grad_bot_start = cfg.get("gradient_bottom_start", "50%")
    grad_bot_end_color = cfg.get("gradient_bottom_end_color", bg)
    grad_bot_fade = cfg.get("gradient_bottom_fade_point", "70%")

    font_family = cfg.get("font_family", "'Inter', sans-serif")
    font_url = cfg.get("font_import_url", "")
    font_import = f"@import url('{font_url}');" if font_url else ""

    return f"""<!DOCTYPE html>
<html>
<head>
<style>
{font_import}

* {{ margin: 0; padding: 0; box-sizing: border-box; }}

body {{
    width: {w}px;
    height: {h}px;
    overflow: hidden;
    background: {bg};
    font-family: {font_family};
    -webkit-font-smoothing: antialiased;
}}

.frame {{
    position: absolute;
    top: 0; left: 0; right: 0; bottom: 0;
    box-shadow: inset 0 0 0 {frame_w}px {accent};
    z-index: 10;
    pointer-events: none;
}}

{photo_css}
.photo {{ z-index: 1; }}

.gradient-top {{
    position: absolute; top: 0; left: 0; width: 100%; height: {grad_top_h};
    background: linear-gradient(to bottom, {grad_top_start} 0%, {grad_top_end} 100%);
    z-index: 2;
}}

.gradient-bottom {{
    position: absolute; top: {grad_bot_start}; left: 0; width: 100%; bottom: 0;
    background: linear-gradient(to bottom, transparent 0%, {grad_bot_end_color} {grad_bot_fade});
    z-index: 2;
}}

.text-box {{
    position: absolute;
    left: {box_x};
    top: {box_y};
    transform: translateY(-50%);
    width: {box_width};
    height: auto;
    min-height: {box_min_h};
    z-index: 5;
    border: {box_border_w}px solid {box_border_color};
    background: {box_fill};
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    padding: {box_padding};
    box-sizing: border-box;
}}

.line1 {{
    font-size: {l1_size};
    font-weight: {l1_weight};
    letter-spacing: {l1_spacing};
    color: {l1_color};
    text-transform: uppercase;
    text-align: center;
    line-height: {l1_lh};
    margin-bottom: {l1_mb};
    white-space: {l1_nowrap};
    overflow: hidden;
    text-overflow: ellipsis;
    max-width: 100%;
}}

.line2 {{
    display: flex;
    justify-content: center;
}}

.line2 span {{
    font-size: {l2_size};
    font-weight: {l2_weight};
    font-style: {l2_style};
    letter-spacing: {l2_spacing};
    text-transform: uppercase;
    line-height: {l2_lh};
    background: {hl_color};
    color: {l2_text_color};
    padding: {l2_hl_pad};
    border-radius: 0;
    display: inline-block;
    box-decoration-break: clone;
    -webkit-box-decoration-break: clone;
}}

.logo {{ z-index: 8; }}
</style>
</head>
<body>
    <div class="frame"></div>
    {f'<div class="photo"><img src="{photo_uri}" /></div><div class="photo-vignette"></div>' if has_photo else ''}
    <div class="gradient-top"></div>
    <div class="gradient-bottom"></div>
    <div class="text-box">
        <div class="line1">{l1}</div>
        <div class="line2"><span>{l2}</span></div>
    </div>
    {logo_html}
</body>
</html>"""


def generate_thumbnail(
    line1: str,
    line2: str,
    output_path: str,
    photo_path: Optional[str] = None,
    logo_path: Optional[str] = None,
    config: Optional[dict] = None,
    variation: int = 0,
    face_info: Optional[dict] = None,
) -> str:
    """Generate a single thumbnail via HTML + Playwright screenshot."""
    cfg = _load_config()
    if config:
        cfg.update(config)

    html = _build_html(line1, line2, photo_path, logo_path, cfg, variation, face_info=face_info)

    # Write HTML to temp file
    tmp_html = tempfile.NamedTemporaryFile(suffix=".html", delete=False, mode="w")
    tmp_html.write(html)
    tmp_html.close()

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

    try:
        w = cfg["width"]
        h = cfg["height"]

        result = subprocess.run(
            [
                "npx", "playwright", "screenshot",
                "--viewport-size", f"{w}, {h}",
                "--wait-for-timeout", "1500",
                f"file://{tmp_html.name}",
                output_path,
            ],
            capture_output=True, text=True, timeout=60,
        )

        if result.returncode != 0:
            raise RuntimeError(f"Screenshot failed: {result.stderr[:300]}")

        return output_path
    finally:
        os.unlink(tmp_html.name)


def generate_variations(
    title: str,
    output_dir: str,
    photo_path: Optional[str] = None,
    video_path: Optional[str] = None,
    logo_path: Optional[str] = None,
    config: Optional[dict] = None,
) -> list[str]:
    """Generate multiple thumbnail variations."""
    cfg = _load_config()
    if config:
        cfg.update(config)

    os.makedirs(output_dir, exist_ok=True)

    face_info = None

    # Auto-extract face frame from video if no photo
    if not photo_path and video_path:
        try:
            from services.thumbnail_generator import extract_face_frame
            frame_path = os.path.join(output_dir, "_face_frame.png")
            result = extract_face_frame(video_path, frame_path, target_width=cfg["width"], target_height=cfg["height"])
            if result:
                photo_path = result["path"]
                face_info = result
        except Exception:
            pass

    # Title split — if title contains " / " use that as the split point
    # (PodStack /plan-thumbnails outputs "LINE 1 / LINE 2" format)
    if " / " in title:
        parts = title.split(" / ", 1)
        line1 = parts[0].strip()
        line2 = parts[1].strip()
    else:
        # Auto-split: balance character count across two lines
        words = title.split()
        if len(words) <= 2:
            line1 = words[0] if words else ""
            line2 = " ".join(words[1:])
        else:
            best_split = len(words) // 2
            best_diff = float("inf")
            for i in range(1, len(words)):
                diff = abs(len(" ".join(words[:i])) - len(" ".join(words[i:])))
                if diff < best_diff:
                    best_diff = diff
                    best_split = i
            line1 = " ".join(words[:best_split])
            line2 = " ".join(words[best_split:])

    paths = []
    n = cfg.get("variations", 3)

    for i in range(n):
        path = os.path.join(output_dir, f"thumb_v{i+1}.png")
        generate_thumbnail(line1, line2, path, photo_path, logo_path, cfg, variation=i, face_info=face_info)
        paths.append(path)

    return paths


def thumbnail_to_video_frame(
    thumbnail_path: str,
    output_path: str,
    duration: float = 0.8,
    width: int = 1080,
    height: int = 1920,
) -> str:
    """Convert thumbnail PNG to a short video clip for appending.

    Includes a silent audio track so concat_outro's acrossfade filter
    works correctly. No baked-in fades — the crossfade transition
    handles the visual blend.
    """
    cmd = [
        "ffmpeg", "-y",
        "-loop", "1", "-i", thumbnail_path,
        "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
        "-t", str(duration),
        "-vf", f"scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black",
        "-c:v", "libx264", "-crf", "18", "-preset", "fast",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k",
        "-shortest",
        output_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"Thumbnail to video failed: {result.stderr[-300:]}")
    return output_path
