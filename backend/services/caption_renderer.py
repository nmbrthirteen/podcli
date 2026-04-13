"""
ASS (Advanced SubStation Alpha) subtitle generator.

Generates styled caption files that FFmpeg can burn into video.
Supports styles: Hormozi, Karaoke, Subtle, and Branded.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.caption_styles import get_style
from utils.timing_utils import seconds_to_ass


def generate_ass_header(style: dict, play_res_x: int = 1080, play_res_y: int = 1920) -> str:
    """Generate the ASS file header with style definitions."""
    bold_val = -1 if style["bold"] else 0
    border_style = style.get("border_style", 1)

    return f"""[Script Info]
Title: Podcast Clip Captions
ScriptType: v4.00+
PlayResX: {play_res_x}
PlayResY: {play_res_y}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{style["font_name"]},{style["font_size"]},{style["primary_color"]},{style["primary_color"]},{style["outline_color"]},{style["back_color"]},{bold_val},0,0,0,100,100,0,0,{border_style},{style["outline_width"]},{style["shadow_depth"]},{style["alignment"]},40,40,{style["margin_v"]},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def generate_branded_header(style: dict, play_res_x: int = 1080, play_res_y: int = 1920) -> str:
    """
    Generate ASS header for branded style.
    Uses BrandedNormal style: bold white text with inline box overrides on active word.
    """
    return f"""[Script Info]
Title: Podcast Clip Captions (Branded)
ScriptType: v4.00+
PlayResX: {play_res_x}
PlayResY: {play_res_y}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: BrandedNormal,{style["font_name"]},{style["font_size"]},&H00FFFFFF,&H00FFFFFF,&H90000000,&H00000000,-1,0,0,0,100,100,2,0,1,1,1,{style["alignment"]},60,60,{style["margin_v"]},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


MIN_WORD_DURATION = 0.05  # 50ms minimum per word


def _sanitize_words(words: list[dict]) -> list[dict]:
    """
    Fix timing edge cases before rendering:
    - Enforce minimum word duration (50ms)
    - Remove words with empty text
    - Ensure end > start
    """
    cleaned = []
    for w in words:
        text = (w.get("word") or "").strip()
        if not text:
            continue

        start = float(w.get("start", 0))
        end = float(w.get("end", 0))

        # Ensure end > start with minimum duration
        if end <= start:
            end = start + MIN_WORD_DURATION
        elif (end - start) < MIN_WORD_DURATION:
            end = start + MIN_WORD_DURATION

        cleaned.append({**w, "word": text, "start": start, "end": end})

    return cleaned


def render_captions(
    words: list[dict],
    caption_style: str,
    output_path: str,
    time_offset: float = 0.0,
) -> str:
    """
    Generate an ASS subtitle file from word-level timestamps.

    Args:
        words: List of {word, start, end, confidence} dicts
        caption_style: "hormozi", "karaoke", "subtle", or "branded"
        output_path: Where to write the .ass file
        time_offset: Subtract this from all timestamps (for clip segments)

    Returns:
        Path to the generated .ass file
    """
    words = _sanitize_words(words)
    if not words:
        # Write empty subtitle file rather than crashing
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(generate_ass_header(get_style(caption_style)))
        return output_path

    style = get_style(caption_style)

    if caption_style == "hormozi":
        content = _render_hormozi(words, style, time_offset)
    elif caption_style == "karaoke":
        content = _render_karaoke(words, style, time_offset)
    elif caption_style == "subtle":
        content = _render_subtle(words, style, time_offset)
    elif caption_style == "branded":
        content = _render_branded(words, style, time_offset)
    else:
        raise ValueError(f"Unknown style: {caption_style}")

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(content)

    return output_path


def _render_hormozi(words: list[dict], style: dict, offset: float) -> str:
    """
    Hormozi style: Show 2-3 words at a time, smooth karaoke-fill highlight.
    Uses \\kf tags for progressive word fill — 1 Dialogue per chunk, no flashing.
    """
    header = generate_ass_header(style)
    events = []
    chunk_size = style["words_per_chunk"]
    uppercase = style["uppercase"]

    chunks = []
    for i in range(0, len(words), chunk_size):
        chunk = words[i : i + chunk_size]
        chunks.append(chunk)

    for chunk in chunks:
        if not chunk:
            continue

        chunk_start = max(0, chunk[0]["start"] - offset)
        chunk_end = max(0, chunk[-1]["end"] - offset)

        # Build \kf karaoke-fill parts: each word fills progressively
        parts = []
        for w in chunk:
            duration_cs = int((w["end"] - w["start"]) * 100)
            text = w["word"].upper() if uppercase else w["word"]
            parts.append(f"{{\\kf{duration_cs}}}{text}")

        # \c = active (filled) color, \2c = inactive (unfilled) color
        line_text = (
            f"{{\\c{style['active_color']}\\2c{style['primary_color']}}}"
            + " ".join(parts)
        )

        start_ts = seconds_to_ass(chunk_start)
        end_ts = seconds_to_ass(chunk_end)

        events.append(
            f"Dialogue: 0,{start_ts},{end_ts},Default,,0,0,0,,{line_text}"
        )

    return header + "\n".join(events) + "\n"


def _render_karaoke(words: list[dict], style: dict, offset: float) -> str:
    """
    Karaoke style: Full sentence visible, words highlight as spoken.
    """
    header = generate_ass_header(style)
    events = []

    sentence_size = style.get("words_per_chunk", 5)
    sentences = []
    for i in range(0, len(words), sentence_size):
        sentences.append(words[i : i + sentence_size])

    for sentence in sentences:
        if not sentence:
            continue

        sent_start = max(0, sentence[0]["start"] - offset)
        sent_end = max(0, sentence[-1]["end"] - offset)

        parts = []
        for w in sentence:
            duration_cs = int((w["end"] - w["start"]) * 100)
            text = w["word"]
            parts.append(f"{{\\kf{duration_cs}}}{text}")

        line_text = " ".join(parts)
        line_text = (
            f"{{\\c{style['active_color']}}}{{\\2c{style['primary_color']}}}" + line_text
        )

        start_ts = seconds_to_ass(sent_start)
        end_ts = seconds_to_ass(sent_end)

        events.append(f"Dialogue: 0,{start_ts},{end_ts},Default,,0,0,0,,{line_text}")

    return header + "\n".join(events) + "\n"


def _render_subtle(words: list[dict], style: dict, offset: float) -> str:
    """
    Subtle style: Clean white subtitles at bottom, sentence-level timing.
    """
    header = generate_ass_header(style)
    events = []

    line_size = style.get("words_per_chunk", 5)
    lines = []
    for i in range(0, len(words), line_size):
        lines.append(words[i : i + line_size])

    for line_words in lines:
        if not line_words:
            continue

        line_start = max(0, line_words[0]["start"] - offset)
        line_end = max(0, line_words[-1]["end"] - offset)

        line_text = " ".join(w["word"] for w in line_words)

        start_ts = seconds_to_ass(line_start)
        end_ts = seconds_to_ass(line_end)

        events.append(f"Dialogue: 0,{start_ts},{end_ts},Default,,0,0,0,,{line_text}")

    return header + "\n".join(events) + "\n"


def _normalize_case(text: str) -> str:
    """Lowercase a word unless it's an acronym or proper 'I'."""
    stripped = text.strip(".,!?;:-–—'\"")
    if stripped.isupper() and len(stripped) >= 2:
        return text  # Likely acronym (e.g. "AI", "CEO")
    if stripped == "I" or stripped == "I'm" or stripped.startswith("I'"):
        return text
    return text.lower()


