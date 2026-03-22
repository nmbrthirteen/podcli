"""
Clip generator — orchestrates the full pipeline for creating a short-form clip.

Pipeline: cut segment → crop to 9:16 → render captions → burn captions
          (+ optional gradient overlay + logo) → normalize audio
"""

import os
import sys
import tempfile
import shutil
from typing import Optional, Callable

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.caption_renderer import render_captions
from services.video_processor import (
    cut_segment,
    cut_multi_segment,
    crop_to_vertical,
    burn_captions,
    normalize_audio,
    concat_outro,
)
from config.caption_styles import get_style

# Filler words to strip from captions (not aggressive — just obvious fillers)
_FILLER_WORDS = frozenset([
    "um", "uh", "uhh", "uhm", "umm", "hmm", "hm", "mhm",
    "ah", "er", "erm", "eh",
])


def _snap_to_sentence_end(
    transcript_words: list[dict], start_second: float, end_second: float,
    max_extension: float = 3.0,
) -> float:
    """
    Snap clip end_second to the nearest sentence boundary (. ! ?).
    Searches forward up to max_extension seconds. If no boundary found,
    searches backward within the clip. Returns adjusted end_second.
    """
    SENTENCE_ENDINGS = ".!?"

    # Get the last word in the clip range
    clip_words = [w for w in transcript_words if w["end"] > start_second and w["start"] < end_second]
    if not clip_words:
        return end_second

    last_clip_word = clip_words[-1]
    last_word_text = last_clip_word.get("word", "").strip()

    # Already ends on a sentence boundary — extend to include full word
    if last_word_text and last_word_text[-1] in SENTENCE_ENDINGS:
        return max(end_second, last_clip_word["end"])

    # Search forward: find next sentence-ending word within max_extension
    for w in sorted(transcript_words, key=lambda x: x["start"]):
        if w["start"] >= last_clip_word["end"] and w["end"] <= end_second + max_extension:
            text = w.get("word", "").strip()
            if text and text[-1] in SENTENCE_ENDINGS:
                return w["end"]

    # Search backward: find last sentence-ending word within the clip
    for w in reversed(clip_words):
        text = w.get("word", "").strip()
        if text and text[-1] in SENTENCE_ENDINGS:
            # Only snap backward if we don't lose more than 30% of the clip
            if w["end"] > start_second + (end_second - start_second) * 0.7:
                return w["end"]

    return end_second


def _clean_transcript_words(words: list[dict]) -> list[dict]:
    """
    Remove filler words from transcript captions.

    - Strips obvious filler words (um, uh, hmm, etc.) so they don't appear in captions.
    - Does NOT shift timestamps — word timing must match the actual audio exactly.
    """
    cleaned = []
    for w in words:
        text = w.get("word", "").strip()
        # Strip punctuation for matching, but keep original text
        bare = text.lower().strip(".,!?;:-–—'\"")
        if bare in _FILLER_WORDS:
            continue
        cleaned.append(w)

    return cleaned


def _build_tight_segments(
    words: list[dict],
    start_second: float,
    end_second: float,
    silence_threshold: float = 1.5,
) -> list[dict]:
    """
    Build tight keep-segments from transcript words, cutting out:
    - Filler words (um, uh, hmm) that are isolated (not mid-sentence)
    - Long silences/pauses (> silence_threshold seconds)

    Returns list of {"start": float, "end": float} segments.
    If no cuts needed, returns a single segment spanning the full range.
    """
    # Get words in clip range
    clip_words = [
        w for w in words
        if w["end"] > start_second and w["start"] < end_second
    ]

    if len(clip_words) < 3:
        return [{"start": start_second, "end": end_second}]

    # Find gaps and filler words to cut
    segments = []
    seg_start = start_second

    for i, w in enumerate(clip_words):
        text = w.get("word", "").strip()
        bare = text.lower().strip(".,!?;:-–—'\"")
        is_filler = bare in _FILLER_WORDS

        # Check gap before this word
        prev_end = clip_words[i - 1]["end"] if i > 0 else start_second
        gap = w["start"] - prev_end

        if gap >= silence_threshold:
            # Long pause — end current segment, start new one
            if prev_end > seg_start + 0.5:  # Don't create tiny segments
                segments.append({"start": round(seg_start, 2), "end": round(prev_end, 2)})
            seg_start = w["start"]
        elif is_filler and gap >= 0.3:
            # Isolated filler word with a gap before it — skip it
            # End segment before the filler, start after it
            if w["start"] > seg_start + 0.5:
                segments.append({"start": round(seg_start, 2), "end": round(w["start"], 2)})
            seg_start = w["end"]

    # Close final segment
    final_end = min(end_second, clip_words[-1]["end"] + 0.3)
    if final_end > seg_start + 0.5:
        segments.append({"start": round(seg_start, 2), "end": round(final_end, 2)})

    if not segments:
        return [{"start": start_second, "end": end_second}]

    # Only return multi-segment if we actually saved meaningful time
    original_duration = end_second - start_second
    kept_duration = sum(s["end"] - s["start"] for s in segments)
    saved = original_duration - kept_duration

    if saved < 1.5 or len(segments) < 2:
        # Not worth the cuts — too little saved
        return [{"start": start_second, "end": end_second}]

    return segments


