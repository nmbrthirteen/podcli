"""
Thumbnail generator for YouTube Shorts.

Generates multiple thumbnail variations per clip using Pillow.
Configurable via .podcli/thumbnail-config.json for brand customization.

Each thumbnail: 1080x1920 PNG with:
- Guest photo (background removed or darkened)
- Two-line title text (Line 1 white, Line 2 accent highlight)
- Logo/watermark
- Dark gradient overlay for text readability
"""

import json
import os
import sys
from typing import Optional

try:
    from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageEnhance
except ImportError:
    print("Pillow not installed. Run: pip install Pillow", file=sys.stderr)
    sys.exit(1)


# ── Default config (overridden by .podcli/thumbnail-config.json) ──

DEFAULT_CONFIG = {
    "width": 1080,
    "height": 1920,

    # Colors
    "bg_color": "#0D0D0D",
    "text_color": "#FFFFFF",
    "accent_color": "#00CED1",
    "highlight_bg": "#0D0D0D",

    # Text
    "font_size_line1": 64,
    "font_size_line2": 64,
    "line1_uppercase": True,
    "line2_italic": True,
    "line2_color": None,  # None = use accent_color
    "text_position": "lower",  # "lower", "center", "upper"
    "max_chars_per_line": 18,

    # Guest photo
    "photo_darken": 0.4,       # 0 = black, 1 = original brightness
    "photo_blur": 2,            # Gaussian blur px
    "photo_position": "center", # "center", "top", "bottom"

    # Logo
    "logo_position": "top-left",  # "top-left", "top-right", "top-center", "none"
    "logo_height": 80,
    "logo_margin": 50,

    # Gradient
    "gradient_strength": 0.8,  # 0-1, bottom gradient opacity

    # Variations to generate
    "variations": 3,
}


def _load_config() -> dict:
    """Load thumbnail config from .podcli/thumbnail-config.json, merged with defaults."""
    config = {**DEFAULT_CONFIG}
    config_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "..", ".podcli", "thumbnail-config.json"
    )
    if os.path.exists(config_path):
        try:
            with open(config_path) as f:
                user = json.load(f)
            config.update(user)
        except (json.JSONDecodeError, IOError):
            pass
    return config


