"""Caption font glyph coverage: a pre-render check, not a render step.

Before burning captions, verify the font that will actually draw them has
glyphs for every character in the clip's text. A font silently missing a
glyph doesn't fail the render; it draws a tofu box (or nothing), which only
shows up when someone watches the clip. This surfaces that as a warning
instead, naming the characters, without blocking the render.

Two render paths, two coverage sources:
  - ASS (libass/ffmpeg): the font is resolved by family name via fontconfig,
    the same way caption_renderer.py's pixel-width measurer resolves it
    (fc-match). fontconfig already ships wherever ASS rendering does, so
    reading a font's charset via `fc-query` avoids adding fontTools/Pillow
    glyph-table parsing as a new dependency.
  - Remotion: fonts are bundled directly (see remotion/src/Root.tsx), not
    resolved from the system, so there's no font *file* to query at render
    time the way fc-query queries one. REMOTION_BUNDLED_SUBSETS mirrors
    exactly which Unicode subsets Root.tsx loads via FontFace; keep the two
    in sync if that import list changes.
"""

import os
from typing import Optional

from utils.proc import run as proc_run

# Keep in sync with the FontFace loads in remotion/src/Root.tsx. Each bundled
# @fontsource package ships one or more of these subsets; this lists only
# the subsets actually imported there, not everything the packages offer.
REMOTION_BUNDLED_SUBSETS = frozenset({"latin", "latin-ext", "georgian"})

# Coarse Unicode block -> fontsource-style subset name. Enough to catch a
# script with no bundled Remotion font at all (which falls through to an
# unverified system "sans-serif"), without needing full OpenType script
# tagging.
_BLOCK_SUBSETS: tuple[tuple[int, int, str], ...] = (
    (0x0000, 0x007F, "latin"),
    (0x0080, 0x024F, "latin-ext"),
    (0x0370, 0x03FF, "greek"),
    (0x0400, 0x04FF, "cyrillic"),
    (0x0500, 0x052F, "cyrillic-ext"),
    (0x0590, 0x05FF, "hebrew"),
    (0x0600, 0x06FF, "arabic"),
    (0x10A0, 0x10FF, "georgian"),
    (0x1C90, 0x1CBF, "georgian"),
    (0x2D00, 0x2D2F, "georgian"),
    (0x4E00, 0x9FFF, "cjk"),
    (0x3040, 0x30FF, "japanese"),
    (0xAC00, 0xD7AF, "korean"),
)


def _subset_for(codepoint: int) -> Optional[str]:
    for lo, hi, subset in _BLOCK_SUBSETS:
        if lo <= codepoint <= hi:
            return subset
    return None


def _unique_chars_outside_coverage(text: str, is_covered) -> list[str]:
    seen: set[str] = set()
    missing: list[str] = []
    for ch in text:
        if ch.isspace() or ch in seen:
            continue
        seen.add(ch)
        if not is_covered(ch):
            missing.append(ch)
    return missing


def _format_warning(missing: list[str], where: str) -> str:
    sample = "".join(missing[:20])
    tail = " and more" if len(missing) > 20 else ""
    return (
        f"Caption font may be missing glyphs for: {sample}{tail} "
        f"({len(missing)} unique character(s), {where})."
    )


def resolve_ass_font_path(font_name: str, bold: bool = False) -> Optional[str]:
    """The exact font file fc-match/libass resolves for an ASS style, the
    same resolution caption_renderer.py's pixel-width measurer uses.
    """
    style = "Bold" if bold else "Regular"
    try:
        result = proc_run(
            ["fc-match", f"{font_name}:{style}", "--format=%{file}"],
            timeout=3, check=False,
        )
    except Exception:
        return None
    if result.returncode != 0:
        return None
    path = (result.stdout or "").strip()
    return path if path and os.path.exists(path) else None


def _font_charset_ranges(font_path: str) -> Optional[list[tuple[int, int]]]:
    """Parse `fc-query --format=%{charset}` into (lo, hi) codepoint ranges."""
    try:
        result = proc_run(
            ["fc-query", "--format=%{charset}", font_path],
            timeout=5, check=False,
        )
    except Exception:
        return None
    if result.returncode != 0:
        return None
    ranges: list[tuple[int, int]] = []
    for token in (result.stdout or "").split():
        parts = token.split("-", 1)
        try:
            lo = int(parts[0], 16)
            hi = int(parts[1], 16) if len(parts) > 1 else lo
        except ValueError:
            continue
        ranges.append((lo, hi))
    return ranges


def check_ass_font_coverage(text: str, font_name: str, bold: bool = False) -> Optional[str]:
    """Warning naming characters the resolved ASS font has no glyph for, or
    None if it covers everything (or coverage couldn't be determined; this
    never fails the render, so an undetermined font is treated as fine).
    """
    if not text:
        return None
    font_path = resolve_ass_font_path(font_name, bold)
    if not font_path:
        return None
    ranges = _font_charset_ranges(font_path)
    if ranges is None:
        return None
    missing = _unique_chars_outside_coverage(
        text, lambda ch: any(lo <= ord(ch) <= hi for lo, hi in ranges)
    )
    if not missing:
        return None
    return _format_warning(missing, f"font '{font_name}'")


def check_remotion_font_coverage(text: str) -> Optional[str]:
    """Warning naming characters whose script has no bundled Remotion font
    at all, so the render would fall back to an unverified system font.
    """
    if not text:
        return None
    missing = _unique_chars_outside_coverage(
        text,
        lambda ch: (lambda s: s is None or s in REMOTION_BUNDLED_SUBSETS)(
            _subset_for(ord(ch))
        ),
    )
    if not missing:
        return None
    return _format_warning(missing, "no bundled Remotion font covers this script")


def check_caption_font_coverage(
    text: str, font_name: str, bold: bool, use_ass: bool
) -> Optional[str]:
    """Dispatch to whichever render path will actually draw the captions."""
    if use_ass:
        return check_ass_font_coverage(text, font_name, bold)
    return check_remotion_font_coverage(text)
