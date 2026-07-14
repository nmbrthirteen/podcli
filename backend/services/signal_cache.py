"""Disk cache for a video's audio signals (energy, reactions).

Transcription already decodes the audio and runs both analyzers, so clip
suggestion reads the profiles from here instead of decoding the source again.
Keyed on the video's content hash, not the engine: the signals come from the
audio, not from the transcript.
"""

from __future__ import annotations

import json
import os
from typing import Any, Optional

from config.paths import paths
from services.transcript_packer import compute_cache_hash


def _signals_path(video_path: str) -> str:
    return os.path.join(paths["cache"], "signals", f"{compute_cache_hash(video_path)}.json")


def load_signals(video_path: str) -> dict[str, Any]:
    """Cached {energy_data, events_data} for a video, or {} if absent."""
    try:
        with open(_signals_path(video_path), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_signals(
    video_path: str,
    energy_data: Optional[list[dict]] = None,
    events_data: Optional[list[dict]] = None,
) -> None:
    """Merge the given profiles into the cache. Best-effort: a cache miss later
    only costs a re-analysis."""
    payload = load_signals(video_path)
    if energy_data is not None:
        payload["energy_data"] = energy_data
    if events_data is not None:
        payload["events_data"] = events_data
    if not payload:
        return
    try:
        path = _signals_path(video_path)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f)
    except (OSError, ValueError):
        pass