_libass_calibration_cache: dict[str, list[int]] = {}


def _calibrate_libass_y(
    font_name: str, font_size: int, bold: bool, margin_v: int,
    play_res_x: int, play_res_y: int,
    lines: list, space_width: int, line_height: int,
) -> list[int]:
    """
    Render a probe frame with FFmpeg+libass to measure the exact Y position
    of each text line. Returns a list of Y-center values (one per line).
    """
    import tempfile
    from utils.proc import run as proc_run

    cache_key = f"{font_name}:{font_size}:{bold}:{margin_v}:{len(lines)}"
    if cache_key in _libass_calibration_cache:
        return _libass_calibration_cache[cache_key]

    # Build a minimal ASS with the same style and text layout
    bold_val = -1 if bold else 0
    display_lines = []
    for line in lines:
        display_lines.append(" ".join(t for _, t, _ in line))
    display_text = "\\N".join(display_lines)

    ass_content = (
        "[Script Info]\nScriptType: v4.00+\n"
        f"PlayResX: {play_res_x}\nPlayResY: {play_res_y}\n"
        "ScaledBorderAndShadow: yes\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
        "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Cal,{font_name},{font_size},"
        f"&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,"
        f"{bold_val},0,0,0,100,100,2,0,1,0,0,2,60,60,{margin_v},1\n\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
        f"Dialogue: 0,0:00:00.00,0:00:01.00,Cal,,0,0,0,,{display_text}\n"
    )

    try:
        ass_file = tempfile.NamedTemporaryFile(suffix=".ass", delete=False, mode="w")
        ass_file.write(ass_content)
        ass_file.close()

        png_file = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
        png_file.close()

        # Render one frame with grey background
        cmd = [
            "ffmpeg", "-y", "-f", "lavfi",
            "-i", f"color=0x808080:s={play_res_x}x{play_res_y}:d=1",
            "-vf", f"ass='{ass_file.name}'",
            "-frames:v", "1", png_file.name,
        ]
        proc_run(cmd, timeout=10, check=False)

        # Analyze the rendered frame to find text line Y centers
        from PIL import Image
        import numpy as np

        img = Image.open(png_file.name).convert("L")  # greyscale
        arr = np.array(img)

        # Background is 128 grey. White text > 200, black outline < 50.
        # Find rows with bright pixels (white text)
        bright = arr > 200
        rows_with_text = np.where(bright.any(axis=1))[0]

        if len(rows_with_text) < 2:
            # Fallback
            _libass_calibration_cache[cache_key] = []
            return []

        # Split into contiguous blocks (one per line)
        # Return list of (y_center, x_left, x_right) per line
        line_info = []
        block_start = rows_with_text[0]
        prev = rows_with_text[0]
        for r in rows_with_text[1:]:
            if r - prev > 8:
                y_center = (block_start + prev) // 2
                # Find X bounds for this line
                line_rows = arr[block_start:prev + 1]
                cols_with_text = np.where((line_rows > 200).any(axis=0))[0]
                x_left = int(cols_with_text[0]) if len(cols_with_text) else 0
                x_right = int(cols_with_text[-1]) if len(cols_with_text) else play_res_x
                line_info.append((y_center, x_left, x_right))
                block_start = r
            prev = r
        y_center = (block_start + prev) // 2
        line_rows = arr[block_start:prev + 1]
        cols_with_text = np.where((line_rows > 200).any(axis=0))[0]
        x_left = int(cols_with_text[0]) if len(cols_with_text) else 0
        x_right = int(cols_with_text[-1]) if len(cols_with_text) else play_res_x
        line_info.append((y_center, x_left, x_right))

        _libass_calibration_cache[cache_key] = line_info
        return line_info

    except Exception as e:
        print(f"Warning: libass calibration failed: {e}", file=sys.stderr)
        _libass_calibration_cache[cache_key] = []
        return []
    finally:
        try:
            os.unlink(ass_file.name)
        except Exception:
            pass
        try:
            os.unlink(png_file.name)
        except Exception:
            pass


