"""
Preset/template system for podcli.

Save and load named configurations so you don't reconfigure settings
for every episode. Stored as JSON in ~/.podcli/presets/
"""

import os
import json
from typing import Optional

PRESETS_DIR = os.path.join(
    os.environ.get("PODCLI_HOME", os.path.expanduser("~/.podcli")),
    "presets",
)

DEFAULT_PRESET = {
    "caption_style": "branded",
    "crop_strategy": "center",
    "time_adjust": -1.0,
    "logo_path": "",
    "whisper_model": "base",
    "top_clips": 5,
    "max_clip_duration": 90,
    "min_clip_duration": 20,
    "target_lufs": -14.0,
    "energy_boost": True,
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
    """Save a preset to disk."""
    os.makedirs(PRESETS_DIR, exist_ok=True)
    path = os.path.join(PRESETS_DIR, f"{name}.json")

    # Only save keys that differ from or match the default schema
    to_save = {}
    for key in DEFAULT_PRESET:
        if key in config:
            to_save[key] = config[key]

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
