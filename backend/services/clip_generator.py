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
import threading
from typing import Optional, Callable

from utils.proc import run as proc_run, ProcError
from utils.text import safe_filename
from config.paths import paths

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.caption_renderer import render_captions
from services.video_processor import (
    cut_segment,
    cut_multi_segment,
    crop_to_vertical,
    fit_to_frame,
    burn_captions,
    normalize_audio,
    concat_outro,
)
from config.caption_styles import get_style
from services.formats import get_format

_FILLER_WORDS = frozenset([
    "um", "uh", "uhh", "uhm", "umm", "hmm", "hm", "mhm",
    "ah", "er", "erm", "eh",
    "ummm", "ummmm", "ummmmm", "uhhh", "uhhhh", "uhhhhh",
    "hmmm", "hmmmm", "uhmm", "uhmmm",
    "mmm", "mmmm", "mhmm", "mmhm",
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
        result = proc_run(cmd, timeout=10, check=False)
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
        result = proc_run(cmd, timeout=90, check=False)
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
        result = proc_run(cmd, timeout=300, check=False)
        return result.returncode == 0 and os.path.exists(output_path)
    except Exception:
        return False


_output_path_lock = threading.Lock()
_reserved_output_paths: set[str] = set()


def _reserve_output_path(output_dir: str, stem: str, ext: str) -> str:
    """Claim an output path no other clip in this run can take.

    Titles come from an LLM and are not deduped, so two clips in one batch can
    share a stem. Rendering both to the same path would interleave the copy and
    the in-place autofix re-encode into one corrupt file while both report
    success. Reservations are per-process, so re-rendering a clip in a later run
    still overwrites its own output instead of piling up suffixes.
    """
    with _output_path_lock:
        candidate = os.path.join(output_dir, f"{stem}{ext}")
        n = 2
        while candidate in _reserved_output_paths:
            candidate = os.path.join(output_dir, f"{stem}-{n}{ext}")
            n += 1
        _reserved_output_paths.add(candidate)
        return candidate


def _reframe_can_jump(
    reframe: bool,
    crop_strategy: str,
    crop_keyframes: list = None,
    keep_segments: list = None,
) -> bool:
    """Whether this render can contain hard visual jumps worth an autofix pass.

    The pass is only skipped where a jump is impossible: no reframe at all, a
    fixed centre crop, or a manual crop held on a single keyframe. Every
    tracked strategy can snap the crop between subjects, so it gets smoothed.
    Speaker labels are not consulted: the default whisper.cpp engine skips
    diarization, so they are absent exactly when face tracking is doing the
    most snapping.
    """
    if keep_segments and len(keep_segments) > 1:
        return True
    if not reframe:
        return False
    if crop_strategy == "center":
        return False
    if crop_strategy == "manual":
        return bool(crop_keyframes and len(crop_keyframes) > 1)
    return True


def _transition_autofix_passes(jumps_possible: bool) -> int:
    raw = (os.environ.get("PODCLI_TRANSITION_AUTOFIX_PASSES") or "").strip()
    if raw:
        try:
            return max(0, min(int(raw), 2))
        except ValueError:
            pass
    return 2 if jumps_possible else 0


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
    """Snap clip end_second forward to the nearest sentence boundary.

    Forward-only. Stops at speaker changes to avoid bleeding into a reply.
    """
    SENTENCE_ENDINGS = ".!?"

    clip_words = [w for w in transcript_words if w["end"] > start_second and w["start"] < end_second]
    if not clip_words:
        return end_second

    last_clip_word = clip_words[-1]
    last_word_text = last_clip_word.get("word", "").strip()
    last_speaker = last_clip_word.get("speaker")

    if last_word_text and last_word_text[-1] in SENTENCE_ENDINGS:
        return max(end_second, last_clip_word["end"])

    for w in sorted(transcript_words, key=lambda x: x["start"]):
        if w["start"] < last_clip_word["end"]:
            continue
        if w["end"] > end_second + max_extension:
            break
        if last_speaker is not None and w.get("speaker") is not None and w.get("speaker") != last_speaker:
            break
        text = w.get("word", "").strip()
        if text and text[-1] in SENTENCE_ENDINGS:
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
    silence_threshold: float = 0.55,
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


def _kept_caption_overlay_path(output_path: str) -> str:
    base, _ = os.path.splitext(os.path.abspath(output_path))
    return f"{base}_captions.mov"


def _render_with_remotion(
    video_path: str,
    words: list[dict],
    caption_style: str,
    output_path: str,
    time_offset: float = 0.0,
    logo_path: Optional[str] = None,
    keep_caption_overlay: bool = False,
) -> tuple[bool, Optional[str]]:
    """
    Render captions using Remotion. Returns (success, optional_prores_overlay_path).
    """
    global _remotion_available
    import subprocess
    import json

    # Quick bail only if the environment is known unavailable for this session.
    # Transient per-clip render failures should not poison the rest of the batch.
    if _remotion_available is False:
        return False, None

    # Find the render script
    project_root = paths["project_root"]
    render_script = os.path.join(project_root, "remotion", "render.mjs")
    if not os.path.exists(render_script):
        _remotion_available = False
        return False, None

    # Check node is available
    node_path = os.environ.get("PODCLI_NODE") or shutil.which("node")
    if not node_path:
        _remotion_available = False
        return False, None

    # Cache the compiled bundle next to the render script. The compositions are
    # project-independent, so a single global bundle (in the managed runtime dir
    # for native installs) is reused across every project instead of rebuilt per
    # data/cache.
    bundle_cache_root = os.path.join(os.path.dirname(render_script), ".bundle-cache")
    remotion_env = {**os.environ, "PODCLI_CACHE_DIR": bundle_cache_root}

    cache_dir = os.path.join(bundle_cache_root, "remotion-bundle")
    bundle_index = os.path.join(cache_dir, "index.html")
    if not os.path.exists(bundle_index):
        try:
            r = subprocess.run(
                [node_path, render_script, "--prebundle"],
                timeout=180,
                cwd=project_root,
                env=remotion_env,
                capture_output=True,
                text=True,
            )
            if r.returncode != 0 or not os.path.exists(bundle_index):
                _remotion_available = False
                return False, None
        except Exception:
            _remotion_available = False
            return False, None

    # Prepare words JSON (adjust timestamps by offset)
    adjusted_words = []
    for w in words:
        adjusted = {
            "word": w.get("word", ""),
            "start": round(w["start"] - time_offset, 3),
            "end": round(w["end"] - time_offset, 3),
        }
        # The chunker starts a new caption card on a speaker change. Dropping the
        # field here left that rule dead in the render while the studio preview,
        # which keeps it, chunked the same words differently.
        if w.get("speaker") is not None:
            adjusted["speaker"] = w["speaker"]
        adjusted_words.append(adjusted)

    # Extract face Y positions so captions can avoid overlapping faces
    # Probe video dimensions
    face_y_norm = None  # normalized 0-1 (0=top, 1=bottom)
    try:
        probe_cmd = ["ffprobe", "-v", "error", "-select_streams", "v:0",
                     "-show_entries", "stream=height", "-of", "csv=p=0",
                     os.path.abspath(video_path)]
        vid_h = int(proc_run(probe_cmd, timeout=5, check=False).stdout.strip())

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
        with open(words_file, "w", encoding="utf-8") as f:
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
        if keep_caption_overlay:
            cmd.append("--keep-overlay")

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
                env=remotion_env,
            )

        if result.returncode == 0 and os.path.exists(output_path):
            _remotion_available = True
            overlay_path = None
            if keep_caption_overlay:
                overlay_path = _kept_caption_overlay_path(output_path)
                if not os.path.exists(overlay_path):
                    overlay_path = None
            return True, overlay_path

        stdout = result.stdout or ""
        if stdout:
            lines = [l.strip() for l in stdout.strip().split("\n") if l.strip()]
            if lines:
                print(f"  Remotion: {lines[-1][:120]}", file=sys.stderr, flush=True)

        print("  Remotion: falling back to ASS for this clip", file=sys.stderr, flush=True)
        return False, None

    except subprocess.TimeoutExpired:
        print("  Remotion: timed out, using ASS for this clip", file=sys.stderr, flush=True)
        return False, None
    except Exception:
        print("  Remotion: render error, using ASS for this clip", file=sys.stderr, flush=True)
        return False, None
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
    format: str = "vertical",
    crop_keyframes: list[dict] = None,
    transcript_words: list[dict] = None,
    title: str = "clip",
    output_dir: Optional[str] = None,
    face_map: dict = None,
    logo_path: Optional[str] = None,
    outro_path: Optional[str] = None,
    intro_path: Optional[str] = None,
    clean_fillers: bool = True,
    keep_segments: list[dict] = None,
    trim_opening: Optional[bool] = None,
    allow_ass_fallback: bool = False,
    use_ass_captions: bool = False,
    keep_caption_overlay: bool = False,
    progress_callback: Optional[Callable[[int, str], None]] = None,
) -> dict:
    """
    Generate a complete short-form video clip.

    Args:
        video_path: Path to source podcast video
        start_second: Clip start time
        end_second: Clip end time
        caption_style: "hormozi", "karaoke", "subtle", or "branded"
        crop_strategy: "center", "face", "speaker", or "speaker-hardcut"
        format: "vertical", "horizontal", or "square"
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
    # Auto-enable caption overlay export when an editor integration that needs it is on.
    if not keep_caption_overlay:
        try:
            from services.integrations.manager import IntegrationsManager
            if IntegrationsManager().is_enabled("davinci_resolve"):
                keep_caption_overlay = True
        except Exception:
            pass

    if not os.path.exists(video_path):
        raise FileNotFoundError(f"Video not found: {video_path}")

    if end_second <= start_second:
        raise ValueError("end_second must be greater than start_second")

    spec = get_format(format)

    if trim_opening is None:
        trim_opening = not (keep_segments and len(keep_segments) > 0)

    llm_start_second, llm_end_second = start_second, end_second
    orig_keep_segments = None
    if keep_segments and len(keep_segments) > 0:
        llm_start_second = keep_segments[0]["start"]
        llm_end_second = keep_segments[-1]["end"]
        orig_keep_segments = [dict(s) for s in keep_segments if s["end"] > s["start"]]
        llm_total = max(0.01, sum(s["end"] - s["start"] for s in orig_keep_segments))
    else:
        llm_total = max(0.01, llm_end_second - llm_start_second)

    # Multi-segment cutting: if keep_segments provided, use those ranges.
    # Otherwise auto-detect silences/fillers and build tight segments.
    if keep_segments and len(keep_segments) > 0:
        # Validate segments from Claude
        keep_segments = [s for s in keep_segments if s["end"] > s["start"]]
        keep_segments.sort(key=lambda s: s["start"])

        if trim_opening and transcript_words:
            trimmed_start = _trim_weak_opening(
                transcript_words,
                keep_segments[0]["start"],
                keep_segments[0]["end"],
            )
            if trimmed_start < keep_segments[0]["end"] - 0.5:
                keep_segments[0]["start"] = trimmed_start

        if transcript_words:
            snapped_end = _snap_to_sentence_end(
                transcript_words,
                keep_segments[-1]["start"],
                keep_segments[-1]["end"],
            )
            if snapped_end > keep_segments[-1]["end"]:
                keep_segments[-1]["end"] = snapped_end

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
        if trim_opening and transcript_words:
            start_second = _trim_weak_opening(transcript_words, start_second, end_second)

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

    if duration < 0.75 * llm_total and llm_total >= 8.0:
        print(
            f"  Boundary revert: post-trim duration {duration:.1f}s < "
            f"75% of asked {llm_total:.1f}s - using original range",
            file=sys.stderr,
            flush=True,
        )
        if orig_keep_segments:
            keep_segments = [dict(s) for s in orig_keep_segments]
            keep_segments.sort(key=lambda s: s["start"])
            start_second = keep_segments[0]["start"]
            end_second = keep_segments[-1]["end"]
            duration = sum(s["end"] - s["start"] for s in keep_segments)
        elif (llm_end_second - llm_start_second) <= spec.dur_max:
            start_second = llm_start_second
            end_second = llm_end_second
            keep_segments = None
            duration = end_second - start_second

    length_warning = None
    if duration > spec.dur_max:
        length_warning = f"{duration:.0f}s, over the {spec.dur_max}s {spec.name} target"
        print(f"  {length_warning}", file=sys.stderr, flush=True)

    # Load style config for branded-specific settings
    style_config = get_style(caption_style)

    # Create temp working directory
    work_dir = tempfile.mkdtemp(prefix="podcast_clip_")
    caption_overlay_path = None

    try:
        total_steps = 4 + (1 if outro_path and os.path.exists(str(outro_path)) else 0) + (
            1 if intro_path and os.path.exists(str(intro_path)) else 0
        )

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
            progress_callback(30, f"Resizing for {spec.name} format (2/{total_steps})")

        cropped_path = os.path.join(work_dir, "cropped.mp4")
        if spec.reframe:
            crop_to_vertical(
                segment_path, cropped_path,
                strategy=crop_strategy,
                transcript_words=crop_words,
                clip_start=crop_clip_start,
                face_map=face_map,
                crop_keyframes=crop_keyframes,
                target_dims=spec.dims,
            )
        else:
            fit_to_frame(segment_path, cropped_path, target_dims=spec.dims)

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

                remotion_ok = False
                caption_overlay_path = None
                if not use_ass_captions:
                    remotion_ok, caption_overlay_path = _render_with_remotion(
                        video_path=cropped_path,
                        words=clip_words,
                        caption_style=caption_style,
                        output_path=captioned_path,
                        time_offset=caption_time_offset,
                        logo_path=logo_path if (style_config.get("logo_support", False) and logo_path) else None,
                        keep_caption_overlay=keep_caption_overlay,
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

        # Step 5: Prepend intro, then append outro (if provided).
        # concat_outro joins two clips head-to-tail, so passing the intro first
        # puts it in front of the clip.
        final_video_path = normalized_path
        if intro_path and os.path.exists(intro_path):
            if progress_callback:
                progress_callback(83, "Adding intro")
            # concat_outro outputs at its first arg's dimensions, so match the
            # intro to the clip's frame or a landscape intro reshapes the clip.
            from services.video_processor import get_dimensions, scale_to_frame
            cw, ch = get_dimensions(final_video_path)
            intro_scaled = os.path.join(work_dir, "intro_scaled.mp4")
            scale_to_frame(intro_path, intro_scaled, cw, ch)
            with_intro_path = os.path.join(work_dir, "with_intro.mp4")
            concat_outro(intro_scaled, final_video_path, with_intro_path)
            final_video_path = with_intro_path

        if outro_path and os.path.exists(outro_path):
            if progress_callback:
                progress_callback(85, f"Adding outro ({total_steps}/{total_steps})")

            with_outro_path = os.path.join(work_dir, "with_outro.mp4")
            concat_outro(final_video_path, outro_path, with_outro_path)
            final_video_path = with_outro_path

        # Step 6: Move to output
        if progress_callback:
            progress_callback(95, "Saving final clip...")

        if output_dir:
            os.makedirs(output_dir, exist_ok=True)
            final_path = _reserve_output_path(output_dir, f"{safe_filename(title)}_short", ".mp4")
        else:
            fd, final_path = tempfile.mkstemp(
                prefix=f"{safe_filename(title)}_short_", suffix=".mp4"
            )
            os.close(fd)

        shutil.copy2(final_video_path, final_path)

        # Optional bounded QA/autofix pass for transition jumps.
        # Hard-capped to avoid any infinite rerender loop.
        max_autofix_passes = _transition_autofix_passes(
            _reframe_can_jump(
                reframe=spec.reframe,
                crop_strategy=crop_strategy,
                crop_keyframes=crop_keyframes,
                keep_segments=keep_segments,
            )
        )
        if max_autofix_passes > 0:
            if progress_callback:
                progress_callback(97, "Quality gate: checking transitions...")
            _auto_fix_transition_jumps(final_path, max_passes=max_autofix_passes)

        # Get file size
        file_size = os.path.getsize(final_path)
        file_size_mb = round(file_size / (1024 * 1024), 2)

        if progress_callback:
            progress_callback(100, "Clip complete!")

        out = {
            "output_path": final_path,
            "duration": round(duration, 2),
            "file_size_mb": file_size_mb,
            "title": title,
            "start_second": start_second,
            "end_second": end_second,
            "caption_style": caption_style,
            "crop_strategy": crop_strategy,
            "format": spec.name,
        }
        if length_warning:
            out["warning"] = length_warning
        if keep_caption_overlay and caption_overlay_path and os.path.exists(caption_overlay_path):
            # Copy out of work_dir before cleanup so the returned paths survive.
            base, _ = os.path.splitext(final_path)
            persisted_overlay = f"{base}_captions.mov"
            shutil.copy2(caption_overlay_path, persisted_overlay)
            out["caption_overlay_path"] = persisted_overlay
            if os.path.exists(cropped_path):
                persisted_source = f"{base}_source.mp4"
                shutil.copy2(cropped_path, persisted_source)
                out["cropped_source_path"] = persisted_source
        return out

    finally:
        if os.path.exists(work_dir):
            shutil.rmtree(work_dir, ignore_errors=True)
