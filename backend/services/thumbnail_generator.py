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

    # Colors — change these to match your brand
    "bg_color": "#1A1A1A",
    "text_color": "#FFFFFF",
    "accent_color": "#00CED1",

    # Text
    "font_size_line1": 80,
    "font_size_line2": 80,
    "line1_uppercase": True,
    "line1_bold": True,
    "line2_uppercase": True,
    "line2_bold": True,
    "line2_italic": True,
    "line2_color": None,           # None = accent_color
    "line2_style": "highlight",    # "highlight" = dark text on accent bg, "colored" = accent text
    "max_chars_per_line": 16,
    "text_y": 0.72,               # 0-1 vertical position

    # Text box — set "box_enabled" false for floating text
    "box_enabled": True,
    "box_border_color": None,      # None = accent_color
    "box_border_width": 4,
    "box_fill_color": "#0D0D0DE0", # Dark fill, E0 = 88% opacity
    "box_padding_x": 50,
    "box_padding_y": 30,
    "box_radius": 0,               # 0 = sharp corners, >0 = rounded

    # Guest photo
    "photo_brightness": 0.85,      # 1.0 = unchanged, 0 = black
    "photo_blur": 0,               # 0 = sharp, >0 = gaussian blur px
    "photo_position": "center",    # center, top, bottom
    "photo_zoom": 1.0,             # 1.0 = fill, >1 = zoom in on face

    # Gradient overlays (0 = off, 1 = full black)
    "gradient_bottom": 0.85,       # Dark gradient at bottom (text area)
    "gradient_top": 0.4,           # Subtle gradient at top (logo area)

    # Logo / Watermark
    "logo_position": "bottom-center",  # top-left, top-right, top-center, bottom-center, none
    "logo_height": 60,
    "logo_margin": 50,
    "logo_opacity": 0.7,           # 0-1

    # Auto face extraction
    "auto_face_frame": True,       # Extract best face frame from video automatically
    "face_frame_count": 10,        # How many frames to sample

    # Frame border (around entire image)
    "frame_border_width": 4,       # 0 = no frame
    "frame_border_color": None,    # None = accent_color

    # Variations
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
    """Get the best available font. Uses Helvetica Neue for clean look."""

    # TTC files need an index to select the right face
    ttc_options = []

    if bold and italic:
        ttc_options = [
            ("/System/Library/Fonts/HelveticaNeue.ttc", 3),    # Helvetica Neue Bold Italic
            ("/System/Library/Fonts/Helvetica.ttc", 3),
        ]
    elif bold:
        ttc_options = [
            ("/System/Library/Fonts/HelveticaNeue.ttc", 1),    # Helvetica Neue Bold
            ("/System/Library/Fonts/Helvetica.ttc", 1),
        ]
    elif italic:
        ttc_options = [
            ("/System/Library/Fonts/HelveticaNeue.ttc", 2),    # Helvetica Neue Italic
            ("/System/Library/Fonts/Helvetica.ttc", 2),
        ]
    else:
        ttc_options = [
            ("/System/Library/Fonts/HelveticaNeue.ttc", 0),    # Helvetica Neue Regular
            ("/System/Library/Fonts/Helvetica.ttc", 0),
        ]

    for path, idx in ttc_options:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size, index=idx)
            except Exception:
                continue

    # Fallback to standalone font files
    fallbacks = []
    if bold:
        fallbacks = [
            "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
            "/Library/Fonts/Arial Bold.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        ]
    else:
        fallbacks = [
            "/System/Library/Fonts/Supplemental/Arial.ttf",
            "/Library/Fonts/Arial.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        ]

    for path in fallbacks:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue

    return ImageFont.load_default()


def _wrap_text(text: str, max_chars: int, font=None, max_width: int = 0) -> list[str]:
    """Wrap text into lines, using pixel width if font provided, otherwise char count."""
    words = text.split()
    lines = []
    current = ""

    if font and max_width > 0:
        # Pixel-accurate wrapping using actual font metrics
        from PIL import ImageDraw, Image
        _measure = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
        for word in words:
            test = f"{current} {word}".strip()
            bbox = _measure.textbbox((0, 0), test, font=font)
            if (bbox[2] - bbox[0]) <= max_width:
                current = test
            else:
                if current:
                    lines.append(current)
                current = word
    else:
        # Fallback: char-count wrapping
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


def _draw_gradients(img: Image.Image, bottom: float = 0.8, top: float = 0.0) -> Image.Image:
    """Draw configurable gradient overlays (bottom and/or top)."""
    w, h = img.size
    gradient = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(gradient)

    # Bottom gradient (for text readability)
    if bottom > 0:
        start_y = int(h * 0.4)
        for y in range(start_y, h):
            progress = (y - start_y) / (h - start_y)
            alpha = int(255 * bottom * progress)
            draw.line([(0, y), (w, y)], fill=(0, 0, 0, alpha))

    # Top gradient (for logo readability)
    if top > 0:
        end_y = int(h * 0.15)
        for y in range(0, end_y):
            progress = 1 - (y / end_y)
            alpha = int(255 * top * progress)
            draw.line([(0, y), (w, y)], fill=(0, 0, 0, alpha))

    return Image.alpha_composite(img.convert("RGBA"), gradient)


def extract_face_frame(
    video_path: str,
    output_path: str,
    sample_count: int = 10,
    target_width: int = 1080,
    target_height: int = 1920,
) -> Optional[dict]:
    """
    Extract the best face frame from a video for use as thumbnail background.

    Samples frames, detects faces, picks the frame with the largest/clearest face.
    Saves the FULL frame (no crop) — the HTML template's CSS `background: cover`
    handles the 9:16 crop so we get a natural chest-up portrait instead of an
    over-zoomed face slice.

    Returns dict with 'path' and 'face_x' (face center as 0-100%), or None.
    """
    try:
        import cv2
        import numpy as np

        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            return None

        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        fps = cap.get(cv2.CAP_PROP_FPS) or 30
        duration = total_frames / fps

        # Load face detector
        backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        proto = os.path.join(backend_dir, "models", "deploy.prototxt")
        model = os.path.join(backend_dir, "models", "res10_300x300_ssd_iter_140000.caffemodel")

        if not (os.path.exists(proto) and os.path.exists(model)):
            cap.release()
            return None

        detector = cv2.dnn.readNetFromCaffe(proto, model)

        best_frame = None
        best_score = 0
        best_face_cx = None
        best_face_cy = None
        best_face_w = 0
        best_face_h = 0

        # Sample frames evenly (skip first/last 10% to avoid intro/outro)
        start_t = duration * 0.1
        end_t = duration * 0.9
        sample_times = [start_t + i * (end_t - start_t) / sample_count for i in range(sample_count)]

        for t in sample_times:
            cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
            ret, frame = cap.read()
            if not ret:
                continue

            h, w = frame.shape[:2]
            blob = cv2.dnn.blobFromImage(cv2.resize(frame, (300, 300)), 1.0, (300, 300), (104.0, 177.0, 123.0))
            detector.setInput(blob)
            detections = detector.forward()

            for j in range(detections.shape[2]):
                conf = detections[0, 0, j, 2]
                if conf > 0.6:
                    x1 = int(detections[0, 0, j, 3] * w)
                    y1 = int(detections[0, 0, j, 4] * h)
                    x2 = int(detections[0, 0, j, 5] * w)
                    y2 = int(detections[0, 0, j, 6] * h)
                    face_w = x2 - x1
                    face_h = y2 - y1

                    if face_w < w * 0.08:
                        continue

                    # Score: confidence × face size (prefer larger/closer faces)
                    score = conf * (face_w * face_h)
                    if score > best_score:
                        best_score = score
                        best_frame = frame.copy()
                        best_face_cx = (x1 + x2) // 2
                        best_face_cy = (y1 + y2) // 2
                        best_face_w = face_w
                        best_face_h = face_h

        cap.release()

        if best_frame is None:
            return None

        h, w = best_frame.shape[:2]

        # Crop a vertical (9:16) slice centered on the face.
        # The HTML template shows the photo in the top ~78% (1498px of 1920px),
        # so we need the face well-centered in that visible zone.
        target_ratio = target_width / target_height  # 0.5625

        crop_h = h
        crop_w = int(crop_h * target_ratio)
        if crop_w > w:
            crop_w = w
            crop_h = int(crop_w / target_ratio)

        # Center horizontally on face exactly
        crop_x = best_face_cx - crop_w // 2
        crop_x = max(0, min(crop_x, w - crop_w))

        # Place face at ~25% from top so it's centered in the visible photo
        # area (top 78% of the final thumbnail). This shows head + shoulders
        # naturally without over-cropping.
        crop_y = best_face_cy - int(crop_h * 0.25)
        crop_y = max(0, min(crop_y, h - crop_h))

        cropped = best_frame[crop_y:crop_y + crop_h, crop_x:crop_x + crop_w]
        resized = cv2.resize(cropped, (target_width, target_height), interpolation=cv2.INTER_LANCZOS4)

        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        cv2.imwrite(output_path, resized)

        # Calculate actual face position in the cropped/resized frame
        actual_face_x = round((best_face_cx - crop_x) / crop_w * 100, 1)
        actual_face_y = round((best_face_cy - crop_y) / crop_h * 100, 1)
        actual_face_w = round(best_face_w / crop_w * 100, 1)
        actual_face_h = round(best_face_h / crop_h * 100, 1)
        return {
            "path": output_path,
            "face_x_pct": actual_face_x,
            "face_y_pct": actual_face_y,
            "face_w_pct": actual_face_w,
            "face_h_pct": actual_face_h,
        }

    except ImportError:
        return None
    except Exception:
        return None


def _hex_to_rgb(hex_color: str) -> tuple:
    """Convert hex color to RGB tuple."""
    hex_color = hex_color.lstrip("#")
    return tuple(int(hex_color[i:i+2], 16) for i in (0, 2, 4))


def _hex_to_rgba(hex_color: str) -> tuple:
    """Convert hex color (with optional alpha like #RRGGBBAA) to RGBA tuple."""
    hex_color = hex_color.lstrip("#")
    if len(hex_color) == 8:
        r, g, b, a = int(hex_color[0:2], 16), int(hex_color[2:4], 16), int(hex_color[4:6], 16), int(hex_color[6:8], 16)
        return (r, g, b, a)
    elif len(hex_color) == 6:
        return (*_hex_to_rgb(hex_color), 255)
    return (0, 0, 0, 255)


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
    border_color = _hex_to_rgb(cfg.get("box_border_color") or cfg["accent_color"])

    # ── Layer 0: Frame border ──
    frame_border = cfg.get("frame_border_width", 4)
    frame_color = _hex_to_rgb(cfg.get("frame_border_color") or cfg["accent_color"])

    # ── Layer 1: Background ──
    img = Image.new("RGBA", (w, h), (*bg_color, 255))

    # ── Layer 2: Guest photo ──
    if guest_photo_path and os.path.exists(guest_photo_path):
        try:
            photo = Image.open(guest_photo_path).convert("RGBA")
            photo_ratio = photo.width / photo.height
            target_ratio = w / h

            zoom = cfg.get("photo_zoom", 1.0)
            if photo_ratio > target_ratio:
                new_h = int(h * zoom)
                new_w = int(new_h * photo_ratio)
            else:
                new_w = int(w * zoom)
                new_h = int(new_w / photo_ratio)

            photo = photo.resize((new_w, new_h), Image.LANCZOS)

            x_offset = (w - new_w) // 2
            pos = cfg["photo_position"]
            if pos == "top":
                y_offset = 0
            elif pos == "bottom":
                y_offset = h - new_h
            else:
                y_offset = (h - new_h) // 2

            # Variation: slight shift
            if variation == 1:
                y_offset -= int(h * 0.04)
            elif variation == 2:
                y_offset += int(h * 0.04)

            # Brightness + blur
            brightness = cfg.get("photo_brightness", 0.85)
            if brightness != 1.0:
                photo = ImageEnhance.Brightness(photo).enhance(brightness)
            blur = cfg.get("photo_blur", 0)
            if blur > 0:
                photo = photo.filter(ImageFilter.GaussianBlur(blur))

            img.paste(photo, (x_offset, y_offset))
        except Exception:
            pass

    # ── Layer 3: Gradient overlays ──
    img = _draw_gradients(img, bottom=cfg.get("gradient_bottom", 0.8), top=cfg.get("gradient_top", 0.0))

    draw = ImageDraw.Draw(img)

    # ── Layer 4: Text box + text ──
    # Safe text area: 80% of image width with margins
    safe_margin = int(w * 0.08)
    max_text_w = w - safe_margin * 2

    font1 = _get_font(cfg["font_size_line1"], bold=cfg.get("line1_bold", True))
    font2 = _get_font(cfg["font_size_line2"], bold=cfg.get("line2_bold", True), italic=cfg.get("line2_italic", True))

    l1_text = title_line1.upper() if cfg.get("line1_uppercase", True) else title_line1
    l2_text = title_line2.upper() if cfg.get("line2_uppercase", True) else title_line2

    # Pixel-aware wrapping with auto-shrink if text still overflows
    def _wrap_and_fit(text, font, font_size, bold=True, italic=False):
        """Wrap text using pixel width, shrink font if any single word is too wide."""
        current_font = font
        current_size = font_size
        for _ in range(5):  # max 5 shrink attempts
            lines = _wrap_text(text, cfg["max_chars_per_line"], font=current_font, max_width=max_text_w)
            # Check if any line overflows
            overflow = False
            for line in lines:
                bbox = draw.textbbox((0, 0), line, font=current_font)
                if (bbox[2] - bbox[0]) > max_text_w:
                    overflow = True
                    break
            if not overflow:
                return lines, current_font
            # Shrink font by 15%
            current_size = max(28, int(current_size * 0.85))
            current_font = _get_font(current_size, bold=bold, italic=italic)
        return lines, current_font

    l1_lines, font1 = _wrap_and_fit(l1_text, font1, cfg["font_size_line1"], bold=cfg.get("line1_bold", True))
    l2_lines, font2 = _wrap_and_fit(l2_text, font2, cfg["font_size_line2"], bold=cfg.get("line2_bold", True), italic=cfg.get("line2_italic", True))

    line2_style = cfg.get("line2_style", "highlight")
    hl_pad_x, hl_pad_y = 16, 8

    # Measure Line 1 block
    l1_sizes = []
    for line in l1_lines:
        bbox = draw.textbbox((0, 0), line, font=font1)
        l1_sizes.append((bbox[2] - bbox[0], bbox[3] - bbox[1]))

    # Measure Line 2 block
    l2_sizes = []
    for line in l2_lines:
        bbox = draw.textbbox((0, 0), line, font=font2)
        l2_sizes.append((bbox[2] - bbox[0], bbox[3] - bbox[1]))

    l1_line_gap = 10
    block_gap = 24  # Clear gap between Line 1 and Line 2

    l1_block_h = sum(s[1] for s in l1_sizes) + l1_line_gap * max(0, len(l1_sizes) - 1)
    l2_block_h = sum(s[1] + hl_pad_y * 2 for s in l2_sizes) if line2_style == "highlight" else sum(s[1] for s in l2_sizes)
    total_content_h = l1_block_h + block_gap + l2_block_h

    # Max width for box sizing — clamp to image bounds
    all_widths = [s[0] for s in l1_sizes] + [s[0] + hl_pad_x * 2 for s in l2_sizes]
    max_content_w = min(max(all_widths) if all_widths else 400, max_text_w)

    # Vertical position + variation shift
    text_y_ratio = cfg.get("text_y", 0.72)
    if variation == 1:
        text_y_ratio -= 0.04
    elif variation == 2:
        text_y_ratio += 0.03
    content_top = int(h * text_y_ratio) - total_content_h // 2
    # Clamp: keep text within image bounds
    content_top = max(10, min(content_top, h - total_content_h - 10))

    # Draw text box
    box_enabled = cfg.get("box_enabled", True)
    if box_enabled:
        pad_x = cfg.get("box_padding_x", 50)
        pad_y = cfg.get("box_padding_y", 35)
        box_l = max(0, (w - max_content_w) // 2 - pad_x)
        box_t = max(0, content_top - pad_y)
        box_r = min(w, (w + max_content_w) // 2 + pad_x)
        box_b = min(h, content_top + total_content_h + pad_y)

        fill_rgba = _hex_to_rgba(cfg.get("box_fill_color", "#0D0D0DE0"))
        border_w = cfg.get("box_border_width", 4)
        radius = cfg.get("box_radius", 0)

        try:
            if radius > 0:
                draw.rounded_rectangle([box_l, box_t, box_r, box_b], radius=radius, fill=fill_rgba, outline=(*border_color, 255), width=border_w)
            else:
                draw.rectangle([box_l, box_t, box_r, box_b], fill=fill_rgba, outline=(*border_color, 255), width=border_w)
        except TypeError:
            draw.rectangle([box_l, box_t, box_r, box_b], fill=fill_rgba)

    # Draw Line 1 — white text, no background
    y = content_top
    for i, line in enumerate(l1_lines):
        tw, th = l1_sizes[i]
        x = (w - tw) // 2
        draw.text((x, y), line, fill=(*text_color, 255), font=font1)
        y += th + l1_line_gap

    # Gap between blocks
    y = content_top + l1_block_h + block_gap

    # Draw Line 2 — dark text on accent highlight, or accent text
    dark_text = _hex_to_rgb(cfg.get("bg_color", "#0D0D0D"))
    for i, line in enumerate(l2_lines):
        tw, th = l2_sizes[i]
        x = (w - tw) // 2

        if line2_style == "highlight":
            draw.rectangle(
                [x - hl_pad_x, y - hl_pad_y, x + tw + hl_pad_x, y + th + hl_pad_y],
                fill=(*accent_color, 255),
            )
            draw.text((x, y), line, fill=(*dark_text, 255), font=font2)
            y += th + hl_pad_y * 2
        else:
            draw.text((x, y), line, fill=(*accent_color, 255), font=font2)
            y += th

    # ── Layer 5: Logo / Watermark ──
    if logo_path and os.path.exists(logo_path) and cfg.get("logo_position", "none") != "none":
        try:
            logo = Image.open(logo_path).convert("RGBA")
            logo_h = cfg.get("logo_height", 60)
            logo_w = int(logo.width * (logo_h / logo.height))
            logo = logo.resize((logo_w, logo_h), Image.LANCZOS)

            # Apply opacity
            opacity = cfg.get("logo_opacity", 0.7)
            if opacity < 1.0:
                alpha = logo.split()[3]
                alpha = alpha.point(lambda p: int(p * opacity))
                logo.putalpha(alpha)

            margin = cfg.get("logo_margin", 50)
            pos = cfg["logo_position"]
            if pos == "top-left":
                lx, ly = margin, margin
            elif pos == "top-right":
                lx, ly = w - logo_w - margin, margin
            elif pos == "top-center":
                lx, ly = (w - logo_w) // 2, margin
            elif pos == "bottom-center":
                lx, ly = (w - logo_w) // 2, h - logo_h - margin
            elif pos == "bottom-left":
                lx, ly = margin, h - logo_h - margin
            elif pos == "bottom-right":
                lx, ly = w - logo_w - margin, h - logo_h - margin
            else:
                lx, ly = margin, margin

            img.paste(logo, (lx, ly), logo)
        except Exception:
            pass

    # ── Layer 6: Frame border around entire image ──
    if frame_border > 0:
        frame_draw = ImageDraw.Draw(img)
        fb = frame_border
        frame_draw.rectangle([fb//2, fb//2, w - fb//2, h - fb//2], outline=(*frame_color, 255), width=fb)

    # Save
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    img.save(output_path, "PNG")
    return output_path


def generate_variations(
    title: str,
    output_dir: str,
    guest_photo_path: Optional[str] = None,
    video_path: Optional[str] = None,
    logo_path: Optional[str] = None,
    config: Optional[dict] = None,
) -> list[str]:
    """
    Generate multiple thumbnail variations for a clip.

    If no guest photo provided but video_path is given, auto-extracts the best
    face frame from the video.

    Splits title into two lines and generates variations with different:
    - Text positions (shifted up/down)
    - Photo positions (shifted)

    Returns list of generated file paths.
    """
    cfg = _load_config()
    if config:
        cfg.update(config)

    os.makedirs(output_dir, exist_ok=True)

    # Auto-extract face frame from video if no photo provided
    if not guest_photo_path and video_path and cfg.get("auto_face_frame", True):
        frame_path = os.path.join(output_dir, "_face_frame.png")
        result = extract_face_frame(
            video_path, frame_path,
            sample_count=cfg.get("face_frame_count", 10),
            target_width=cfg["width"],
            target_height=cfg["height"],
        )
        if result:
            guest_photo_path = result["path"]

    # Split title into two lines (smart split — try to balance)
    words = title.split()
    if len(words) <= 2:
        line1 = words[0] if words else ""
        line2 = " ".join(words[1:])
    else:
        # Try splits and pick the most balanced one
        best_split = len(words) // 2
        best_diff = float("inf")
        for split_at in range(1, len(words)):
            l1 = " ".join(words[:split_at])
            l2 = " ".join(words[split_at:])
            diff = abs(len(l1) - len(l2))
            if diff < best_diff:
                best_diff = diff
                best_split = split_at
        line1 = " ".join(words[:best_split])
        line2 = " ".join(words[best_split:])

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
    duration: float = 0.8,
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
