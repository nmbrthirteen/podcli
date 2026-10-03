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

from config.paths import paths

_CORRECTIONS_PATH = paths["corrections"]


def _load_corrections() -> dict[str, str]:
    """Load corrections dict from .podcli/corrections.json."""
    if not os.path.exists(_CORRECTIONS_PATH):
        return {}
    try:
        with open(_CORRECTIONS_PATH, encoding="utf-8") as f:
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
    with open(_CORRECTIONS_PATH, "w", encoding="utf-8") as f:
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


def _strip_for_match(text: str) -> str:
    return text.strip(".,!?;:\"'()-")


_TRAILING_PUNCT_RE = re.compile(r"[.,!?;:\"'()\-]+$")


def _trailing_punct(text: str) -> str:
    """Trailing punctuation only (not leading), e.g. 'AI.' -> '.'."""
    match = _TRAILING_PUNCT_RE.search(text)
    return match.group(0) if match else ""


def _merge_multiword_corrections(words: list[dict], corrections: dict[str, str]) -> list[dict]:
    """
    Merge consecutive words matching a multi-word correction key (e.g.
    "open AI" -> "OpenAI") into the corrected word(s).

    Segment text gets multi-word corrections for free from the regex pass
    below, but captions are burned from individual words. Left split,
    "open" and "AI" render as two separate caption words instead of the
    fix. The merged word(s) span from the start of the first matched word
    to the end of the last one, so caption timing stays continuous.
    """
    multiword = {k: v for k, v in corrections.items() if len(k.split()) > 1}
    if not multiword or not words:
        return words

    # Longest key first so a 3-word phrase wins over a 2-word prefix of it.
    key_tokens = sorted(
        ((key.split(), value) for key, value in multiword.items()),
        key=lambda kv: len(kv[0]),
        reverse=True,
    )

    merged: list[dict] = []
    i = 0
    n = len(words)
    while i < n:
        match = None
        for tokens, replacement in key_tokens:
            span = len(tokens)
            if i + span > n:
                continue
            candidate = words[i : i + span]
            candidate_norm = [_strip_for_match(w.get("word", "")).lower() for w in candidate]
            if candidate_norm == [t.lower() for t in tokens]:
                match = (candidate, replacement)
                break
        if match is None:
            merged.append(words[i])
            i += 1
            continue

        candidate, replacement = match
        first, last = candidate[0], candidate[-1]
        repl_words = replacement.split() or [replacement]
        span_start = first["start"]
        span_end = last["end"]
        span_dur = max(0.0, span_end - span_start)
        per = span_dur / len(repl_words)

        # The matched words' punctuation and other fields (speaker,
        # confidence, ...) would otherwise vanish behind the correction:
        # "open AI." becomes "OpenAI" with no period and a dropped speaker.
        # Carry the last word's trailing punctuation onto the final merged
        # word, and the first word's other fields onto every merged word.
        trailing_punct = _trailing_punct(last.get("word", ""))
        confidences = [
            w["confidence"] for w in candidate if isinstance(w.get("confidence"), (int, float))
        ]
        extra_fields = {k: v for k, v in first.items() if k not in ("word", "start", "end")}
        if confidences:
            extra_fields["confidence"] = min(confidences)

        for idx, rw in enumerate(repl_words):
            w_start = span_start + per * idx
            w_end = span_end if idx == len(repl_words) - 1 else span_start + per * (idx + 1)
            word_text = rw + trailing_punct if idx == len(repl_words) - 1 else rw
            merged.append({**extra_fields, "word": word_text, "start": w_start, "end": w_end})
        i += len(candidate)

    return merged


def apply_corrections(
    words: list[dict],
    segments: list[dict],
) -> tuple[list[dict], list[dict]]:
    """
    Apply corrections to transcript words and segments in-place.

    Modifies the 'word' field in each word dict and the 'text' field
    in each segment dict. Multi-word corrections can change the number of
    words (several words merge into the correction's word(s)), so the
    `words` list itself is replaced in-place via slice assignment, so
    callers that hold a reference to the original list still see the
    update. Returns the same lists (mutated).
    """
    corrections = _load_corrections()
    if not corrections:
        return words, segments

    pattern = _build_pattern(corrections)
    if pattern is None:
        return words, segments

    replacer = lambda m: _replace_match(m, corrections)

    # Merge multi-word corrections first so captions (built from words) read
    # the fix the same way the segment text already does.
    merged_words = _merge_multiword_corrections(words, corrections)
    if merged_words is not words:
        words[:] = merged_words

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
