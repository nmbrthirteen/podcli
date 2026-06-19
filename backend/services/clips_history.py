"""
Clip history — read/write .podcli/history/clips.json.

Shared on-disk contract with the TypeScript ClipsHistory service
(src/services/clips-history.ts). Both languages read and write the same
file; this module is the Python writer used by the CLI.

Entry shape (all fields beyond the core render record are optional):
  id, source_video, start_second, end_second, caption_style, crop_strategy,
  logo_path?, title, output_path, file_size_mb, duration, created_at,
  content_type?, transcript_slice?, youtube_video_id?, metrics?,
  generated_titles?, description?, tags?, hashtags?

metrics? = {views?, retention?, ctr?, impressions?, fetched_at?}  (Phase 2)

Rule: writes are read-modify-write and MUST preserve unknown keys, so fields
written by the other language (e.g. Phase 2 metrics) are never clobbered.
"""

import json
import os
import shutil
from typing import Optional

from config.paths import paths

_CLIPS_HISTORY_PATH = paths["clipsHistory"]


def load_clips_history() -> list[dict]:
    """Load all clip entries. Returns [] if the file is missing or unreadable."""
    if not os.path.exists(_CLIPS_HISTORY_PATH):
        return []
    try:
        with open(_CLIPS_HISTORY_PATH, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def save_clips_history(entries: list[dict]) -> str:
    """Persist the full entry list atomically. Returns the file path.

    Writes a temp file then os.replace()s it into place so a crash or a
    concurrent reader (the TS web server) never sees a half-written file.
    """
    os.makedirs(os.path.dirname(_CLIPS_HISTORY_PATH), exist_ok=True)
    tmp = f"{_CLIPS_HISTORY_PATH}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(entries, f, indent=2, ensure_ascii=False)
    os.replace(tmp, _CLIPS_HISTORY_PATH)
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
    if not clip_id:
        return None
    entries = load_clips_history()
    for e in entries:
        if e.get("id") == clip_id:
            return e
    prefix_matches = [e for e in entries if str(e.get("id", "")).startswith(clip_id)]
    return prefix_matches[0] if len(prefix_matches) == 1 else None


def _clip_sidecar_paths(clip_id: str) -> list[str]:
    """Per-clip sidecar files (words/recipe/reframe) and the thumbnail dir."""
    history_dir = os.path.dirname(_CLIPS_HISTORY_PATH)
    return [
        os.path.join(history_dir, "words", f"{clip_id}.json"),
        os.path.join(history_dir, "recipes", f"{clip_id}.json"),
        os.path.join(history_dir, "reframe", f"{clip_id}.json"),
    ]


def delete_clip(clip_id: str) -> Optional[dict]:
    """Remove a clip from history along with its rendered output and sidecars.

    Returns the removed entry, or None if no clip matched. The source video is
    never touched — only artifacts podcli rendered for this clip.
    """
    target = find_clip(clip_id)
    if target is None:
        return None
    full_id = str(target.get("id"))
    entries = load_clips_history()
    save_clips_history([e for e in entries if str(e.get("id")) != full_id])

    artifacts = list(_clip_sidecar_paths(full_id))
    output_path = target.get("output_path")
    if output_path:
        artifacts.append(output_path)
    for path in artifacts:
        try:
            if path and os.path.isfile(path):
                os.remove(path)
        except OSError:
            pass

    thumb_dir = os.path.join(paths["output"], "thumbnails", full_id)
    try:
        if os.path.isdir(thumb_dir):
            shutil.rmtree(thumb_dir)
    except OSError:
        pass
    return target


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
