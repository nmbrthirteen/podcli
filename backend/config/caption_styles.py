"""
Caption style definitions for ASS subtitle rendering.

Each style defines fonts, colors, sizes, and behavior for the
supported caption types.
"""

import subprocess
import functools


@functools.lru_cache(maxsize=1)
def _detect_font() -> str:
    """
    Detect the best available sans-serif font on this system.
    Falls back through a priority list until one is found.
    """
    # Priority order: common cross-platform sans-serif fonts
    candidates = [
        "Arial",
        "Helvetica",
        "Liberation Sans",
        "Noto Sans",
        "DejaVu Sans",
        "FreeSans",
        "sans-serif",  # Generic fallback (ffmpeg/fontconfig may resolve it)
    ]

    try:
        result = subprocess.run(
            ["fc-list", "--format=%{family}\n"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            available = set()
            for line in result.stdout.splitlines():
                # fc-list returns "Family1,Family2" for multi-name fonts
                for name in line.split(","):
                    available.add(name.strip())

            for candidate in candidates:
                if candidate in available:
                    return candidate
    except Exception:
        pass

    # If fc-list isn't available (macOS without fontconfig), try common ones
    # Arial is almost always available on macOS/Windows
    return candidates[0]


DETECTED_FONT = _detect_font()

# ASS color format: &HAABBGGRR (hex, alpha-blue-green-red)
# Note: ASS uses BGR order, NOT RGB!

STYLES = {
    "hormozi": {
        "description": "Bold, centered, 2-3 words at a time, active word gets color pop",
        "font_name": DETECTED_FONT,
        "font_size": 80,
        "primary_color": "&H00FFFFFF",       # White (inactive words)
        "active_color": "&H0000FFFF",         # Yellow (active word) — BGR for yellow
        "outline_color": "&H00000000",         # Black outline
        "back_color": "&H80000000",            # Semi-transparent black box
        "active_box_color": "&H00000000",      # Black box behind active word
        "bold": True,
        "outline_width": 0,
        "shadow_depth": 0,
        "border_style": 3,                     # Opaque box (uses back_color as fill)
        "alignment": 2,                        # Bottom-center
        "margin_v": 180,                       # Vertical margin from bottom
        "words_per_chunk": 3,                  # Show 3 words at a time
        "uppercase": True,
        "gradient_overlay": False,
        "logo_support": False,
    },
    "karaoke": {
        "description": "Full sentence visible, words highlight progressively",
        "font_name": DETECTED_FONT,
        "font_size": 60,
        "primary_color": "&H00808080",         # Gray (unspoken words)
        "active_color": "&H00FFFFFF",           # White (spoken words)
        "outline_color": "&H00000000",
        "back_color": "&H80000000",
        "bold": False,
        "outline_width": 3,
        "shadow_depth": 1,
        "alignment": 2,
        "margin_v": 160,
        "words_per_chunk": 5,
        "uppercase": False,
        "gradient_overlay": False,
        "logo_support": False,
    },
    "subtle": {
        "description": "Clean white text at bottom with shadow, professional look",
        "font_name": DETECTED_FONT,
        "font_size": 52,
        "primary_color": "&H00FFFFFF",         # White
        "active_color": None,                   # No active word highlighting
        "outline_color": "&H00000000",
        "back_color": "&H80000000",
        "bold": False,
        "outline_width": 2,
        "shadow_depth": 2,
        "alignment": 2,
        "margin_v": 100,
        "words_per_chunk": 5,
        "uppercase": False,
        "gradient_overlay": False,
        "logo_support": False,
    },
    "branded": {
        "description": "Large bold text, 5-7 words wrapping across 2 lines, dark rounded pill on active word. Clean, no gradient.",
        "font_name": DETECTED_FONT,
        "font_size": 72,                        # Slightly smaller for clean 2-line fit
        "primary_color": "&H00FFFFFF",          # White (all words)
        "active_color": "&H00FFFFFF",           # White text on dark box
        "active_box_color": "&H00000000",       # Black pill bg
        "active_box_alpha": "&H30",             # Pill opacity (00=solid, FF=invisible) — 30 ≈ 81% opaque
        "active_box_padding_x": 10,             # Horizontal padding inside pill
        "active_box_padding_y": 10,             # Vertical padding inside pill
        "active_box_rounding": 15,              # Border rounding for pill shape
        "outline_color": "&H00000000",          # Black
        "back_color": "&H00000000",             # Transparent
        "bold": True,
        "outline_width": 0,                     # No box border (BackColour used for pill)
        "shadow_depth": 1,                      # Subtle shadow for text edge smoothing
        "alignment": 2,                         # Bottom-center
        "margin_v": 500,                        # Position: ~72% from top (natural lower-third)
        "words_per_chunk": 6,                   # Show 5-7 words across 2 lines
        "uppercase": False,                     # Mixed case, natural capitalization
        "gradient_overlay": False,              # No gradient — clean direct-on-video
        "gradient_opacity": 0.0,
        "logo_support": True,                   # Logo top-left
        "logo_margin_x": 40,                    # Logo X offset from left
        "logo_margin_y": 60,                    # Logo Y offset from top
        "logo_height": 100,                     # Logo height in px
    },
}


def get_style(name: str) -> dict:
    """Get a caption style config by name."""
    style = STYLES.get(name)
    if not style:
        raise ValueError(f"Unknown caption style: {name}. Available: {list(STYLES.keys())}")
    return style
