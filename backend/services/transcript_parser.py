"""
Parse speaker-labeled transcript formats into word-level timestamps.

Supported format:
    Speaker (MM:SS)
    Text of what they said...

    Speaker2 (MM:SS)
    More text...
"""

import re
import json
from typing import List, Dict, Any, Optional


def parse_srt_timestamp(ts: str) -> float:
    """Convert SRT timestamp HH:MM:SS,mmm to seconds."""
    ts = ts.strip().replace(",", ".")
    parts = ts.split(":")
    if len(parts) == 3:
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
    return 0.0


def parse_vtt_timestamp(ts: str) -> float:
    """Convert VTT timestamp HH:MM:SS.mmm or MM:SS.mmm to seconds."""
    ts = ts.strip()
    parts = ts.split(":")
    if len(parts) == 3:
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
    elif len(parts) == 2:
        return int(parts[0]) * 60 + float(parts[1])
    return 0.0


def parse_srt(text: str, total_duration: Optional[float] = None, time_adjust: float = 0.0) -> Dict[str, Any]:
    """
    Parse an SRT subtitle file into structured data with word-level timestamps.

    SRT format:
        1
        00:00:01,000 --> 00:00:04,000
        Hello this is text
    """
    blocks = []
    current_start = 0.0
    current_end = 0.0
    current_text_lines: List[str] = []
    state = "index"  # index, timestamp, text

    for line in text.strip().split("\n"):
        line_stripped = line.strip()

        if state == "index":
            if re.match(r'^\d+$', line_stripped):
                state = "timestamp"
            continue

        if state == "timestamp":
            match = re.match(r'(\d{2}:\d{2}:\d{2},\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2},\d{3})', line_stripped)
            if match:
                current_start = parse_srt_timestamp(match.group(1))
                current_end = parse_srt_timestamp(match.group(2))
                current_text_lines = []
                state = "text"
            continue

        if state == "text":
            if line_stripped == "":
                # End of block
                text_content = " ".join(current_text_lines).strip()
                # Strip HTML-like tags
                text_content = re.sub(r'<[^>]+>', '', text_content)
                text_content = re.sub(r'\s+', ' ', text_content).strip()
                if text_content:
                    blocks.append({
                        "start": current_start,
                        "end": current_end,
                        "text": text_content,
                    })
                state = "index"
            else:
                current_text_lines.append(line_stripped)

    # Handle last block if file doesn't end with blank line
    if state == "text" and current_text_lines:
        text_content = " ".join(current_text_lines).strip()
        text_content = re.sub(r'<[^>]+>', '', text_content)
        text_content = re.sub(r'\s+', ' ', text_content).strip()
        if text_content:
            blocks.append({
                "start": current_start,
                "end": current_end,
                "text": text_content,
            })

    if not blocks:
        return {"error": "No subtitle blocks found in SRT"}

    return _blocks_to_result(blocks, total_duration, time_adjust, fmt="srt")


def parse_vtt(text: str, total_duration: Optional[float] = None, time_adjust: float = 0.0) -> Dict[str, Any]:
    """
    Parse a WebVTT subtitle file into structured data with word-level timestamps.

    VTT format:
        WEBVTT

        00:00:01.000 --> 00:00:04.000
        Hello this is text
    """
    blocks = []
    current_start = 0.0
    current_end = 0.0
    current_text_lines: List[str] = []
    in_text = False

    lines = text.strip().split("\n")
    # Skip WEBVTT header and any metadata
    i = 0
    while i < len(lines):
        if lines[i].strip().startswith("WEBVTT"):
            i += 1
            break
        i += 1

    for line in lines[i:]:
        line_stripped = line.strip()

        # Check for timestamp line
        ts_match = re.match(
            r'(\d{1,2}:\d{2}:\d{2}\.\d{3}|\d{2}:\d{2}\.\d{3})\s*-->\s*(\d{1,2}:\d{2}:\d{2}\.\d{3}|\d{2}:\d{2}\.\d{3})',
            line_stripped,
        )
        if ts_match:
            # Save previous block
            if in_text and current_text_lines:
                text_content = " ".join(current_text_lines).strip()
                text_content = re.sub(r'<[^>]+>', '', text_content)
                text_content = re.sub(r'\s+', ' ', text_content).strip()
                if text_content:
                    blocks.append({
                        "start": current_start,
                        "end": current_end,
                        "text": text_content,
                    })

            current_start = parse_vtt_timestamp(ts_match.group(1))
            current_end = parse_vtt_timestamp(ts_match.group(2))
            current_text_lines = []
            in_text = True
            continue

        if in_text:
            if line_stripped == "":
                text_content = " ".join(current_text_lines).strip()
                text_content = re.sub(r'<[^>]+>', '', text_content)
                text_content = re.sub(r'\s+', ' ', text_content).strip()
                if text_content:
                    blocks.append({
                        "start": current_start,
                        "end": current_end,
                        "text": text_content,
                    })
                current_text_lines = []
                in_text = False
            else:
                # Skip cue identifiers (lines that are just numbers or names before timestamps)
                current_text_lines.append(line_stripped)

    # Handle last block
    if in_text and current_text_lines:
        text_content = " ".join(current_text_lines).strip()
        text_content = re.sub(r'<[^>]+>', '', text_content)
        text_content = re.sub(r'\s+', ' ', text_content).strip()
        if text_content:
            blocks.append({
                "start": current_start,
                "end": current_end,
                "text": text_content,
            })

    if not blocks:
        return {"error": "No subtitle blocks found in VTT"}

    return _blocks_to_result(blocks, total_duration, time_adjust, fmt="vtt")


