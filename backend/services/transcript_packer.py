"""
Pack a cached transcription into a compact markdown surface for LLM reading.

The goal: give an LLM everything it needs to reason about clip selection and
cut points without watching video. Words, speakers, silences, and optional
energy peaks fit into ~10-20KB of text.

Input:  cached transcription JSON (.podcli/cache/<hash>.json)
Output: .podcli/packed/<hash>.md
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from typing import Optional

PROJECT_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
)
CACHE_DIR = os.path.join(PROJECT_ROOT, ".podcli", "cache")
PACKED_DIR = os.path.join(PROJECT_ROOT, ".podcli", "packed")

# Phrase construction tuning
SILENCE_SPLIT_SEC = 0.5      # split a phrase on word gap >= this
SILENCE_GAP_REPORT_SEC = 0.6 # list gaps >= this in the silence section
PHRASE_MAX_SEC = 12.0
PHRASE_MAX_CHARS = 160
ENERGY_PEAKS_TO_REPORT = 20


def compute_cache_hash(video_path: str) -> str:
    """Match src/services/transcript-cache.ts: sha256 of first 10MB + 'size:<N>', 16 hex chars."""
    size = os.path.getsize(video_path)
    h = hashlib.sha256()
    remaining = 10 * 1024 * 1024
    with open(video_path, "rb") as f:
        while remaining > 0:
            chunk = f.read(min(1 << 20, remaining))
            if not chunk:
                break
            h.update(chunk)
            remaining -= len(chunk)
    h.update(f"size:{size}".encode())
    return h.hexdigest()[:16]


def _fmt_duration(seconds: float) -> str:
    s = int(round(seconds))
    h, rem = divmod(s, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h {m}m {s}s"
    return f"{m}m {s}s"


def _fmt_ts(seconds: float) -> str:
    return f"{seconds:07.2f}"


def _normalize_speakers(speakers_block) -> dict[str, dict]:
    """Return {raw_id: {total_time, label, segments}} regardless of input shape.

    Accepts two shapes:
      - transcribe output: {"num_speakers": N, "speakers": {SPEAKER_00: {...}}}
      - parse_transcript output: [{"id": SPEAKER_0, "label": ..., "total_time": ...}]
    """
    if isinstance(speakers_block, dict):
        inner = speakers_block.get("speakers", {})
        if isinstance(inner, dict):
            return inner
        speakers_block = inner  # list fall-through
    if isinstance(speakers_block, list):
        return {
            entry.get("id", f"S{i}"): {
                "total_time": entry.get("total_time", 0),
                "label": entry.get("label", entry.get("id", f"S{i}")),
                "segments": entry.get("segments", 0),
            }
            for i, entry in enumerate(speakers_block)
            if isinstance(entry, dict)
        }
    return {}


def _speaker_short_map(speakers_block) -> dict[str, str]:
    """Map speaker IDs AND labels to short codes (S0, S1, ...) sorted by talk time.

    Accepts either key — transcribe output uses SPEAKER_00 everywhere, but
    parse_transcript uses labels ("Host") in word.speaker/segment.speaker
    while keeping SPEAKER_0 as the canonical id. Mapping both avoids S? holes.
    """
    normalized = _normalize_speakers(speakers_block)
    ranked = sorted(
        normalized.items(),
        key=lambda kv: kv[1].get("total_time", 0),
        reverse=True,
    )
    short: dict[str, str] = {}
    for i, (raw_id, info) in enumerate(ranked):
        code = f"S{i}"
        short[raw_id] = code
        label = info.get("label")
        if label and label not in short:
            short[label] = code
    return short


def _speaker_at(speaker_segments: list[dict], t: float, last_known: Optional[str]) -> Optional[str]:
    """Return the raw speaker ID whose segment contains time t, else last_known."""
    for seg in speaker_segments:
        if seg["start"] <= t <= seg["end"]:
            return seg["speaker"]
    return last_known


def _build_phrases(
    words: list[dict],
    short: dict[str, str],
    speaker_segments: list[dict],
) -> list[dict]:
    """Group words into phrases by speaker + silence gaps.

    Uses speaker_segments as authoritative for speaker attribution — per-word
    speaker fields are often noisy at segment boundaries, causing single-word
    fragments that make the transcript unreadable.
    """
    phrases: list[dict] = []
    current: Optional[dict] = None
    last_known: Optional[str] = None

    for w in words:
        text = w.get("word", "")
        if not text:
            continue
        start = float(w.get("start", 0.0))
        end = float(w.get("end", start))
        mid = (start + end) / 2
        raw_spk = _speaker_at(speaker_segments, mid, last_known) or w.get("speaker") or last_known
        if raw_spk is None:
            raw_spk = "UNKNOWN"
        last_known = raw_spk
        spk = short.get(raw_spk, "S?")

        should_split = (
            current is None
            or current["speaker"] != spk
            or (start - current["end"]) >= SILENCE_SPLIT_SEC
            or (end - current["start"]) >= PHRASE_MAX_SEC
            or (len(current["text"]) + len(text) + 1) >= PHRASE_MAX_CHARS
        )

        if should_split:
            if current is not None:
                phrases.append(current)
            current = {"speaker": spk, "start": start, "end": end, "text": text.lstrip()}
        else:
            glue = "" if text.startswith(("'", ",", ".", "?", "!", ";", ":")) else " "
            current["text"] += f"{glue}{text}"
            current["end"] = end

    if current is not None:
        phrases.append(current)
    return phrases


def _find_silence_gaps(words: list[dict], min_gap: float) -> list[dict]:
    gaps = []
    for prev, nxt in zip(words, words[1:]):
        gap = float(nxt.get("start", 0.0)) - float(prev.get("end", 0.0))
        if gap >= min_gap:
            gaps.append({
                "start": float(prev.get("end", 0.0)),
                "end": float(nxt.get("start", 0.0)),
                "duration": gap,
            })
    return gaps


def _phrase_for_time(phrases: list[dict], t: float) -> Optional[dict]:
    for p in phrases:
        if p["start"] <= t <= p["end"]:
            return p
    # nearest
    if not phrases:
        return None
    return min(phrases, key=lambda p: min(abs(p["start"] - t), abs(p["end"] - t)))


def pack_transcript(
    transcript: dict,
    source_label: str,
    energy_data: Optional[list[dict]] = None,
) -> str:
    """Render a packed markdown view of a transcription JSON."""
    duration = float(transcript.get("duration", 0.0))
    language = transcript.get("language", "?")
    words = transcript.get("words", [])
    speakers_block = transcript.get("speakers", {}) or {}
    speakers_map = _normalize_speakers(speakers_block)
    num_speakers = (
        speakers_block.get("num_speakers")
        if isinstance(speakers_block, dict)
        else None
    ) or len(speakers_map)

    short = _speaker_short_map(speakers_block)
    speaker_segments = transcript.get("speaker_segments", []) or []
    phrases = _build_phrases(words, short, speaker_segments)
    gaps = _find_silence_gaps(words, SILENCE_GAP_REPORT_SEC)

    lines: list[str] = []
    lines.append(f"# Episode: {source_label}")
    lines.append(
        f"duration: {_fmt_duration(duration)} · language: {language} · "
        f"speakers: {num_speakers} · words: {len(words)} · phrases: {len(phrases)}"
    )
    lines.append("")

    # Speakers summary
    lines.append("## Speakers")
    ranked = sorted(
        speakers_map.items(),
        key=lambda kv: kv[1].get("total_time", 0),
        reverse=True,
    )
    for raw_id, info in ranked:
        talk = float(info.get("total_time", 0))
        pct = (talk / duration * 100) if duration > 0 else 0
        label = info.get("label") or raw_id
        lines.append(
            f"- {short[raw_id]} ({label}): {_fmt_duration(talk)} · {pct:.0f}%"
        )
    lines.append("")

    # Transcript phrases
    lines.append("## Transcript")
    for p in phrases:
        lines.append(
            f"[{_fmt_ts(p['start'])}-{_fmt_ts(p['end'])}] {p['speaker']} {p['text']}"
        )
    lines.append("")

    # Silence gaps
    if gaps:
        lines.append(f"## Silence gaps (>={SILENCE_GAP_REPORT_SEC}s)")
        for g in gaps:
            lines.append(
                f"[{_fmt_ts(g['start'])}-{_fmt_ts(g['end'])}] {g['duration']:.2f}s"
            )
        lines.append("")

    # Energy peaks (optional)
    if energy_data:
        sorted_by_rms = sorted(energy_data, key=lambda e: e.get("rms_db", -999), reverse=True)
        peaks = sorted_by_rms[:ENERGY_PEAKS_TO_REPORT]
        peaks.sort(key=lambda e: e.get("time", 0))
        lines.append(f"## Energy peaks (top {len(peaks)})")
        for e in peaks:
            t = float(e.get("time", 0))
            rms = float(e.get("rms_db", 0))
            near = _phrase_for_time(phrases, t)
            snippet = ""
            if near:
                txt = near["text"]
                snippet = f' near: "{txt[:70]}{"…" if len(txt) > 70 else ""}"'
            lines.append(f"[{_fmt_ts(t)}] {rms:.1f}dB{snippet}")
        lines.append("")

    return "\n".join(lines)


def load_cache(cache_hash: str) -> dict:
    path = os.path.join(CACHE_DIR, f"{cache_hash}.json")
    with open(path, "r") as f:
        return json.load(f)


def packed_path_for(cache_hash: str) -> str:
    return os.path.join(PACKED_DIR, f"{cache_hash}.md")


def write_packed(
    transcript: dict,
    cache_hash: str,
    source_label: Optional[str] = None,
    energy_data: Optional[list[dict]] = None,
) -> tuple[str, str]:
    """Pack a transcript dict and write to .podcli/packed/<hash>.md."""
    label = source_label or cache_hash
    md = pack_transcript(transcript, label, energy_data=energy_data)
    os.makedirs(PACKED_DIR, exist_ok=True)
    out_path = packed_path_for(cache_hash)
    with open(out_path, "w") as f:
        f.write(md)
    return out_path, md


def pack_from_hash(cache_hash: str, source_label: Optional[str] = None) -> tuple[str, str]:
    """Pack a cached transcript by its hash. Returns (output_path, markdown)."""
    transcript = load_cache(cache_hash)
    return write_packed(transcript, cache_hash, source_label)


def pack_from_video(video_path: str) -> tuple[str, str]:
    """Pack a transcript given the source video path."""
    cache_hash = compute_cache_hash(video_path)
    label = os.path.basename(video_path)
    return pack_from_hash(cache_hash, label)


def _main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: python -m services.transcript_packer <video-path-or-cache-hash>", file=sys.stderr)
        return 2
    target = argv[1]
    if os.path.exists(target):
        out_path, md = pack_from_video(target)
    else:
        out_path, md = pack_from_hash(target)
    size_kb = len(md.encode("utf-8")) / 1024
    print(f"wrote {out_path} ({size_kb:.1f} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv))
