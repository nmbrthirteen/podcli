"""
Parse speaker-labeled transcript formats into word-level timestamps.

Supported format:
    Speaker (MM:SS)
    Text of what they said...

    Speaker2 (MM:SS)
    More text...
"""

import re
from typing import List, Dict, Any, Optional


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
