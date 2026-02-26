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

    return f"""[Script Info]
Title: Podcast Clip Captions
ScriptType: v4.00+
PlayResX: {play_res_x}
PlayResY: {play_res_y}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{style["font_name"]},{style["font_size"]},{style["primary_color"]},{style["primary_color"]},{style["outline_color"]},{style["back_color"]},{bold_val},0,0,0,100,100,0,0,1,{style["outline_width"]},{style["shadow_depth"]},{style["alignment"]},40,40,{style["margin_v"]},1

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
Style: BrandedNormal,{style["font_name"]},{style["font_size"]},&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,0,0,{style["alignment"]},80,80,{style["margin_v"]},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


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
    Hormozi style: Show 2-3 words at a time, highlight active word.
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

        for word_idx, active_word in enumerate(chunk):
            w_start = max(0, active_word["start"] - offset)
            w_end = max(0, active_word["end"] - offset)

            parts = []
            for j, w in enumerate(chunk):
                text = w["word"].upper() if uppercase else w["word"]
                if j == word_idx:
                    active_c = style["active_color"]
                    parts.append(f"{{\\c{active_c}}}{text}{{\\c{style['primary_color']}}}")
                else:
                    parts.append(text)

            line_text = " ".join(parts)
            start_ts = seconds_to_ass(w_start)
            end_ts = seconds_to_ass(w_end)

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

    sentence_size = 10
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

    line_size = 7
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


def _render_branded(words: list[dict], style: dict, offset: float) -> str:
    """
    Branded style — large bold text wrapping across 2-3 lines.
    Active word gets a dark rounded-box background.
    All text stays white. Designed for TikTok/Reels vertical video.

    Shows ~7 words at a time (wrapping naturally via ASS WrapStyle: 0),
    cycling through each word as active with a dark box highlight.
    """
    header = generate_branded_header(style)
    events = []
    chunk_size = style.get("words_per_chunk", 7)
    box_color = style.get("active_box_color", "&H00181818")

    # Group words into chunks
    chunks = []
    for i in range(0, len(words), chunk_size):
        chunk = words[i : i + chunk_size]
        chunks.append(chunk)

    for chunk in chunks:
        if not chunk:
            continue

        for word_idx, active_word in enumerate(chunk):
            w_start = max(0, active_word["start"] - offset)
            w_end = max(0, active_word["end"] - offset)

            parts = []
            for j, w in enumerate(chunk):
                text = w["word"]
                if j == word_idx:
                    # Dark box via thick border: \3c sets border color as box fill
                    # \xbord\ybord control horizontal/vertical padding
                    parts.append(
                        f"{{\\bord10\\xbord14\\ybord8\\3c{box_color}\\shad0}}"
                        f"{text}"
                        f"{{\\bord0\\3c&H00000000&}}"
                    )
                else:
                    parts.append(text)

            line_text = " ".join(parts)

            start_ts = seconds_to_ass(w_start)
            end_ts = seconds_to_ass(w_end)

            events.append(
                f"Dialogue: 0,{start_ts},{end_ts},BrandedNormal,,0,0,0,,{line_text}"
            )

    return header + "\n".join(events) + "\n"