def _get_font(size: int, bold: bool = True, italic: bool = False) -> ImageFont.FreeTypeFont:
    """Get the best available font. Tries system fonts, falls back to default."""
    font_candidates = []

    if bold and italic:
        font_candidates += [
            "/System/Library/Fonts/Helvetica.ttc",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-BoldOblique.ttf",
        ]
    elif bold:
        font_candidates += [
            "/System/Library/Fonts/SFCompact.ttf",
            "/System/Library/Fonts/Helvetica.ttc",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        ]
    elif italic:
        font_candidates += [
            "/System/Library/Fonts/Helvetica.ttc",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Oblique.ttf",
        ]

    # Always try Arial as fallback
    font_candidates += [
        "/Library/Fonts/Arial Bold.ttf",
        "/Library/Fonts/Arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]

    for path in font_candidates:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue

    return ImageFont.load_default()


def _wrap_text(text: str, max_chars: int) -> list[str]:
    """Wrap text into lines of max_chars, breaking at word boundaries."""
    words = text.split()
    lines = []
    current = ""
    for word in words:
        test = f"{current} {word}".strip()
        if len(test) <= max_chars:
            current = test
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def _draw_gradient(img: Image.Image, strength: float = 0.8) -> Image.Image:
    """Draw a bottom-to-top dark gradient overlay."""
    w, h = img.size
    gradient = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(gradient)

    # Bottom 60% gets gradient
    start_y = int(h * 0.4)
    for y in range(start_y, h):
        progress = (y - start_y) / (h - start_y)
        alpha = int(255 * strength * progress)
        draw.line([(0, y), (w, y)], fill=(0, 0, 0, alpha))

    # Also light gradient at top for logo readability
    for y in range(0, int(h * 0.15)):
        progress = 1 - (y / (h * 0.15))
        alpha = int(100 * strength * progress)
        draw.line([(0, y), (w, y)], fill=(0, 0, 0, alpha))

    return Image.alpha_composite(img.convert("RGBA"), gradient)


def _hex_to_rgb(hex_color: str) -> tuple:
    """Convert hex color to RGB tuple."""
    hex_color = hex_color.lstrip("#")
    return tuple(int(hex_color[i:i+2], 16) for i in (0, 2, 4))


def generate_thumbnail(
    title_line1: str,
    title_line2: str,
    output_path: str,
    guest_photo_path: Optional[str] = None,
    logo_path: Optional[str] = None,
    config: Optional[dict] = None,
    variation: int = 0,
) -> str:
    """
    Generate a single thumbnail image.

    Args:
        title_line1: Top line of title text (e.g., "WHY DATA CENTERS")
        title_line2: Bottom line with accent (e.g., "MUST LEAVE EARTH")
        output_path: Where to save the PNG
        guest_photo_path: Optional guest photo (fills background)
        logo_path: Optional logo (top corner)
        config: Override config dict (merged with defaults)
        variation: Variation index (0, 1, 2) — changes layout slightly

    Returns:
        Path to generated thumbnail
    """
    cfg = _load_config()
    if config:
        cfg.update(config)

    w, h = cfg["width"], cfg["height"]
    bg_color = _hex_to_rgb(cfg["bg_color"])
    text_color = _hex_to_rgb(cfg["text_color"])
    accent_color = _hex_to_rgb(cfg.get("line2_color") or cfg["accent_color"])

    # Create base image
    img = Image.new("RGBA", (w, h), (*bg_color, 255))

    # Guest photo as background
    if guest_photo_path and os.path.exists(guest_photo_path):
        try:
            photo = Image.open(guest_photo_path).convert("RGBA")

            # Resize to fill width, position based on config
            photo_ratio = photo.width / photo.height
            target_ratio = w / h

            if photo_ratio > target_ratio:
                new_h = h
                new_w = int(new_h * photo_ratio)
            else:
                new_w = w
                new_h = int(new_w / photo_ratio)

            photo = photo.resize((new_w, new_h), Image.LANCZOS)

            # Position
            x_offset = (w - new_w) // 2
            if cfg["photo_position"] == "top":
                y_offset = 0
            elif cfg["photo_position"] == "bottom":
                y_offset = h - new_h
            else:
                y_offset = (h - new_h) // 2

            # Variation: shift photo position slightly
            if variation == 1:
                y_offset -= int(h * 0.05)
            elif variation == 2:
                y_offset += int(h * 0.05)

            # Darken + blur
            enhancer = ImageEnhance.Brightness(photo)
            photo = enhancer.enhance(cfg["photo_darken"])
            if cfg["photo_blur"] > 0:
                photo = photo.filter(ImageFilter.GaussianBlur(cfg["photo_blur"]))

            img.paste(photo, (x_offset, y_offset))
        except Exception:
            pass  # Failed to load photo, continue with solid bg

    # Gradient overlay
    img = _draw_gradient(img, cfg["gradient_strength"])

    draw = ImageDraw.Draw(img)

    # Logo
    if logo_path and os.path.exists(logo_path) and cfg["logo_position"] != "none":
        try:
            logo = Image.open(logo_path).convert("RGBA")
            logo_h = cfg["logo_height"]
            logo_w = int(logo.width * (logo_h / logo.height))
            logo = logo.resize((logo_w, logo_h), Image.LANCZOS)

            margin = cfg["logo_margin"]
            if cfg["logo_position"] == "top-left":
                logo_x, logo_y = margin, margin
            elif cfg["logo_position"] == "top-right":
                logo_x, logo_y = w - logo_w - margin, margin
            elif cfg["logo_position"] == "top-center":
                logo_x, logo_y = (w - logo_w) // 2, margin
            else:
                logo_x, logo_y = margin, margin

            img.paste(logo, (logo_x, logo_y), logo)
        except Exception:
            pass

    # Title text
    font1 = _get_font(cfg["font_size_line1"], bold=True)
    font2 = _get_font(cfg["font_size_line2"], bold=True, italic=cfg.get("line2_italic", True))

    # Process line1
    line1_text = title_line1.upper() if cfg["line1_uppercase"] else title_line1
    line1_lines = _wrap_text(line1_text, cfg["max_chars_per_line"])

    # Process line2
    line2_text = title_line2
    line2_lines = _wrap_text(line2_text, cfg["max_chars_per_line"])

    # Calculate text block height
    line_spacing = 12
    total_lines = line1_lines + line2_lines
    line_heights = []
    for i, line in enumerate(total_lines):
        font = font1 if i < len(line1_lines) else font2
        bbox = draw.textbbox((0, 0), line, font=font)
        line_heights.append(bbox[3] - bbox[1])

    total_text_h = sum(line_heights) + line_spacing * (len(total_lines) - 1)

    # Text Y position based on config + variation
    if cfg["text_position"] == "lower":
        base_y = int(h * 0.68)
    elif cfg["text_position"] == "center":
        base_y = (h - total_text_h) // 2
    else:  # upper
        base_y = int(h * 0.25)

    # Variation shifts
    if variation == 1:
        base_y -= int(h * 0.05)
    elif variation == 2:
        base_y += int(h * 0.03)

    # Draw each line
    y = base_y
    for i, line in enumerate(total_lines):
        is_line2 = i >= len(line1_lines)
        font = font2 if is_line2 else font1
        color = accent_color if is_line2 else text_color

        bbox = draw.textbbox((0, 0), line, font=font)
        text_w = bbox[2] - bbox[0]
        text_h = bbox[3] - bbox[1]
        x = (w - text_w) // 2

        # Line2 gets a highlight background box
        if is_line2:
            pad_x, pad_y = 16, 8
            highlight_bg = _hex_to_rgb(cfg.get("highlight_bg", cfg["bg_color"]))
            # Draw rounded-ish rectangle (PIL doesn't have rounded_rectangle in older versions)
            try:
                draw.rounded_rectangle(
                    [x - pad_x, y - pad_y, x + text_w + pad_x, y + text_h + pad_y],
                    radius=10,
                    fill=(*highlight_bg, 220),
                )
            except AttributeError:
                # Older Pillow without rounded_rectangle
                draw.rectangle(
                    [x - pad_x, y - pad_y, x + text_w + pad_x, y + text_h + pad_y],
                    fill=(*highlight_bg, 220),
                )

        # Draw text
        draw.text((x, y), line, fill=(*color, 255), font=font)
        y += text_h + line_spacing

    # Save
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    img.save(output_path, "PNG")
    return output_path


def generate_variations(
    title: str,
    output_dir: str,
    guest_photo_path: Optional[str] = None,
    logo_path: Optional[str] = None,
    config: Optional[dict] = None,
) -> list[str]:
    """
    Generate multiple thumbnail variations for a clip.

    Splits title into two lines and generates variations with different:
    - Text positions (lower, center, shifted)
    - Photo positions (centered, shifted up/down)

    Returns list of generated file paths.
    """
    cfg = _load_config()
    if config:
        cfg.update(config)

    # Split title into two lines
    words = title.split()
    mid = len(words) // 2
    if mid == 0:
        mid = 1
    line1 = " ".join(words[:mid])
    line2 = " ".join(words[mid:])

    os.makedirs(output_dir, exist_ok=True)
    paths = []

    n_variations = cfg.get("variations", 3)
    for i in range(n_variations):
        filename = f"thumb_v{i+1}.png"
        path = os.path.join(output_dir, filename)
        generate_thumbnail(
            title_line1=line1,
            title_line2=line2,
            output_path=path,
            guest_photo_path=guest_photo_path,
            logo_path=logo_path,
            config=cfg,
            variation=i,
        )
        paths.append(path)

    return paths


def thumbnail_to_video_frame(
    thumbnail_path: str,
    output_path: str,
    duration: float = 2.0,
    fade_in: float = 0.3,
    fade_out: float = 0.3,
    width: int = 1080,
    height: int = 1920,
) -> str:
    """
    Convert a thumbnail image to a short video clip with fade in/out.

    Used to append thumbnail as the last 2 seconds of a short video.
    """
    import subprocess

    cmd = [
        "ffmpeg", "-y",
        "-loop", "1",
        "-i", thumbnail_path,
        "-t", str(duration),
        "-vf", (
            f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black,"
            f"fade=t=in:st=0:d={fade_in},"
            f"fade=t=out:st={duration - fade_out}:d={fade_out}"
        ),
        "-c:v", "libx264", "-crf", "18", "-preset", "fast",
        "-pix_fmt", "yuv420p",
        "-an",  # No audio for the thumbnail frame
        output_path,
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"Thumbnail to video failed: {result.stderr[-300:]}")
    return output_path
