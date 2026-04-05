"""
Clip generator — orchestrates the full pipeline for creating a short-form clip.

Pipeline: cut segment → crop to 9:16 → render captions → burn captions
          (+ optional gradient overlay + logo) → normalize audio
"""

import os
import sys
import tempfile
import shutil
import subprocess
import re
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

# Weak lead-in markers that often add setup without adding viral value.
# These are only used for opening-trim decisions (not caption deletion).
_WEAK_OPENING_WORDS = frozenset([
    "so", "well", "okay", "ok", "like", "you", "know",
    "right", "yeah", "yes", "actually", "basically",
]) | _FILLER_WORDS

_SCENE_TIME_RE = re.compile(r"pts_time:([0-9]+(?:\.[0-9]+)?)")


def _trim_weak_opening(
    words: list[dict],
    start_second: float,
    end_second: float,
    max_trim: float = 3.0,
    min_gain: float = 0.25,
) -> float:
    """
    Tighten clip opening toward the first substantive sentence.

    Rules:
    - Remove obvious dead air at the head.
    - Optionally skip a short run of weak opener words.
    - Never trim past max_trim seconds or into a likely hook ("?" / "!").
    """
    if not words or end_second <= start_second + 0.6:
        return start_second

    clip_words = sorted(
        [w for w in words if w["end"] > start_second and w["start"] < end_second],
        key=lambda w: w["start"],
    )
    if len(clip_words) < 2:
        return start_second

    candidate_start = start_second
    first_word = clip_words[0]

    # Dead air before first spoken word.
    if first_word["start"] - start_second >= 0.45:
        candidate_start = first_word["start"]

    weak_window_end = min(end_second, start_second + max_trim)
    weak_run_end = None
    saw_real_word_after_weak = False

    for w in clip_words:
        if w["start"] > weak_window_end:
            break
        raw = str(w.get("word", "")).strip()
        if not raw:
            continue
        # If the opening already has question/exclamation energy, keep it.
        if "?" in raw or "!" in raw:
            break
        bare = raw.lower().strip(".,!?;:-–—'\"")
        if bare in _WEAK_OPENING_WORDS:
            weak_run_end = max(float(w["end"]), weak_run_end or 0.0)
            continue
        saw_real_word_after_weak = True
        break

    if weak_run_end is not None and saw_real_word_after_weak:
        candidate_start = max(candidate_start, weak_run_end)

    candidate_start = min(candidate_start, start_second + max_trim, end_second - 0.5)
    if candidate_start - start_second >= min_gain:
        return round(candidate_start, 2)
    return start_second