def _measure_text_widths(texts: list[str], font_name: str, font_size: int, bold: bool, spacing: int = 2) -> list[int]:
    """Measure pixel width of each text string using Pillow for accurate positioning.

    Uses fc-match to find the exact same font that libass will use, ensuring
    pixel-accurate pill positioning.
    """
    try:
        from PIL import ImageFont, ImageDraw, Image
        from utils.proc import run as proc_run

        font = None

        # 1) Use fc-match to find the exact font libass resolves (most accurate)
        try:
            style = "Bold" if bold else "Regular"
            result = proc_run(
                ["fc-match", f"{font_name}:{style}", "--format=%{file}"],
                timeout=3, check=False,
            )
            if result.returncode == 0 and result.stdout.strip():
                fc_path = result.stdout.strip()
                if os.path.exists(fc_path):
                    font = ImageFont.truetype(fc_path, font_size)
        except Exception:
            pass

        # 2) Fallback: try common paths (macOS, Linux, Windows)
        if font is None:
            candidates = [
                "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
                "/System/Library/Fonts/Helvetica.ttc",
                "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
                "C:/Windows/Fonts/arialbd.ttf",
            ] if bold else [
                "/System/Library/Fonts/Supplemental/Arial.ttf",
                "/System/Library/Fonts/Helvetica.ttc",
                "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
                "C:/Windows/Fonts/arial.ttf",
            ]
            for path in candidates:
                if os.path.exists(path):
                    try:
                        idx = 1 if (bold and path.endswith(".ttc")) else 0
                        font = ImageFont.truetype(path, font_size, index=idx)
                        break
                    except Exception:
                        continue

        if font is None:
            return [int(len(t) * font_size * 0.6 + spacing * len(t)) for t in texts]

        draw = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
        widths = []
        for text in texts:
            bbox = draw.textbbox((0, 0), text, font=font)
            # ASS Spacing adds extra pixels between each character
            w = bbox[2] - bbox[0] + spacing * max(0, len(text) - 1)
            widths.append(w)
        return widths
    except ImportError:
        return [int(len(t) * font_size * 0.6) for t in texts]


