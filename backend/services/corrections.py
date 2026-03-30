"""
Transcript word corrections — fixes Whisper misheard proper nouns.

Loads replacements from .podcli/corrections.json and applies them to
transcript words and segments. Single source of truth used by all paths
(Whisper, import, parse).

Format of corrections.json:
{
  "Boxel": "Voxel",
  "grub": "GRU",
  "open AI": "OpenAI"
}
"""

import json
import os
import re
from typing import Optional


_CORRECTIONS_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "..", ".podcli", "corrections.json"
)


def _load_corrections() -> dict[str, str]:
    """Load corrections dict from .podcli/corrections.json."""
    if not os.path.exists(_CORRECTIONS_PATH):
        return {}
    try:
        with open(_CORRECTIONS_PATH) as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
        return {}
    except Exception:
        return {}


def get_corrections() -> dict[str, str]:
    """Get current corrections dict (public API for UI/MCP)."""
    return _load_corrections()


def save_corrections(corrections: dict[str, str]) -> str:
    """Save corrections dict to .podcli/corrections.json. Returns the file path."""
    os.makedirs(os.path.dirname(_CORRECTIONS_PATH), exist_ok=True)
    with open(_CORRECTIONS_PATH, "w") as f:
        json.dump(corrections, f, indent=2, ensure_ascii=False)
    return _CORRECTIONS_PATH


def _build_pattern(corrections: dict[str, str]) -> Optional[re.Pattern]:
    """Build a compiled regex that matches any correction key (case-insensitive, word boundary)."""
    if not corrections:
        return None
    # Sort by length descending so longer matches take priority (e.g. "open AI" before "AI")
    keys = sorted(corrections.keys(), key=len, reverse=True)
    pattern = "|".join(re.escape(k) for k in keys)
    return re.compile(rf"\b({pattern})\b", re.IGNORECASE)


def _replace_match(match: re.Match, corrections: dict[str, str]) -> str:
    """Replace a regex match with its correction, preserving the original's case pattern."""
    matched = match.group(0)
    # Look up case-insensitive: try exact first, then case-insensitive scan
    replacement = corrections.get(matched)
    if replacement is None:
        for key, val in corrections.items():
            if key.lower() == matched.lower():
                replacement = val
                break
    return replacement if replacement is not None else matched


def apply_corrections(
    words: list[dict],
    segments: list[dict],
) -> tuple[list[dict], list[dict]]:
    """
    Apply corrections to transcript words and segments in-place.

    Modifies the 'word' field in each word dict and the 'text' field
    in each segment dict. Returns the same lists (mutated).
    """
    corrections = _load_corrections()
    if not corrections:
        return words, segments

    pattern = _build_pattern(corrections)
    if pattern is None:
        return words, segments

    replacer = lambda m: _replace_match(m, corrections)

    # Fix individual words (strip punctuation for matching, preserve it in output)
    for w in words:
        word_text = w.get("word", "")
        if word_text:
            # Try regex match on full text first
            new_text = pattern.sub(replacer, word_text)
            if new_text != word_text:
                w["word"] = new_text
            else:
                # Strip trailing/leading punctuation and try again
                stripped = word_text.strip(".,!?;:\"'()-")
                if stripped and stripped != word_text:
                    new_stripped = pattern.sub(replacer, stripped)
                    if new_stripped != stripped:
                        w["word"] = word_text.replace(stripped, new_stripped)

    # Fix segment text (may contain multi-word corrections like "open AI" → "OpenAI")
    for seg in segments:
        seg_text = seg.get("text", "")
        if seg_text:
            new_text = pattern.sub(replacer, seg_text)
            if new_text != seg_text:
                seg["text"] = new_text

    return words, segments
