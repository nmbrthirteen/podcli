"""
Caption style definitions for ASS subtitle rendering.

Each style defines fonts, colors, sizes, and behavior for the
supported caption types.
"""

# ASS color format: &HAABBGGRR (hex, alpha-blue-green-red)
# Note: ASS uses BGR order, NOT RGB!

STYLES = {
    "hormozi": {
        "description": "Bold, centered, 2-3 words at a time, active word gets color pop",
        "font_name": "Arial",
        "font_size": 80,
        "primary_color": "&H00FFFFFF",       # White (inactive words)
        "active_color": "&H0000FFFF",         # Yellow (active word) — BGR for yellow
        "outline_color": "&H00000000",         # Black outline
        "back_color": "&H80000000",            # Semi-transparent black shadow
        "bold": True,
        "outline_width": 4,
        "shadow_depth": 2,
        "alignment": 2,                        # Bottom-center
        "margin_v": 180,                       # Vertical margin from bottom
        "words_per_chunk": 3,                  # Show 3 words at a time
        "uppercase": True,
        "gradient_overlay": False,
        "logo_support": False,
    },
    "karaoke": {
        "description": "Full sentence visible, words highlight progressively",
        "font_name": "Arial",
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
        "words_per_chunk": None,               # Full sentence
        "uppercase": False,
        "gradient_overlay": False,
        "logo_support": False,
    },
    "subtle": {
        "description": "Clean white text at bottom with shadow, professional look",
        "font_name": "Arial",
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
        "words_per_chunk": None,               # Full sentence/segment
        "uppercase": False,
        "gradient_overlay": False,
        "logo_support": False,
    },
    "branded": {
        "description": "Large bold text, ~7 words wrapping across 2-3 lines, dark box on active word. TikTok/Reels style.",
        "font_name": "Arial",
        "font_size": 90,
        "primary_color": "&H00FFFFFF",         # White (all words)
        "active_color": "&H00FFFFFF",           # White text on box
        "active_box_color": "&H00181818",       # Dark box (near-black)
        "outline_color": "&H00000000",          # Black outline
        "back_color": "&H00000000",             # No shadow
        "bold": True,
        "outline_width": 0,                     # No outline — gradient provides contrast
        "shadow_depth": 0,
        "alignment": 2,                         # Bottom-center
        "margin_v": 360,                        # Lower-third area
        "words_per_chunk": 7,                   # Show ~7 words, wraps across 2-3 lines
        "uppercase": False,                     # Mixed case
        "gradient_overlay": True,               # Enable bottom 50% dark gradient
        "gradient_opacity": 0.7,                # 70% black at bottom edge
        "logo_support": True,                   # Enable top-left logo
        "logo_margin_x": 30,                    # Logo X offset from left
        "logo_margin_y": 40,                    # Logo Y offset from top
        "logo_height": 80,                      # Logo render height in px
    },
}


def get_style(name: str) -> dict:
    """Get a caption style config by name."""
    style = STYLES.get(name)
    if not style:
        raise ValueError(f"Unknown caption style: {name}. Available: {list(STYLES.keys())}")
    return style