def _blocks_to_result(blocks: List[Dict], total_duration: Optional[float], time_adjust: float, fmt: str) -> Dict[str, Any]:
    """Convert parsed subtitle blocks into the standard output format."""
    all_words = []
    segments = []

    for block in blocks:
        words_in_block = block["text"].split()
        if not words_in_block:
            continue

        block_duration = block["end"] - block["start"]
        usable_duration = block_duration * 0.95
        word_duration = usable_duration / len(words_in_block) if words_in_block else 0

        for j, w in enumerate(words_in_block):
            word_start = block["start"] + j * word_duration + time_adjust
            word_end = word_start + word_duration * 0.9
            all_words.append({
                "word": w,
                "start": round(max(0, word_start), 3),
                "end": round(max(0, word_end), 3),
                "speaker": None,
            })

        segments.append({
            "text": block["text"],
            "start": round(max(0, block["start"] + time_adjust), 3),
            "end": round(max(0, block["end"] + time_adjust), 3),
            "speaker": None,
        })

    full_text = " ".join(w["word"] for w in all_words)
    duration = total_duration or (blocks[-1]["end"] if blocks else 0)

    return {
        "transcript": full_text,
        "words": all_words,
        "segments": segments,
        "duration": round(duration, 2),
        "language": "en",
        "speakers": [],
        "speaker_segments": [],
        "imported": True,
        "format": fmt,
    }


def detect_and_parse(text: str, total_duration: Optional[float] = None, time_adjust: float = 0.0) -> Dict[str, Any]:
    """
    Auto-detect transcript format and parse accordingly.

    Detection rules:
    - SRT: starts with a digit line followed by a timestamp line (e.g. "1\n00:00:01,000 -->")
    - VTT: starts with "WEBVTT"
    - JSON: starts with { or [
    - Otherwise: speaker format (e.g. "Speaker (MM:SS)\nText...")
    """
    stripped = text.strip()

    # VTT detection
    if stripped.startswith("WEBVTT"):
        return parse_vtt(text, total_duration=total_duration, time_adjust=time_adjust)

    # SRT detection: first line is a digit, second line contains -->
    lines = stripped.split("\n", 3)
    if len(lines) >= 2 and re.match(r'^\d+$', lines[0].strip()) and '-->' in lines[1]:
        return parse_srt(text, total_duration=total_duration, time_adjust=time_adjust)

    # JSON detection
    if stripped.startswith("{") or stripped.startswith("["):
        try:
            data = json.loads(stripped)
            if isinstance(data, list):
                words = data
                segments_list: List[Dict] = []
            else:
                words = data.get("words", [])
                segments_list = data.get("segments", [])
            full_text = " ".join(w.get("word", "") for w in words)
            duration = total_duration or 0
            if words:
                last_end = max(w.get("end", 0) for w in words)
                duration = total_duration or last_end
            return {
                "transcript": full_text,
                "words": words,
                "segments": segments_list,
                "duration": round(duration, 2),
                "language": "en",
                "speakers": [],
                "speaker_segments": [],
                "imported": True,
                "format": "json",
            }
        except json.JSONDecodeError:
            pass  # Fall through to speaker format

    # Default: speaker format
    return parse_speaker_transcript(text, total_duration=total_duration, time_adjust=time_adjust)


def parse_timestamp(ts: str) -> float:
    """Convert MM:SS or HH:MM:SS to seconds."""
    parts = ts.strip().split(":")
    if len(parts) == 2:
        return int(parts[0]) * 60 + int(parts[1])
    elif len(parts) == 3:
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
    return 0.0