def generate_clip(
    video_path: str,
    start_second: float,
    end_second: float,
    caption_style: str = "hormozi",
    crop_strategy: str = "face",
    transcript_words: list[dict] = None,
    title: str = "clip",
    output_dir: Optional[str] = None,
    face_map: dict = None,
    logo_path: Optional[str] = None,
    outro_path: Optional[str] = None,
    clean_fillers: bool = True,
    keep_segments: list[dict] = None,
    progress_callback: Optional[Callable[[int, str], None]] = None,
) -> dict:
    """
    Generate a complete short-form video clip.

    Args:
        video_path: Path to source podcast video
        start_second: Clip start time
        end_second: Clip end time
        caption_style: "hormozi", "karaoke", "subtle", or "branded"
        crop_strategy: "center" or "face"
        transcript_words: Word-level timestamps from transcription
        title: Clip title (used in filename)
        output_dir: Where to save the final clip (defaults to temp)
        logo_path: Path to logo image (PNG). Used with "branded" style.
        progress_callback: Optional (percent, message) callback

    Returns:
        {
            "output_path": str,
            "duration": float,
            "file_size_mb": float,
            "title": str,
        }
    """
    if not os.path.exists(video_path):
        raise FileNotFoundError(f"Video not found: {video_path}")

    if end_second <= start_second:
        raise ValueError("end_second must be greater than start_second")

    # Multi-segment cutting: if keep_segments provided, use those ranges.
    # Otherwise auto-detect silences/fillers and build tight segments.
    if keep_segments and len(keep_segments) > 0:
        # Validate segments from Claude
        keep_segments = [s for s in keep_segments if s["end"] > s["start"]]
        keep_segments.sort(key=lambda s: s["start"])
        start_second = keep_segments[0]["start"]
        end_second = keep_segments[-1]["end"]
        duration = sum(s["end"] - s["start"] for s in keep_segments)
    else:
        # Snap clip boundaries to sentence endings
        if transcript_words:
            end_second = _snap_to_sentence_end(transcript_words, start_second, end_second)

        # Auto-build tight segments: cut long pauses and isolated fillers
        if transcript_words and clean_fillers:
            auto_segments = _build_tight_segments(
                transcript_words, start_second, end_second,
            )
            if len(auto_segments) > 1:
                keep_segments = auto_segments
                duration = sum(s["end"] - s["start"] for s in keep_segments)
            else:
                keep_segments = None
                duration = end_second - start_second
        else:
            keep_segments = None
            duration = end_second - start_second

    if duration > 180:
        raise ValueError(f"Clip too long ({duration:.0f}s). Max 180 seconds for shorts.")

    # Load style config for branded-specific settings
    style_config = get_style(caption_style)

    # Create temp working directory
    work_dir = tempfile.mkdtemp(prefix="podcast_clip_")

    try:
        total_steps = 4 + (1 if outro_path and os.path.exists(str(outro_path)) else 0)

        # Step 1: Cut the segment(s) from the source video
        if progress_callback:
            n_segs = len(keep_segments) if keep_segments else 1
            msg = f"Cutting {n_segs} segment{'s' if n_segs > 1 else ''} (1/{total_steps})"
            progress_callback(10, msg)

        segment_path = os.path.join(work_dir, "segment.mp4")
        if keep_segments and len(keep_segments) > 1:
            cut_multi_segment(video_path, segment_path, keep_segments)
        else:
            cut_segment(video_path, segment_path, start_second, end_second)

        # Remap transcript words for multi-segment clips.
        # Needed before crop (speaker detection) and captions.
        if keep_segments and len(keep_segments) > 1 and transcript_words:
            remapped_words = []
            cumulative_t = 0.0
            for seg in keep_segments:
                seg_words = [
                    w for w in transcript_words
                    if w["end"] > seg["start"] and w["start"] < seg["end"]
                ]
                seg_duration = seg["end"] - seg["start"]
                for w in seg_words:
                    # Clamp to segment bounds to avoid negative/overflow timestamps
                    # for words that straddle a segment boundary
                    remapped_start = max(0, cumulative_t + (w["start"] - seg["start"]))
                    remapped_end = min(cumulative_t + seg_duration, cumulative_t + (w["end"] - seg["start"]))
                    if remapped_end > remapped_start:
                        remapped_words.append({
                            **w,
                            "start": round(remapped_start, 3),
                            "end": round(remapped_end, 3),
                        })
                cumulative_t += seg_duration
            crop_words = remapped_words
            crop_clip_start = 0
            caption_time_offset = 0
        else:
            crop_words = transcript_words
            crop_clip_start = start_second
            caption_time_offset = start_second

        # Step 2: Crop to vertical 9:16
        if progress_callback:
            progress_callback(30, f"Resizing for vertical format (2/{total_steps})")

        cropped_path = os.path.join(work_dir, "cropped.mp4")
        crop_to_vertical(
            segment_path, cropped_path,
            strategy=crop_strategy,
            transcript_words=crop_words,
            clip_start=crop_clip_start,
            face_map=face_map,
        )

        # Step 3: Generate captions + burn (with optional gradient & logo)
        if transcript_words:
            if progress_callback:
                progress_callback(50, f"Adding {caption_style} captions (3/{total_steps})")

            if keep_segments and len(keep_segments) > 1:
                clip_words = remapped_words
            else:
                clip_words = [
                    w for w in transcript_words
                    if w["end"] > start_second and w["start"] < end_second
                ]

            # Clean filler words
            if clean_fillers:
                clip_words = _clean_transcript_words(clip_words)

            if clip_words:
                # Generate ASS subtitle file
                ass_path = os.path.join(work_dir, "captions.ass")
                render_captions(
                    words=clip_words,
                    caption_style=caption_style,
                    output_path=ass_path,
                    time_offset=caption_time_offset,
                )

                # Burn captions into video
                if progress_callback:
                    progress_callback(65, f"Rendering captions into video (3/{total_steps})")

                captioned_path = os.path.join(work_dir, "captioned.mp4")

                # Branded style: add gradient overlay + logo
                use_gradient = style_config.get("gradient_overlay", False)
                gradient_opacity = style_config.get("gradient_opacity", 0.6)
                use_logo = style_config.get("logo_support", False) and logo_path

                burn_captions(
                    input_path=cropped_path,
                    ass_path=ass_path,
                    output_path=captioned_path,
                    gradient_overlay=use_gradient,
                    gradient_opacity=gradient_opacity,
                    logo_path=logo_path if use_logo else None,
                    logo_height=style_config.get("logo_height", 80),
                    logo_margin_x=style_config.get("logo_margin_x", 30),
                    logo_margin_y=style_config.get("logo_margin_y", 40),
                )
            else:
                captioned_path = cropped_path
        else:
            captioned_path = cropped_path

        # Step 4: Normalize audio
        if progress_callback:
            progress_callback(70, f"Balancing audio levels (4/{total_steps})")

        normalized_path = os.path.join(work_dir, "normalized.mp4")
        normalize_audio(captioned_path, normalized_path)

        # Step 5: Append outro (if provided)
        if outro_path and os.path.exists(outro_path):
            if progress_callback:
                progress_callback(85, f"Adding outro ({total_steps}/{total_steps})")

            with_outro_path = os.path.join(work_dir, "with_outro.mp4")
            concat_outro(normalized_path, outro_path, with_outro_path)
            final_video_path = with_outro_path
        else:
            final_video_path = normalized_path

        # Step 6: Move to output
        if progress_callback:
            progress_callback(95, "Saving final clip...")

        # Clean filename
        safe_title = "".join(c if c.isalnum() or c in "-_ " else "" for c in title)
        safe_title = safe_title.strip().replace(" ", "_")[:50]
        output_filename = f"{safe_title}_short.mp4"

        if output_dir:
            os.makedirs(output_dir, exist_ok=True)
            final_path = os.path.join(output_dir, output_filename)
        else:
            final_path = os.path.join(work_dir, output_filename)

        shutil.copy2(final_video_path, final_path)

        # Get file size
        file_size = os.path.getsize(final_path)
        file_size_mb = round(file_size / (1024 * 1024), 2)

        if progress_callback:
            progress_callback(100, "Clip complete!")

        return {
            "output_path": final_path,
            "duration": round(duration, 2),
            "file_size_mb": file_size_mb,
            "title": title,
            "start_second": start_second,
            "end_second": end_second,
            "caption_style": caption_style,
            "crop_strategy": crop_strategy,
        }

    finally:
        # Clean up temp files (but not if output is in work_dir)
        if output_dir and os.path.exists(work_dir):
            shutil.rmtree(work_dir, ignore_errors=True)
