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

# Default to .podcli/ in the project root (two levels up from backend/)
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PRESETS_DIR = os.path.join(
    os.environ.get("PODCLI_HOME", os.path.join(_PROJECT_ROOT, ".podcli")),
    "presets",
)

DEFAULT_PRESET = {
    "caption_style": "branded",
    "crop_strategy": "speaker",
    "time_adjust": -1.0,
    "logo_path": "",
    "outro_path": "",
    "video_path": "",
    "transcript_path": "",
    "output_dir": "",
    "whisper_model": "base",
    "top_clips": 5,
    "max_clip_duration": 40,
    "min_clip_duration": 15,
    "target_lufs": -14.0,
    "energy_boost": True,
    "quality": "max",
    "no_speakers": False,
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
                with open(path, "r") as fh:
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

    with open(path, "r") as f:
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

    with open(path, "w") as f:
        json.dump(to_save, f, indent=2)

    return path


def delete_preset(name: str) -> bool:
    """Delete a preset file."""
    path = os.path.join(PRESETS_DIR, f"{name}.json")
    if os.path.exists(path):
        os.remove(path)
        return True
    return False