def parse_speaker_transcript(raw_text: str, total_duration: Optional[float] = None, time_adjust: float = 0.0) -> Dict[str, Any]:
    """
    Parse a speaker-labeled transcript into structured data with word-level timestamps.

    Words between two known timestamps are distributed evenly across that time window.

    Args:
        raw_text: The raw transcript text
        total_duration: Total duration of the podcast in seconds
        time_adjust: Seconds to add/subtract from all timestamps (e.g., -1.0 to shift 1s earlier)
    """
    lines = raw_text.strip().split("\n")

    # Pattern: "Speaker Name (MM:SS)" or "Speaker (HH:MM:SS)"
    header_pattern = re.compile(r'^(.+?)\s*\((\d{1,2}:\d{2}(?::\d{2})?)\)\s*$')

    # First pass: extract blocks (speaker, start_time, text)
    blocks = []
    current_speaker = None
    current_time = 0.0
    current_text_lines = []

    for line in lines:
        match = header_pattern.match(line.strip())
        if match:
            # Save previous block
            if current_speaker is not None:
                text = " ".join(current_text_lines).strip()
                # Clean up special chars
                text = text.replace("⁓", "").strip()
                text = re.sub(r'\s+', ' ', text)
                if text:
                    blocks.append({
                        "speaker": current_speaker,
                        "start": current_time,
                        "text": text,
                    })

            current_speaker = match.group(1).strip()
            current_time = parse_timestamp(match.group(2))
            current_text_lines = []
        else:
            stripped = line.strip()
            if stripped:
                current_text_lines.append(stripped)

    # Don't forget the last block
    if current_speaker is not None:
        text = " ".join(current_text_lines).strip()
        text = text.replace("⁓", "").strip()
        text = re.sub(r'\s+', ' ', text)
        if text:
            blocks.append({
                "speaker": current_speaker,
                "start": current_time,
                "text": text,
            })

    if not blocks:
        return {"error": "No speaker blocks found in transcript"}

    # Second pass: compute end times for each block
    for i, block in enumerate(blocks):
        if i + 1 < len(blocks):
            block["end"] = blocks[i + 1]["start"]
        else:
            # Last block: use total_duration or estimate from text length
            if total_duration:
                block["end"] = total_duration
            else:
                # Rough estimate: ~3 words per second
                word_count = len(block["text"].split())
                block["end"] = block["start"] + max(word_count / 3.0, 2.0)

    # Third pass: generate word-level timestamps
    all_words = []
    segments = []
    speakers_set = set()
    speaker_times = {}

    for block in blocks:
        speaker = block["speaker"]
        speakers_set.add(speaker)
        words_in_block = block["text"].split()

        if not words_in_block:
            continue

        block_duration = block["end"] - block["start"]
        # Leave a small gap at the end of each block
        usable_duration = block_duration * 0.95
        word_duration = usable_duration / len(words_in_block) if words_in_block else 0

        block_words = []
        for j, w in enumerate(words_in_block):
            word_start = block["start"] + j * word_duration + time_adjust
            word_end = word_start + word_duration * 0.9  # small gap between words
            word_obj = {
                "word": w,
                "start": round(max(0, word_start), 3),
                "end": round(max(0, word_end), 3),
                "speaker": speaker,
            }
            all_words.append(word_obj)
            block_words.append(word_obj)

        # Track speaker time
        if speaker not in speaker_times:
            speaker_times[speaker] = 0.0
        speaker_times[speaker] += block_duration

        segments.append({
            "text": block["text"],
            "start": round(max(0, block["start"] + time_adjust), 3),
            "end": round(max(0, block["end"] + time_adjust), 3),
            "speaker": speaker,
        })

    # Build speaker summary
    speakers_list = []
    for i, speaker in enumerate(sorted(speakers_set)):
        speakers_list.append({
            "id": f"SPEAKER_{i}",
            "label": speaker,
            "total_time": round(speaker_times.get(speaker, 0), 2),
        })

    full_text = " ".join(w["word"] for w in all_words)
    duration = total_duration or (blocks[-1]["end"] if blocks else 0)

    return {
        "transcript": full_text,
        "words": all_words,
        "segments": segments,
        "duration": round(duration, 2),
        "language": "en",
        "speakers": speakers_list,
        "speaker_segments": [
            {"speaker": b["speaker"], "start": b["start"], "end": b["end"]}
            for b in blocks
        ],
        "imported": True,
        "format": "speaker_timestamp",
    }