def _get_media_duration(path: str) -> float:
    """Read media duration in seconds (best effort)."""
    try:
        cmd = [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=nk=1:nw=1",
            path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if result.returncode != 0:
            return 0.0
        return float((result.stdout or "0").strip() or 0.0)
    except Exception:
        return 0.0


def _detect_scene_cuts(path: str, threshold: float = 0.22, max_cuts: int = 32) -> list[float]:
    """
    Detect hard visual changes likely to feel like jump cuts.
    Returns scene-change timestamps in seconds.
    """
    try:
        cmd = [
            "ffmpeg", "-hide_banner",
            "-i", path,
            "-filter:v", f"select='gt(scene,{threshold})',showinfo",
            "-an",
            "-f", "null", "-",
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
        text = (result.stderr or "") + "\n" + (result.stdout or "")
        raw = [float(m.group(1)) for m in _SCENE_TIME_RE.finditer(text)]
        if not raw:
            return []
        cuts: list[float] = []
        for t in raw:
            if not cuts or abs(t - cuts[-1]) > 0.08:
                cuts.append(round(t, 3))
            if len(cuts) >= max_cuts:
                break
        return cuts
    except Exception:
        return []


def _select_problematic_scene_cuts(cuts: list[float], duration: float) -> list[float]:
    """
    Pick cuts that are most likely to feel bad:
    - clustered cuts (rapid switches)
    - tail-end cuts right before clip ends
    """
    if not cuts or duration <= 0:
        return []

    flagged: list[float] = []
    for i, t in enumerate(cuts):
        prev_t = cuts[i - 1] if i > 0 else None
        next_t = cuts[i + 1] if i + 1 < len(cuts) else None
        is_clustered = (
            (prev_t is not None and (t - prev_t) <= 0.9)
            or (next_t is not None and (next_t - t) <= 0.9)
        )
        is_tail = (duration - t) <= 2.0
        if is_clustered or is_tail:
            flagged.append(t)

    # Keep order and de-dup (safety)
    out: list[float] = []
    for t in flagged:
        if not out or abs(t - out[-1]) > 0.08:
            out.append(t)
    return out


def _apply_local_transition_smoothing(
    input_path: str,
    output_path: str,
    cut_times: list[float],
    pass_index: int = 0,
) -> bool:
    """Apply short blur masks around selected cut times (adaptive by pass)."""
    if not cut_times:
        return False

    # Pass 0: clean/light smoothing. Pass 1+: stronger smoothing.
    if pass_index <= 0:
        outer_pad = 0.15
        core_pad = 0.08
        outer_sigma = 1.8
        core_sigma = 3.2
    else:
        outer_pad = 0.20
        core_pad = 0.11
        outer_sigma = 2.2
        core_sigma = 4.0

    outer_windows = [(max(0.0, t - outer_pad), t + outer_pad) for t in cut_times]
    core_windows = [(max(0.0, t - core_pad), t + core_pad) for t in cut_times]

    outer_expr = "+".join(f"between(t,{a:.3f},{b:.3f})" for a, b in outer_windows)
    core_expr = "+".join(f"between(t,{a:.3f},{b:.3f})" for a, b in core_windows)

    vf = (
        f"gblur=sigma={outer_sigma:.1f}:steps=1:enable='{outer_expr}',"
        f"gblur=sigma={core_sigma:.1f}:steps=1:enable='{core_expr}'"
    )
    cmd = [
        "ffmpeg", "-y",
        "-i", input_path,
        "-vf", vf,
        "-c:v", "libx264", "-crf", "18", "-preset", "medium",
        "-c:a", "copy",
        "-movflags", "+faststart",
        output_path,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        return result.returncode == 0 and os.path.exists(output_path)
    except Exception:
        return False


def _auto_fix_transition_jumps(video_path: str, max_passes: int = 1) -> bool:
    """
    Bounded auto-fix for jumpy transitions. Never loops indefinitely.
    Returns True if at least one fix pass succeeded.
    """
    fixed_any = False
    if max_passes <= 0:
        return False

    current = video_path
    for i in range(max_passes):
        duration = _get_media_duration(current)
        scene_threshold = 0.22 if i == 0 else 0.18
        cuts = _detect_scene_cuts(current, threshold=scene_threshold)
        problematic = _select_problematic_scene_cuts(cuts, duration)
        if not problematic:
            break

        temp_out = current + f".autofix{i+1}.mp4"
        ok = _apply_local_transition_smoothing(
            current,
            temp_out,
            problematic,
            pass_index=i,
        )
        if not ok:
            break
        os.replace(temp_out, current)
        fixed_any = True

    return fixed_any


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
    silence_threshold: float = 0.8,
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

    if saved < 0.8 or len(segments) < 2:
        # Not worth the cuts — too little saved
        return [{"start": start_second, "end": end_second}]

    return segments


_remotion_available = None  # True/False/None — environment availability, not per-clip success


def _render_with_remotion(
    video_path: str,
    words: list[dict],
    caption_style: str,
    output_path: str,
    time_offset: float = 0.0,
    logo_path: Optional[str] = None,
) -> bool:
    """
    Render captions using Remotion. Returns True on success, False to fall back to ASS.
    """
    global _remotion_available
    import subprocess
    import json

    # Quick bail only if the environment is known unavailable for this session.
    # Transient per-clip render failures should not poison the rest of the batch.
    if _remotion_available is False:
        return False

    # Find the render script
    project_root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
    render_script = os.path.join(project_root, "remotion", "render.mjs")
    if not os.path.exists(render_script):
        _remotion_available = False
        return False

    # Check node is available
    node_path = shutil.which("node")
    if not node_path:
        _remotion_available = False
        return False

    # Pre-check: ensure bundle exists (prebundle if not)
    cache_dir = os.path.join(project_root, ".podcli", "cache", "remotion-bundle")
    bundle_index = os.path.join(cache_dir, "index.html")
    if not os.path.exists(bundle_index):
        # Try a quick prebundle
        try:
            r = subprocess.run(
                [node_path, render_script, "--prebundle"],
                capture_output=True, text=True, timeout=30, cwd=project_root,
            )
            if r.returncode != 0 or not os.path.exists(bundle_index):
                _remotion_available = False
                return False
        except Exception:
            _remotion_available = False
            return False

    # Prepare words JSON (adjust timestamps by offset)
    adjusted_words = []
    for w in words:
        adjusted_words.append({
            "word": w.get("word", ""),
            "start": round(w["start"] - time_offset, 3),
            "end": round(w["end"] - time_offset, 3),
        })

    # Extract face Y positions so captions can avoid overlapping faces
    # Probe video dimensions
    face_y_norm = None  # normalized 0-1 (0=top, 1=bottom)
    try:
        probe_cmd = ["ffprobe", "-v", "error", "-select_streams", "v:0",
                     "-show_entries", "stream=height", "-of", "csv=p=0",
                     os.path.abspath(video_path)]
        vid_h = int(subprocess.run(probe_cmd, capture_output=True, text=True, timeout=5).stdout.strip())

        # Get face center Y from the cropped video's face detection
        # Use a quick sample at the middle of the clip
        try:
            import cv2
            sys.path.insert(0, os.path.join(project_root, "backend"))
            from services.face_detector import create_detector, detect_faces
            cap = cv2.VideoCapture(os.path.abspath(video_path))
            if cap.isOpened():
                vid_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
                vid_h_actual = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
                dur = cap.get(cv2.CAP_PROP_FRAME_COUNT) / (cap.get(cv2.CAP_PROP_FPS) or 30)
                detector = create_detector(vid_w, vid_h_actual)
                if detector:
                    # Sample 5 frames
                    ys = []
                    for t in [dur * f for f in [0.1, 0.3, 0.5, 0.7, 0.9]]:
                        cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
                        ret, frame = cap.read()
                        if ret:
                            faces = detect_faces(detector, frame, vid_w, vid_h_actual)
                            for f in faces:
                                ys.append(f["cy"] / vid_h_actual)
                    if ys:
                        face_y_norm = sum(ys) / len(ys)
                cap.release()
        except Exception:
            pass
    except Exception:
        pass

    words_file = output_path + ".words.json"
    try:
        payload = {
            "words": adjusted_words,
            "faceY": face_y_norm,  # normalized face center Y (0-1), null if unknown
        }
        with open(words_file, "w") as f:
            json.dump(payload, f)

        cmd = [
            node_path, render_script,
            "--video", os.path.abspath(video_path),
            "--words", os.path.abspath(words_file),
            "--style", caption_style,
            "--output", os.path.abspath(output_path),
        ]
        if logo_path and os.path.exists(logo_path):
            cmd.extend(["--logo", os.path.abspath(logo_path)])

        # Redirect stderr to devnull to suppress Chrome/FFmpeg noise
        # (avoids buffer deadlock and terminal spam)
        with open(os.devnull, "w") as _devnull:
            result = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=_devnull,
                text=True,
                timeout=600,
                cwd=project_root,
            )

        if result.returncode == 0 and os.path.exists(output_path):
            _remotion_available = True
            return True

        # Log errors
        stdout = result.stdout or ""
        if stdout:
            lines = [l.strip() for l in stdout.strip().split("\n") if l.strip()]
            if lines:
                print(f"  Remotion: {lines[-1][:120]}", flush=True)

        print("  Remotion: falling back to ASS for this clip", flush=True)
        return False

    except subprocess.TimeoutExpired:
        print("  Remotion: timed out, using ASS for this clip", flush=True)
        return False
    except Exception as e:
        print("  Remotion: render error, using ASS for this clip", flush=True)
        return False
    finally:
        try:
            os.unlink(words_file)
        except Exception:
            pass


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
    allow_ass_fallback: bool = False,
    progress_callback: Optional[Callable[[int, str], None]] = None,
) -> dict:
    """
    Generate a complete short-form video clip.

    Args:
        video_path: Path to source podcast video
        start_second: Clip start time
        end_second: Clip end time
        caption_style: "hormozi", "karaoke", "subtle", or "branded"
        crop_strategy: "center", "face", or "speaker"
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

        # Tighten the opening of the first kept segment to reduce setup/preamble.
        if transcript_words:
            trimmed_start = _trim_weak_opening(
                transcript_words,
                keep_segments[0]["start"],
                keep_segments[0]["end"],
            )
            if trimmed_start < keep_segments[0]["end"] - 0.5:
                keep_segments[0]["start"] = trimmed_start

        start_second = keep_segments[0]["start"]
        end_second = keep_segments[-1]["end"]

        # If Claude returned a single segment, still auto-trim pauses within it.
        # Multiple segments means Claude made deliberate editorial cuts — trust those.
        if len(keep_segments) == 1 and transcript_words and clean_fillers:
            auto_segments = _build_tight_segments(
                transcript_words, start_second, end_second,
            )
            if len(auto_segments) > 1:
                keep_segments = auto_segments

        duration = sum(s["end"] - s["start"] for s in keep_segments)
    else:
        # Tighten weak setup at the head before sentence/end trimming.
        if transcript_words:
            start_second = _trim_weak_opening(transcript_words, start_second, end_second)

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

    if duration > 45:
        raise ValueError(f"Clip too long ({duration:.0f}s). Max 45 seconds for shorts.")

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
            # Filter words to just this clip's time range
            crop_words = [
                w for w in transcript_words
                if w["end"] > start_second and w["start"] < end_second
            ] if transcript_words else transcript_words
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

        # Step 3: Render captions (Remotion-first; ASS fallback optional)
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
                if progress_callback:
                    progress_callback(65, f"Rendering captions into video (3/{total_steps})")

                captioned_path = os.path.join(work_dir, "captioned.mp4")

                remotion_ok = _render_with_remotion(
                    video_path=cropped_path,
                    words=clip_words,
                    caption_style=caption_style,
                    output_path=captioned_path,
                    time_offset=caption_time_offset,
                    logo_path=logo_path if (style_config.get("logo_support", False) and logo_path) else None,
                )

                if not remotion_ok and not allow_ass_fallback:
                    raise RuntimeError(
                        "Remotion caption render failed. ASS fallback is disabled "
                        "(set allow_ass_fallback=true to permit fallback)."
                    )

                if not remotion_ok and allow_ass_fallback:
                    # Optional fallback: ASS subtitle burn-in
                    ass_path = os.path.join(work_dir, "captions.ass")
                    render_captions(
                        words=clip_words,
                        caption_style=caption_style,
                        output_path=ass_path,
                        time_offset=caption_time_offset,
                    )

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

        # Optional bounded QA/autofix pass for transition jumps.
        # Hard-capped to avoid any infinite rerender loop.
        max_autofix_passes = int(os.environ.get("PODCLI_TRANSITION_AUTOFIX_PASSES", "2") or "2")
        max_autofix_passes = max(0, min(max_autofix_passes, 2))
        if max_autofix_passes > 0:
            if progress_callback:
                progress_callback(97, "Quality gate: checking transitions...")
            _auto_fix_transition_jumps(final_path, max_passes=max_autofix_passes)

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