def _rounded_rect_drawing(w: int, h: int, r: int) -> str:
    """Generate ASS \\p1 drawing commands for a rounded rectangle at origin."""
    r = min(r, w // 2, h // 2)
    # Cubic bezier control point offset for ~circular arcs: r * 0.55
    c = int(r * 0.55)
    return (
        f"m {r} 0 "
        f"l {w - r} 0 "
        f"b {w - r + c} 0 {w} {r - c} {w} {r} "
        f"l {w} {h - r} "
        f"b {w} {h - r + c} {w - r + c} {h} {w - r} {h} "
        f"l {r} {h} "
        f"b {r - c} {h} 0 {h - r + c} 0 {h - r} "
        f"l 0 {r} "
        f"b 0 {r - c} {r - c} 0 {r} 0"
    )


def _render_branded(words: list[dict], style: dict, offset: float) -> str:
    """
    Branded style — large bold white text, dark rounded pill on active word only.

    Uses a two-layer approach:
    - Layer 0: \\p1 drawing of a rounded rectangle (pill) behind the active word
    - Layer 1: Text with all words visible

    Each word's display is contiguous to the next word's start to prevent
    flashing/gaps between words.
    """
    header = generate_branded_header(style)
    events = []
    chunk_size = style.get("words_per_chunk", 6)

    raw_box = style.get("active_box_color", "&H00000000")
    box_color = raw_box if raw_box.endswith("&") else raw_box + "&"
    box_alpha = style.get("active_box_alpha", "&H00")

    # Pill dimensions from style config
    pad_x = style.get("active_box_padding_x", 20)
    pad_y = style.get("active_box_padding_y", 10)
    rounding = style.get("active_box_rounding", 15)

    font_size = style.get("font_size", 72)
    font_name = style.get("font_name", "Arial")
    is_bold = style.get("bold", True)
    margin_v = style.get("margin_v", 500)
    play_res_x = 1080
    play_res_y = 1920
    spacing = 2  # ASS Spacing field from header

    # Group words into chunks
    chunks = []
    for i in range(0, len(words), chunk_size):
        chunk = words[i : i + chunk_size]
        chunks.append(chunk)

    for chunk in chunks:
        if not chunk:
            continue

        chunk_start = max(0, chunk[0]["start"] - offset)
        chunk_end = max(0, chunk[-1]["end"] - offset)

        # Normalize casing
        normalized = []
        for j, w in enumerate(chunk):
            text = _normalize_case(w["word"])
            if j == 0:
                text = text[0].upper() + text[1:] if len(text) > 1 else text.upper()
            normalized.append(text)

        # Measure word widths for pill positioning.
        # Use spacing=0 here — ASS Spacing is handled by libass internally,
        # but we position words ourselves with \pos so we need pure font metrics.
        word_widths = _measure_text_widths(normalized, font_name, font_size, is_bold, 0)
        space_width = _measure_text_widths([" "], font_name, font_size, is_bold, 0)[0]

        # Split into visual lines that fit within play_res_x - margins
        max_line_w = play_res_x - 120  # 60px margin each side
        lines = []  # each line: list of (word_index, text, width)
        current_line = []
        current_w = 0
        for wi, text in enumerate(normalized):
            test_w = current_w + (space_width if current_line else 0) + word_widths[wi]
            if test_w > max_line_w and current_line:
                lines.append(current_line)
                current_line = [(wi, text, word_widths[wi])]
                current_w = word_widths[wi]
            else:
                current_line.append((wi, text, word_widths[wi]))
                current_w = test_w
        if current_line:
            lines.append(current_line)

        # Build display text with explicit \N line breaks
        display_lines = []
        for line in lines:
            display_lines.append(" ".join(t for _, t, _ in line))
        display_text = "\\N".join(display_lines)

        # Line height and vertical layout
        line_height = int(font_size * 1.2)
        total_text_h = line_height * len(lines)
        # Use explicit \pos for both text and pill — no reliance on libass layout.
        # This gives us full control over line spacing and pill sizing.
        # Position text block from bottom: last line baseline at (PlayResY - MarginV)
        # Use generous line spacing so pills have room for proper padding.
        pill_total_h = font_size + pad_y * 2  # pill height per line
        line_spacing = pill_total_h + 8  # pill height + 8px clearance between pills
        total_block_h = line_spacing * len(lines)
        # Block bottom aligns with MarginV
        block_bottom_y = play_res_y - margin_v
        # Each line's center Y:
        line_center_ys = []
        for li in range(len(lines)):
            # Position from bottom up: last line first
            y = block_bottom_y - (len(lines) - 1 - li) * line_spacing - font_size // 2
            line_center_ys.append(y)

        # Pre-compute line geometry — measure space width directly with the font,
        # use word widths without ASS Spacing inflation for tighter layout.
        raw_space_w = _measure_text_widths(["A A"], font_name, font_size, is_bold, 0)[0] - \
                      _measure_text_widths(["AA"], font_name, font_size, is_bold, 0)[0]
        line_geometry = []  # (line_center_y, line_left_x, full_line_w, space_w)
        for li, line in enumerate(lines):
            sum_word_w = sum(ww for _, _, ww in line)
            full_line_w = sum_word_w + raw_space_w * max(0, len(line) - 1)
            line_left_x = (play_res_x - full_line_w) // 2
            line_geometry.append((line_center_ys[li], line_left_x, full_line_w, raw_space_w))

        # For each word: emit pill + text (both explicitly positioned)
        for wi, w in enumerate(chunk):
            w_start = max(0, w["start"] - offset)

            # Contiguous timing
            if wi < len(chunk) - 1:
                w_end = max(0, chunk[wi + 1]["start"] - offset)
            else:
                w_end = chunk_end

            if w_end <= w_start:
                w_end = w_start + 0.1

            start_ts = seconds_to_ass(w_start)
            end_ts = seconds_to_ass(w_end)

            # Find which visual line this word is on
            word_line_idx = 0
            word_pos_in_line = 0
            for li, line in enumerate(lines):
                for pi, (idx, _, _) in enumerate(line):
                    if idx == wi:
                        word_line_idx = li
                        word_pos_in_line = pi
                        break

            line_cy, line_lx, _, actual_space_w = line_geometry[word_line_idx]

            # X position using derived natural spacing
            current_line = lines[word_line_idx]
            word_x = line_lx
            for k in range(word_pos_in_line):
                word_x += current_line[k][2] + actual_space_w
            word_x = int(word_x)

            word_w = word_widths[wi]
            word_cx = word_x + word_w // 2
            actual_pad_x = max(pad_x, int(font_size * 0.25))  # at least 25% of font size

            # Pill dimensions
            pill_w = word_w + actual_pad_x * 2
            pill_h = font_size + pad_y * 2
            pill_r = min(rounding, pill_h // 2)

            # Pill drawing centered at origin (for \an5 positioning)
            drawing = _rounded_rect_drawing(pill_w, pill_h, pill_r)
            # Shift drawing so its center is at (0,0) for \an5
            draw_offset_x = pill_w // 2
            draw_offset_y = pill_h // 2

            # Layer 0: Pill — positioned at word center with \an5
            events.append(
                f"Dialogue: 0,{start_ts},{end_ts},BrandedNormal,,0,0,0,,"
                f"{{\\an7\\pos({word_cx - draw_offset_x},{line_cy - draw_offset_y})"
                f"\\p1\\c{box_color}\\bord0\\shad0"
                f"\\1a&HFF&\\t(0,80,\\1a{box_alpha}&)"
                f"}}"
                f"{drawing}{{\\p0}}"
            )

            # Layer 1: Each word positioned with \an5\pos for perfect pill alignment
            for li, line in enumerate(lines):
                lcy_l, llx_l, _, asp_w = line_geometry[li]
                wx = llx_l
                for pi, (_, text, ww) in enumerate(line):
                    wcx = int(wx + ww // 2)
                    events.append(
                        f"Dialogue: 1,{start_ts},{end_ts},BrandedNormal,,0,0,0,,"
                        f"{{\\an5\\pos({wcx},{lcy_l})}}{text}"
                    )
                    wx += ww + asp_w

    return header + "\n".join(events) + "\n"
