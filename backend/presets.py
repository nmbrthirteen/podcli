"""
Preset/template system for podcli.

Save and load named configurations so you don't reconfigure settings
for every episode. Stored as JSON in .podcli/presets/

A preset is a complete show config: video path, rendering options,
corrections, output dir — everything needed to run `podcli process --preset myshow`.
"""

import os
import json
from typing import Optional

from config.paths import paths
from services.formats import FORMATS

PRESETS_DIR = os.path.join(paths["home"], "presets")

# Back-compat aliases for the vertical format's durations. Source of truth is
# FORMATS["vertical"]; kept as module-level names because cli.py,
# clip_generator.py and claude_suggest.py import them directly.
_VERTICAL = FORMATS["vertical"]
MIN_CLIP_DURATION = _VERTICAL.dur_min
MAX_CLIP_DURATION = _VERTICAL.dur_max
TARGET_CLIP_DURATION_MIN = _VERTICAL.target_min
TARGET_CLIP_DURATION_MAX = _VERTICAL.target_max

DEFAULT_PRESET = {
    "caption_style": "branded",
    "crop_strategy": "face",
    "format": "vertical",
    "time_adjust": -1.0,
    "logo_path": "",
    "outro_path": "",
    "video_path": "",
    "transcript_path": "",
    "output_dir": "",
    "whisper_model": "base",
    "top_clips": 5,
    "max_clip_duration": MAX_CLIP_DURATION,
    "min_clip_duration": MIN_CLIP_DURATION,
    "target_lufs": -14.0,
    "energy_boost": True,
    "quality": "max",
    "no_speakers": False,
    "allow_ass_fallback": True,
    "use_ass_captions": False,
    "generate_thumbnails": True,
    "generate_content": True,
    "ai_select": True,
    "review_each_clip": False,
    "post_render_review": False,
    "more_suggestions_multiplier": 3,
    "corrections": {},
}


def list_presets() -> list[dict]:
    """List all saved presets."""
    if not os.path.exists(PRESETS_DIR):
        return []

    presets = []
    for f in sorted(os.listdir(PRESETS_DIR)):
        if f.endswith(".json"):
            name = f[:-5]
            path = os.path.join(PRESETS_DIR, f)
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
                    presets.append({"name": name, **data})
            except (json.JSONDecodeError, IOError):
                pass
    return presets


def get_preset(name: str) -> dict:
    """Load a preset by name, falling back to defaults for missing keys."""
    path = os.path.join(PRESETS_DIR, f"{name}.json")
    if not os.path.exists(path):
        if name == "default":
            return {**DEFAULT_PRESET}
        raise FileNotFoundError(f"Preset not found: {name}")

    with open(path, "r", encoding="utf-8") as f:
        saved = json.load(f)

    # Merge with defaults so new keys are always present
    merged = {**DEFAULT_PRESET, **saved, "name": name}
    return merged


def save_preset(name: str, config: dict) -> str:
    """Save a preset to disk. Saves all provided keys."""
    os.makedirs(PRESETS_DIR, exist_ok=True)
    path = os.path.join(PRESETS_DIR, f"{name}.json")

    # Save all keys that are provided (not just DEFAULT_PRESET keys)
    to_save = {}
    for key, val in config.items():
        if key == "name":
            continue  # don't store the name inside the file
        to_save[key] = val

    with open(path, "w", encoding="utf-8") as f:
        json.dump(to_save, f, indent=2)

    return path


def delete_preset(name: str) -> bool:
    """Delete a preset file."""
    path = os.path.join(PRESETS_DIR, f"{name}.json")
    if os.path.exists(path):
        os.remove(path)
        return True
    return False
