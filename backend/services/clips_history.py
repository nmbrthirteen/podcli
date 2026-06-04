"""
Clip history — read/write .podcli/history/clips.json.

Shared on-disk contract with the TypeScript ClipsHistory service
(src/services/clips-history.ts). Both languages read and write the same
file; this module is the Python writer used by the CLI.

Entry shape (all fields beyond the core render record are optional):
  id, source_video, start_second, end_second, caption_style, crop_strategy,
  logo_path?, title, output_path, file_size_mb, duration, created_at,
  content_type?, transcript_slice?, youtube_video_id?, metrics?

metrics? = {views?, retention?, ctr?, impressions?, fetched_at?}  (Phase 2)

Rule: writes are read-modify-write and MUST preserve unknown keys, so fields
written by the other language (e.g. Phase 2 metrics) are never clobbered.
"""

import json
import os
from typing import Optional

from config.paths import paths

_CLIPS_HISTORY_PATH = paths["clipsHistory"]


def load_clips_history() -> list[dict]:
    """Load all clip entries. Returns [] if the file is missing or unreadable."""
    if not os.path.exists(_CLIPS_HISTORY_PATH):
        return []
    try:
        with open(_CLIPS_HISTORY_PATH) as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def save_clips_history(entries: list[dict]) -> str:
    """Persist the full entry list. Returns the file path."""
    os.makedirs(os.path.dirname(_CLIPS_HISTORY_PATH), exist_ok=True)
    with open(_CLIPS_HISTORY_PATH, "w") as f:
        json.dump(entries, f, indent=2, ensure_ascii=False)
    return _CLIPS_HISTORY_PATH


def list_clips(limit: int = 50) -> list[dict]:
    """Most recent clips first."""
    entries = load_clips_history()
    return entries[-limit:][::-1]


def get_clips_by_source(video_path: str) -> list[dict]:
    """All clips from a source video (basename match), newest first."""
    target = os.path.basename(video_path)
    entries = load_clips_history()
    return [e for e in entries if os.path.basename(e.get("source_video", "")) == target][::-1]


def find_clip(clip_id: str) -> Optional[dict]:
    """Find by exact id, falling back to an unambiguous 8-char prefix match."""
    entries = load_clips_history()
    for e in entries:
        if e.get("id") == clip_id:
            return e
    prefix_matches = [e for e in entries if str(e.get("id", "")).startswith(clip_id)]
    return prefix_matches[0] if len(prefix_matches) == 1 else None


def update_clip(clip_id: str, **fields) -> Optional[dict]:
    """Update a clip in place, preserving all unknown keys. Returns the updated entry or None.

    Only keys with non-None values are applied, so callers can pass optional
    edits without overwriting existing data with None.
    """
    target = find_clip(clip_id)
    if target is None:
        return None
    entries = load_clips_history()
    updated = None
    for e in entries:
        if e.get("id") == target.get("id"):
            for key, value in fields.items():
                if value is not None:
                    e[key] = value
            updated = e
            break
    if updated is None:
        return None
    save_clips_history(entries)
    return updated
