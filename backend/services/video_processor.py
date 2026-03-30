"""
Video processing service using FFmpeg.

Handles: cutting segments, cropping to 9:16, burning captions,
audio normalization, and final encoding.
"""

import os
import subprocess
import json
from typing import Optional

from services.encoder import get_video_encode_flags
import sys

# Max time for any single FFmpeg call (seconds). Prevents infinite hangs.
_FFMPEG_TIMEOUT = 300

# Quality presets: name → (crf, preset)
# Lower CRF = higher quality = larger file. 18 is visually lossless.
QUALITY_PRESETS = {
    "low":    {"crf": "28", "preset": "fast"},       # ~2-4 MB/min, fast encode
    "medium": {"crf": "23", "preset": "medium"},     # ~4-8 MB/min, balanced
    "high":   {"crf": "18", "preset": "slow"},       # ~8-15 MB/min, great quality
    "max":    {"crf": "14", "preset": "slower"},      # ~15-30 MB/min, near-lossless
}

_quality = os.environ.get("PODCLI_QUALITY", "high")
_qp = QUALITY_PRESETS.get(_quality, QUALITY_PRESETS["high"])
CPU_FLAGS = ["-c:v", "libx264", "-crf", _qp["crf"], "-preset", _qp["preset"], "-profile:v", "high"]


def _run_ffmpeg_with_fallback(cmd_parts_before_enc: list, cmd_parts_after_enc: list, output_path: str, label: str = "encode") -> str:
    """
    Run an FFmpeg command with the best encoder. If it fails, retry with libx264.
    cmd = cmd_parts_before_enc + enc_flags + cmd_parts_after_enc + [output_path]
    """
    enc_flags = get_video_encode_flags()
    cmd = cmd_parts_before_enc + enc_flags + cmd_parts_after_enc + [output_path]

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=_FFMPEG_TIMEOUT)
    if result.returncode == 0:
        return output_path

    # If not already CPU, retry with libx264
    if enc_flags != CPU_FLAGS:
        print(f"Warning: HW encoder failed for {label}, falling back to libx264", file=sys.stderr)
        cmd_fallback = cmd_parts_before_enc + CPU_FLAGS + cmd_parts_after_enc + [output_path]
        result2 = subprocess.run(cmd_fallback, capture_output=True, text=True, timeout=_FFMPEG_TIMEOUT)
        if result2.returncode == 0:
            return output_path
        raise RuntimeError(f"FFmpeg {label} failed (both HW and CPU): {result2.stderr[-500:]}")

    raise RuntimeError(f"FFmpeg {label} failed: {result.stderr[-500:]}")


def _has_audio_stream(video_path: str) -> bool:
    """Check if a video file contains an audio stream."""
    try:
        info = get_video_info(video_path)
        return any(s.get("codec_type") == "audio" for s in info.get("streams", []))
    except Exception:
        return True  # Assume yes if we can't check


def get_video_info(video_path: str) -> dict:
    """Get video metadata via ffprobe."""
    cmd = [
        "ffprobe",
        "-v", "quiet",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        video_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=_FFMPEG_TIMEOUT)
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {result.stderr}")
    return json.loads(result.stdout)


def get_dimensions(video_path: str) -> tuple[int, int]:
    """Get video width and height."""
    info = get_video_info(video_path)
    for stream in info.get("streams", []):
        if stream.get("codec_type") == "video":
            return int(stream["width"]), int(stream["height"])
    raise ValueError(f"No video stream found in {video_path}")


def cut_segment(
    input_path: str,
    output_path: str,
    start_second: float,
    end_second: float,
) -> str:
    """
    Extract a time segment from a video file.
    Uses -ss before -i with re-encoding for frame-accurate timestamps.
    """
    duration = end_second - start_second

    cmd = [
        "ffmpeg", "-y",
        "-ss", str(start_second),
        "-i", input_path,
        "-t", str(duration),
        "-c:v", "libx264", "-crf", "18", "-preset", "fast", "-profile:v", "high",
        "-c:a", "aac", "-b:a", "192k",
        "-avoid_negative_ts", "make_zero",
        output_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=_FFMPEG_TIMEOUT)
    if result.returncode != 0:
        raise RuntimeError(f"FFmpeg cut failed: {result.stderr[-500:]}")
    return output_path


def cut_multi_segment(
    input_path: str,
    output_path: str,
    segments: list[dict],
) -> str:
    """
    Cut multiple time ranges from a video and concatenate them seamlessly.

    segments: [{"start": 10.5, "end": 25.0}, {"start": 30.2, "end": 45.0}]

    Each segment is cut individually with frame-accurate encoding, then
    concatenated with matching codec settings for gapless playback.
    """
    if len(segments) == 1:
        return cut_segment(input_path, output_path, segments[0]["start"], segments[0]["end"])

    work_dir = os.path.dirname(output_path) or "."
    part_paths = []

    try:
        # Cut each segment
        for i, seg in enumerate(segments):
            part_path = os.path.join(work_dir, f"_part_{i}.mp4")
            cut_segment(input_path, part_path, seg["start"], seg["end"])
            part_paths.append(part_path)

        # Build concat file
        concat_file = os.path.join(work_dir, "_concat_parts.txt")
        with open(concat_file, "w") as f:
            for p in part_paths:
                f.write(f"file '{os.path.abspath(p)}'\n")

        # Concatenate with stream copy — parts already have matching codecs
        # from cut_segment, so no re-encode needed
        cmd = [
            "ffmpeg", "-y",
            "-f", "concat", "-safe", "0",
            "-i", concat_file,
            "-c", "copy",
            "-movflags", "+faststart",
            output_path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=_FFMPEG_TIMEOUT)
        if result.returncode != 0:
            raise RuntimeError(f"FFmpeg concat failed: {result.stderr[-500:]}")

        return output_path

    finally:
        # Clean up temp parts
        for p in part_paths:
            if os.path.exists(p):
                os.remove(p)
        concat_file = os.path.join(work_dir, "_concat_parts.txt")
        if os.path.exists(concat_file):
            os.remove(concat_file)


def crop_to_vertical(
    input_path: str,
    output_path: str,
    strategy: str = "face",
    transcript_words: list = None,
    clip_start: float = 0,
    face_map: dict = None,
) -> str:
    """
    Crop/scale video to 1080x1920 (9:16 vertical).

    Strategies:
    - center: Take center column of the frame, scale to fit
    - face: Detect face position, center crop on face (falls back to center)
    - speaker: Like face, but switches to the active speaker using transcript
               word-level speaker labels. Falls back to face then center.

    transcript_words: Word dicts with 'speaker', 'start', 'end' keys (from Whisper+pyannote).
                      Used by 'face' strategy when speaker data is available.
    clip_start: The start time of this clip in the original video (for timestamp alignment).
    """
    width, height = get_dimensions(input_path)
    target_w, target_h = 1080, 1920
    target_ratio = target_w / target_h  # 0.5625

    source_ratio = width / height

    if strategy in ("face", "speaker"):
        result = _track_and_crop(
            input_path, output_path,
            width, height, target_w, target_h,
            transcript_words, clip_start,
        )
        if result:
            return result
        strategy = "center"

    if strategy == "center":
        if source_ratio > target_ratio:
            # Wide source with no face detected: blurred background + sharp center.
            # Scales source to fill 9:16 height → blur → overlay sharp fit-to-width.
            vf_complex = (
                f"split[bg][fg];"
                f"[bg]scale=-2:{target_h},crop={target_w}:{target_h}:(iw-{target_w})/2:0,"
                f"boxblur=25:3[bbg];"
                f"[fg]scale={target_w}:-2[sfg];"
                f"[bbg][sfg]overlay=0:(H-h)/2[v]"
            )
            return _run_ffmpeg_with_fallback(
                cmd_parts_before_enc=[
                    "ffmpeg", "-y",
                    "-i", input_path,
                    "-filter_complex", vf_complex,
                    "-map", "[v]", "-map", "0:a?",
                ],
                cmd_parts_after_enc=[
                    "-c:a", "aac",
                    "-b:a", "192k",
                    "-ar", "44100",
                    "-movflags", "+faststart",
                ],
                output_path=output_path,
                label="crop_blur_bg",
            )
        else:
            crop_w = width
            crop_h = int(crop_w / target_ratio)
            if crop_h > height:
                vf = f"scale={target_w}:-2,pad={target_w}:{target_h}:(ow-iw)/2:(oh-ih)/2:black"
            else:
                crop_y = (height - crop_h) // 2
                vf = f"crop={crop_w}:{crop_h}:0:{crop_y},scale={target_w}:{target_h}"

    return _run_ffmpeg_with_fallback(
        cmd_parts_before_enc=[
            "ffmpeg", "-y",
            "-i", input_path,
            "-vf", vf,
        ],
        cmd_parts_after_enc=[
            "-c:a", "aac",
            "-b:a", "192k",
            "-ar", "44100",
            "-movflags", "+faststart",
        ],
        output_path=output_path,
        label="crop",
    )


def _detect_split_screen(video_path: str, width: int, height: int) -> bool:
    """Quick check: is this a split-screen layout (two side-by-side cameras)?"""
    try:
        import cv2
        from services.face_detector import create_detector, detect_faces

        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            return False

        fps = cap.get(cv2.CAP_PROP_FPS) or 30
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        duration = total / fps

        detector = create_detector(width, height)
        if detector is None:
            cap.release()
            return False

        mid_x = width // 2

        frames_with_two_faces = 0
        total_sampled = 0

        for i in range(min(20, max(5, int(duration)))):
            t = (i + 1) * duration / (min(20, max(5, int(duration))) + 1)
            cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
            ret, frame = cap.read()
            if not ret:
                continue

            faces = detect_faces(detector, frame, width, height)

            left = False
            right = False
            for f in faces:
                if f["cx"] < mid_x:
                    left = True
                else:
                    right = True

            total_sampled += 1
            if left and right:
                frames_with_two_faces += 1

        cap.release()
        return total_sampled > 0 and frames_with_two_faces / total_sampled >= 0.5

    except Exception:
        return False


def _build_cam_expr(keyframes: list, duration: float, is_split: bool, max_parts: int = 80):
    """
    Build an FFmpeg crop_x expression from SmoothedCameraman keyframes.

    keyframes: [(time, crop_x), ...]
    Returns expression string, or None if too complex.
    """
    if not keyframes:
        return None
    # Ensure t=0 covered
    if keyframes[0][0] > 0.05:
        keyframes = [(0, keyframes[0][1])] + keyframes
    if len(keyframes) == 1:
        return str(keyframes[0][1])

    parts = []
    for i in range(len(keyframes) - 1):
        t0, x0 = keyframes[i]
        t1, x1 = keyframes[i + 1]
        dt = max(0.01, t1 - t0)
        jump = abs(x1 - x0)

        if jump < 2:
            # Negligible movement — hold
            parts.append(f"if(between(t\\,{t0:.3f}\\,{t1:.3f})\\,{x0}\\,")
        elif jump > 150 or (is_split and jump > 50):
            # Large jump / split-screen speaker switch: instant cut
            cut_t = round(t1 - 0.01, 3)
            parts.append(f"if(between(t\\,{t0:.3f}\\,{cut_t:.3f})\\,{x0}\\,")
            parts.append(f"if(between(t\\,{cut_t:.3f}\\,{t1:.3f})\\,{x1}\\,")
        else:
            # Smooth linear interpolation (camera pan)
            parts.append(
                f"if(between(t\\,{t0:.3f}\\,{t1:.3f})\\,"
                f"{x0}+(({x1}-{x0})*(t-{t0:.3f})/{dt:.3f})\\,"
            )

    if len(parts) > max_parts:
        return None

    return "".join(parts) + str(keyframes[-1][1]) + ")" * len(parts)


def _simplify_keyframes(keyframes: list, tolerance: int = 5) -> list:
    """Remove intermediate keyframes that lie on a line between neighbours."""
    if len(keyframes) <= 2:
        return keyframes
    result = [keyframes[0]]
    for i in range(1, len(keyframes) - 1):
        t_prev, x_prev = result[-1]
        t_curr, x_curr = keyframes[i]
        t_next, x_next = keyframes[i + 1]
        dt_total = t_next - t_prev
        if dt_total < 0.01:
            continue
        expected = x_prev + (x_next - x_prev) * (t_curr - t_prev) / dt_total
        if abs(x_curr - expected) > tolerance:
            result.append(keyframes[i])
    result.append(keyframes[-1])
    return result


def _track_and_crop(
    input_path: str,
    output_path: str,
    width: int,
    height: int,
    target_w: int,
    target_h: int,
    transcript_words: list = None,
    clip_start: float = 0,
) -> Optional[str]:
    """
    Adaptive face tracking with exponential-smoothing camera.

    Works for both split-screen and single-camera layouts:
    1. Dense face sampling (~10 fps) with YuNet
    2. Transcript-driven speaker selection (who's talking → which face)
    3. Exponential-smoothing camera with dead zone (no jitter)
    4. Hard cuts only for split-screen speaker switches
    5. Vertical position from median face-Y (static per clip)
    """
    try:
        import cv2
        from services.face_detector import create_detector, detect_faces
    except ImportError:
        return None

    target_ratio = target_w / target_h  # 0.5625

    cap = cv2.VideoCapture(input_path)
    if not cap.isOpened():
        return None

    fps = cap.get(cv2.CAP_PROP_FPS) or 30
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    duration = total_frames / fps if total_frames > 0 else 0

    if duration < 0.5:
        cap.release()
        return None

    detector = create_detector(width, height)
    if detector is None:
        cap.release()
        return None

    # ── Dense face sampling (~10 fps) ────────────────────────────
    sample_step = max(1, int(fps / 10))
    detections = []  # [(time, faces), ...]

    frame_idx = 0
    while frame_idx < total_frames:
        cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
        ret, frame = cap.read()
        if not ret:
            frame_idx += sample_step
            continue
        t = frame_idx / fps
        faces = detect_faces(detector, frame, width, height)
        detections.append((t, faces))
        frame_idx += sample_step

    cap.release()

    if not detections:
        return None

    face_frames = sum(1 for _, faces in detections if faces)
    if face_frames < 3:
        return None

    # ── Detect layout from face data ────────────────────────────
    mid_x = width // 2
    split_count = sum(1 for _, faces in detections if len(faces) >= 2)
    has_any_split = split_count >= 3  # enough multi-face frames to build side mapping

    # ── Crop dimensions ──────────────────────────────────────────
    # Full-height crop: the 16:9 → 9:16 aspect conversion already
    # gives ~3× horizontal zoom. Adding vertical zoom on top clips
    # faces on laptop-angle cameras and shows ceiling. Keep it
    # simple: crop the full height, only pan horizontally.
    crop_h = height
    crop_w = int(crop_h * target_ratio)
    if crop_w > width:
        crop_w = width
        crop_h = int(crop_w / target_ratio)

    # ── Speaker segments from transcript ─────────────────────────
    segments = []  # [(start, end, speaker), ...]
    if transcript_words:
        cur_sp = None
        seg_start = 0.0
        for w in sorted(transcript_words, key=lambda x: x["start"]):
            sp = w.get("speaker")
            t = max(0.0, w["start"] - clip_start)
            if sp != cur_sp and sp is not None:
                if cur_sp is not None:
                    segments.append((seg_start, t, cur_sp))
                cur_sp = sp
                seg_start = t
        if cur_sp:
            segments.append((seg_start, duration, cur_sp))
        # Merge short segments (<2s) — brief interjections ("yeah",
        # "right") shouldn't trigger camera moves.  Absorb them into
        # the previous speaker's turn so the camera holds steady.
        merged = []
        for seg in segments:
            if merged and seg[2] == merged[-1][2]:
                merged[-1] = (merged[-1][0], seg[1], seg[2])
            elif merged and (seg[1] - seg[0]) < 2.0:
                merged[-1] = (merged[-1][0], seg[1], merged[-1][2])
            else:
                merged.append(list(seg))
        segments = merged

    speakers = sorted(set(seg[2] for seg in segments)) if segments else []

    def _speaker_at(t):
        for s, e, sp in segments:
            if s <= t <= e:
                return sp
        return segments[0][2] if segments else None

    # ── Speaker-to-side mapping ─────────────────────────────────
    # Built from ANY frames with 2+ faces — works for both
    # pure split-screen and Riverside-style mixed layouts.
    speaker_side = {}  # speaker → "left" | "right"
    if has_any_split and len(speakers) >= 2:
        talk = {}
        first = {}
        for seg in segments:
            sp = seg[2]
            talk[sp] = talk.get(sp, 0) + (seg[1] - seg[0])
            if sp not in first:
                first[sp] = seg[0]
        top2 = sorted(speakers, key=lambda s: talk.get(s, 0), reverse=True)[:2]
        by_first = sorted(top2, key=lambda s: first.get(s, float("inf")))
        speaker_side[by_first[0]] = "left"
        if len(by_first) > 1:
            speaker_side[by_first[1]] = "right"
        for sp in speakers:
            if sp not in speaker_side:
                speaker_side[sp] = "left"

    # ── Face selection per frame ─────────────────────────────────
    # Per-frame layout detection: if THIS frame has 2+ faces,
    # pick the one on the active speaker's side. Handles Riverside
    # videos that switch between single-camera and split-screen.
    def _pick_face(faces, speaker):
        if not faces:
            return None
        if len(faces) >= 2 and speaker in speaker_side:
            side = speaker_side[speaker]
            return (min if side == "left" else max)(faces, key=lambda f: f["cx"])
        # Single face or no side mapping: use largest (most prominent)
        return max(faces, key=lambda f: f["fw"])

    # ── Exponential-smoothing camera ────────────────────────────
    # The camera "chases" the target face using exponential decay,
    # which gives natural ease-out motion (fast start, gradual
    # settling). Unlike fixed-speed approaches this is frame-rate
    # independent and feels organic.
    #
    # dead_zone:     ignore face drift within this radius of the
    #                current camera center — prevents micro-jitter
    # smooth_rate:   convergence speed; at rate 4 the camera
    #                reaches ~95 % of the target in ≈0.75 s
    # snap_distance: teleport instantly above this (speaker change
    #                or layout switch — no sweeping across the seam)
    import math

    dead_zone = crop_w * 0.20
    smooth_rate = 4.0
    snap_distance = crop_w * 0.55

    # Initialise camera on the first detected face
    cam_x = float(width) / 2
    for _, faces in detections:
        if faces:
            cam_x = float(max(faces, key=lambda f: f["fw"])["cx"])
            break

    keyframes_x = []
    prev_t = 0.0
    prev_speaker = None
    last_snap_t = -10.0  # time of last force_snap (start with no cooldown)
    snap_cooldown = 2.0   # minimum seconds between force_snaps

    for t, faces in detections:
        speaker = _speaker_at(t) if segments else None
        face = _pick_face(faces, speaker)

        if face is None:
            prev_t = t
            continue

        target_x = float(face["cx"])
        diff = target_x - cam_x
        dt = max(0.01, t - prev_t)

        # Hard cut when the active speaker switches to a different
        # side.  Works for pure split-screen AND mixed layouts
        # (Riverside-style single↔split transitions).
        # Cooldown prevents rapid ping-pong from brief interjections
        # that survive segment merging.
        force_snap = (
            speaker is not None
            and speaker != prev_speaker
            and prev_speaker is not None
            and speaker_side.get(speaker) != speaker_side.get(prev_speaker)
            and (t - last_snap_t) >= snap_cooldown
        )

        if force_snap or abs(diff) > snap_distance:
            # Smooth 2-second pan to the other speaker instead of instant snap
            # Rate 1.5 with dt~0.1 gives alpha~0.14 per step → reaches 95% in ~2s
            pan_alpha = 1.0 - math.exp(-1.5 * dt)
            cam_x += diff * pan_alpha
            if force_snap:
                last_snap_t = t
        elif abs(diff) > dead_zone:
            # Exponential smoothing: move a fraction of remaining
            # distance each step.  alpha → 1 means instant, → 0 means
            # no movement.  exp(-rate * dt) makes it time-step safe.
            alpha = 1.0 - math.exp(-smooth_rate * dt)
            cam_x += diff * alpha
        # else: face inside dead zone — camera holds position

        crop_x = int(cam_x - crop_w / 2)
        crop_x = max(0, min(crop_x, width - crop_w))

        if not keyframes_x or crop_x != keyframes_x[-1][1]:
            keyframes_x.append((round(t, 3), crop_x))

        prev_t = t
        if speaker is not None:
            prev_speaker = speaker

    # ── Vertical position ────────────────────────────────────────
    # Full-height crop → crop_y is always 0 (or centered if
    # crop_h < height due to aspect-ratio clamping).
    crop_y = max(0, (height - crop_h) // 2)

    # ── Build FFmpeg filter ──────────────────────────────────────
    if not keyframes_x:
        crop_x = max(0, (width - crop_w) // 2)
        vf = f"crop={crop_w}:{crop_h}:{crop_x}:{crop_y},scale={target_w}:{target_h}"
    elif len(keyframes_x) == 1:
        vf = f"crop={crop_w}:{crop_h}:{keyframes_x[0][1]}:{crop_y},scale={target_w}:{target_h}"
    else:
        # Simplify keyframes to reduce expression complexity
        keyframes_x = _simplify_keyframes(keyframes_x)
        x_expr = _build_cam_expr(keyframes_x, duration, has_any_split)
        if x_expr is None:
            # Too complex — fall back to median position
            xs = [x for _, x in keyframes_x]
            med_x = sorted(xs)[len(xs) // 2]
            vf = f"crop={crop_w}:{crop_h}:{med_x}:{crop_y},scale={target_w}:{target_h}"
        else:
            vf = f"crop={crop_w}:{crop_h}:{x_expr}:{crop_y},scale={target_w}:{target_h}"

    return _run_ffmpeg_with_fallback(
        cmd_parts_before_enc=["ffmpeg", "-y", "-i", input_path, "-vf", vf],
        cmd_parts_after_enc=[
            "-c:a", "aac", "-b:a", "192k", "-ar", "44100",
            "-movflags", "+faststart",
        ],
        output_path=output_path, label="crop_track",
    )


def _crop_split_screen(
    input_path: str,
    output_path: str,
    width: int,
    height: int,
    target_w: int,
    target_h: int,
    transcript_words: list,
    clip_start: float,
) -> Optional[str]:
    """
    Split-screen crop: cut each speaker segment with a static crop on their
    camera half. No complex expressions — just separate FFmpeg calls + concat.

    This is how production tools (Opus Clip, Descript, etc.) handle it:
    static crop per speaker, hard cut between segments.
    """
    try:
        import cv2
        import numpy as np
        from services.face_detector import create_detector, detect_faces

        target_ratio = target_w / target_h
        mid_x = width // 2

        # Detect face position in each half
        cap = cv2.VideoCapture(input_path)
        if not cap.isOpened():
            return None

        fps = cap.get(cv2.CAP_PROP_FPS) or 30
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        duration = total_frames / fps

        detector = create_detector(width, height)
        if detector is None:
            cap.release()
            return None

        # Collect face positions per half AND per-frame face counts
        # to distinguish split-screen frames from single-person frames
        left_faces = []   # (cx_in_half, cy)
        right_faces = []  # (cx_in_half, cy)
        single_faces = []  # (cx_in_full, cy) — faces from single-person frames

        sample_count = min(40, max(10, int(duration * 2)))
        split_frame_count = 0
        single_frame_count = 0
        # Record per-sample face count for per-segment layout lookup
        sample_face_counts = []  # (time, num_faces)

        for i in range(sample_count):
            t = (i + 1) * duration / (sample_count + 1)
            cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
            ret, frame = cap.read()
            if not ret:
                continue

            faces = detect_faces(detector, frame, width, height)
            sample_face_counts.append((t, len(faces)))

            if len(faces) >= 2:
                # Split-screen frame
                split_frame_count += 1
                for f in faces:
                    cx, cy = f["cx"], f["cy"]
                    if cx < mid_x:
                        left_faces.append((cx, cy))
                    else:
                        right_faces.append((cx - mid_x, cy))
            elif len(faces) == 1:
                # Single-person frame
                single_frame_count += 1
                single_faces.append((faces[0]["cx"], faces[0]["cy"]))

        cap.release()

        if not left_faces and not right_faces and not single_faces:
            return None

        is_mixed = split_frame_count >= 3 and single_frame_count >= 3

        # Compute static crop for each half (split-screen segments)
        half_w = mid_x
        # Vertical zoom: 75% of frame height for tighter face framing
        adj_h = int(height * 0.75)
        adj_w = int(adj_h * target_ratio)
        if adj_w > half_w:
            # Zoomed crop wider than half — fall back to full height
            adj_h = height
            adj_w = int(height * target_ratio)

        def _compute_split_crop(faces, half_offset):
            """Compute crop for a split-screen half (zoomed to face)."""
            if not faces:
                cx_in_half = half_w // 2
                crop_x = max(0, min(cx_in_half - adj_w // 2, half_w - adj_w))
                return half_offset + crop_x, max(0, (height - adj_h) // 2), adj_h

            cx = int(np.median([f[0] for f in faces]))
            cy = int(np.median([f[1] for f in faces]))

            crop_x = cx - adj_w // 2
            crop_x = max(0, min(crop_x, half_w - adj_w))
            crop_x += half_offset

            # Vertical: place face at ~33% from top
            crop_y = cy - int(adj_h * 0.33)
            crop_y = max(0, min(crop_y, height - adj_h))

            return crop_x, crop_y, adj_h

        def _compute_single_crop(faces):
            """Compute crop for single-person fullscreen (with same zoom for consistency)."""
            if not faces:
                return (width - adj_w) // 2, max(0, (height - adj_h) // 2), adj_h

            cx = int(np.median([f[0] for f in faces]))
            cy = int(np.median([f[1] for f in faces]))

            crop_x = cx - adj_w // 2
            crop_x = max(0, min(crop_x, width - adj_w))

            crop_y = cy - int(adj_h * 0.33)
            crop_y = max(0, min(crop_y, height - adj_h))

            return crop_x, crop_y, adj_h

        left_crop_x, left_crop_y, left_h = _compute_split_crop(left_faces, 0)
        right_crop_x, right_crop_y, right_h = _compute_split_crop(right_faces, mid_x)
        single_crop_x, single_crop_y, single_h = _compute_single_crop(single_faces)

        left_w = int(left_h * target_ratio)
        right_w = int(right_h * target_ratio)
        single_w = int(single_h * target_ratio)

        # Map speakers to sides (first speaker by time = left)
        speakers = sorted(set(w.get("speaker") for w in transcript_words if w.get("speaker")))

        if len(speakers) < 2:
            # Single speaker — if mixed layout, prefer fullscreen crop; else use best half
            if is_mixed and single_faces:
                vf = f"crop={single_w}:{single_h}:{single_crop_x}:{single_crop_y},scale={target_w}:{target_h}"
            elif len(left_faces) >= len(right_faces):
                vf = f"crop={left_w}:{left_h}:{left_crop_x}:{left_crop_y},scale={target_w}:{target_h}"
            else:
                vf = f"crop={right_w}:{right_h}:{right_crop_x}:{right_crop_y},scale={target_w}:{target_h}"
            return _run_ffmpeg_with_fallback(
                cmd_parts_before_enc=["ffmpeg", "-y", "-i", input_path, "-vf", vf],
                cmd_parts_after_enc=["-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-movflags", "+faststart"],
                output_path=output_path, label="crop_split",
            )

        # Map speakers: sort by talk time, first by appearance = left
        speaker_talk = {}
        speaker_first = {}
        for w in transcript_words:
            sp = w.get("speaker")
            if sp:
                speaker_talk[sp] = speaker_talk.get(sp, 0) + (w["end"] - w["start"])
                if sp not in speaker_first:
                    speaker_first[sp] = w["start"]

        top_2 = sorted(speakers, key=lambda s: speaker_talk.get(s, 0), reverse=True)[:2]
        top_2_by_first = sorted(top_2, key=lambda s: speaker_first.get(s, float("inf")))

        speaker_side = {}
        speaker_side[top_2_by_first[0]] = "left"
        if len(top_2_by_first) > 1:
            speaker_side[top_2_by_first[1]] = "right"
        for sp in speakers:
            if sp not in speaker_side:
                speaker_side[sp] = speaker_side.get(top_2_by_first[0], "left")

        # Build speaker segments (times relative to the cut segment, clamped to [0, duration])
        segments = []
        cur_speaker = None
        seg_start = 0

        for w in sorted(transcript_words, key=lambda x: x["start"]):
            sp = w.get("speaker")
            t = max(0, min(w["start"] - clip_start, duration))
            if sp != cur_speaker and sp is not None:
                if cur_speaker is not None:
                    segments.append((seg_start, t, cur_speaker))
                cur_speaker = sp
                seg_start = t

        if cur_speaker:
            segments.append((seg_start, duration, cur_speaker))

        # Merge short segments (<1s)
        merged = []
        for seg in segments:
            if merged and seg[2] == merged[-1][2]:
                merged[-1] = (merged[-1][0], seg[1], seg[2])
            elif merged and (seg[1] - seg[0]) < 1.0:
                merged[-1] = (merged[-1][0], seg[1], merged[-1][2])
            else:
                merged.append(list(seg))
        segments = merged

        if not segments:
            return None

        # For mixed layouts, detect per-segment layout by sampling a frame
        # from the original (uncut) video at the segment's absolute time
        def _is_segment_split(seg_start_t):
            """Check if a segment's time falls in a split-screen or single-person region.
            Uses the pre-computed sample_face_counts instead of re-opening the video."""
            if not is_mixed:
                return True  # Pure split-screen, all segments use half crops
            # Find the nearest sampled frame to this segment's start time
            best_dist = float("inf")
            best_count = 2  # default to split
            for t, n in sample_face_counts:
                dist = abs(t - seg_start_t)
                if dist < best_dist:
                    best_dist = dist
                    best_count = n
            return best_count >= 2

        # Cut each segment with its speaker's crop, then concat
        work_dir = os.path.dirname(output_path) or "."
        part_paths = []

        try:
            for i, (start_t, end_t, speaker) in enumerate(segments):
                # Skip segments shorter than 1 frame (~0.04s at 24fps)
                if end_t - start_t < 0.05:
                    continue

                # For mixed layouts: detect if this segment is split or single
                seg_is_split = _is_segment_split(start_t)

                if seg_is_split:
                    side = speaker_side.get(speaker, "left")
                    if side == "left":
                        cw, ch, cx, cy = left_w, left_h, left_crop_x, left_crop_y
                    else:
                        cw, ch, cx, cy = right_w, right_h, right_crop_x, right_crop_y
                else:
                    # Single-person fullscreen — use full-frame crop (no vertical zoom)
                    cw, ch, cx, cy = single_w, single_h, single_crop_x, single_crop_y

                part_path = os.path.join(work_dir, f"_speaker_part_{i}.mp4")
                vf = f"crop={cw}:{ch}:{cx}:{cy},scale={target_w}:{target_h}"

                seg_duration = end_t - start_t

                cmd = [
                    "ffmpeg", "-y",
                    "-ss", str(start_t), "-t", str(seg_duration),
                    "-i", input_path,
                    "-vf", vf,
                    "-c:v", "libx264", "-crf", "18", "-preset", "fast",
                    "-c:a", "aac", "-b:a", "192k",
                    "-avoid_negative_ts", "make_zero",
                    part_path,
                ]
                r = subprocess.run(cmd, capture_output=True, text=True, timeout=_FFMPEG_TIMEOUT)
                if r.returncode != 0:
                    print(f"Warning: split-screen segment {i} failed: {r.stderr[-200:]}", file=sys.stderr)
                    continue
                part_paths.append(part_path)

            if not part_paths:
                return None

            # Concat all parts
            concat_file = os.path.join(work_dir, "_speaker_concat.txt")
            with open(concat_file, "w") as f:
                for p in part_paths:
                    f.write(f"file '{os.path.abspath(p)}'\n")

            cmd = [
                "ffmpeg", "-y",
                "-f", "concat", "-safe", "0",
                "-i", concat_file,
                "-c", "copy",
                "-movflags", "+faststart",
                output_path,
            ]
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=_FFMPEG_TIMEOUT)
            if r.returncode != 0:
                return None

            return output_path

        finally:
            for p in part_paths:
                if os.path.exists(p):
                    os.remove(p)
            concat_path = os.path.join(work_dir, "_speaker_concat.txt")
            if os.path.exists(concat_path):
                os.remove(concat_path)

    except Exception as e:
        import traceback
        print(f"Warning: split-screen crop failed: {e}\n{traceback.format_exc()}", file=sys.stderr)
        return None


def _use_face_map(
    face_map: dict,
    transcript_words: list,
    clip_start: float,
    width: int,
    height: int,
    target_ratio: float,
    crop_h: int = None,
) -> Optional[str]:
    """
    Use pre-computed face_map from transcription to determine crop position.
    Much faster than re-scanning the video.

    For multi-speaker clips: builds speaker-aware panning expression.
    For single-speaker: returns the dominant speaker's crop position.
    crop_h: if set, compute crop width from this height (for vertical zoom).
    """
    clusters = face_map.get("clusters", [])
    speaker_mappings = face_map.get("speaker_mappings", {})
    dominant = face_map.get("dominant_speaker")
    is_split_screen = face_map.get("is_split_screen", False)
    crop_w = int((crop_h or height) * target_ratio)
    mid_x = width // 2
    seam_margin = 20

    if not clusters:
        return None

    # Verify face_map was computed at the same resolution
    map_w = face_map.get("video_width", width)
    if map_w != width:
        return None

    # Recompute crop_x from center_x with current crop_w (may differ from
    # stored values if vertical zoom changes the crop width).
    clusters = [dict(cl) for cl in clusters]
    for cl in clusters:
        cx = cl["center_x"]
        if is_split_screen and len(clusters) >= 2:
            if cx < mid_x:
                cl["crop_x"] = max(0, min(cx - crop_w // 2, mid_x - crop_w - seam_margin))
            else:
                cl["crop_x"] = max(mid_x + seam_margin, min(cx - crop_w // 2, width - crop_w))
        else:
            cl["crop_x"] = max(0, min(cx - crop_w // 2, width - crop_w))

    # Check if clip has multiple speakers
    speakers_in_clip = set()
    if transcript_words:
        speakers_in_clip = set(w.get("speaker") for w in transcript_words if w.get("speaker"))

    if len(speakers_in_clip) < 2 or len(clusters) < 2:
        # Single speaker — use their cluster or the dominant one
        for sp in speakers_in_clip:
            ci = speaker_mappings.get(sp)
            if ci is not None and ci < len(clusters):
                return str(clusters[ci]["crop_x"])
        if dominant and dominant in speaker_mappings:
            ci = speaker_mappings[dominant]
            if ci < len(clusters):
                return str(clusters[ci]["crop_x"])
        return str(clusters[0]["crop_x"])

    # Multi-speaker: build panning expression from speaker segments
    # Group words into speaker segments
    segments = []
    current_speaker = None
    seg_start = 0
    clip_end = max(w["end"] for w in transcript_words) if transcript_words else 0

    for w in sorted(transcript_words, key=lambda x: x["start"]):
        sp = w.get("speaker")
        t = max(0, w["start"] - clip_start)
        if sp != current_speaker and sp is not None:
            if current_speaker is not None:
                segments.append((seg_start, t, current_speaker))
            current_speaker = sp
            seg_start = t

    if current_speaker is not None:
        segments.append((seg_start, clip_end - clip_start, current_speaker))

    if not segments:
        return str(clusters[0]["crop_x"])

    # Merge short segments (<1.0s)
    merged = []
    for seg in segments:
        if merged and seg[2] == merged[-1][2]:
            merged[-1] = (merged[-1][0], seg[1], seg[2])
        elif merged and (seg[1] - seg[0]) < 1.0:
            merged[-1] = (merged[-1][0], seg[1], merged[-1][2])
        else:
            merged.append(list(seg))
    segments = merged

    # Default position
    default_ci = speaker_mappings.get(dominant, 0)
    default_x = clusters[min(default_ci, len(clusters) - 1)]["crop_x"]

    # Build keyframes — instant cut for split-screen, smooth pan otherwise
    pan_duration = 0.0 if is_split_screen else 0.4
    duration = segments[-1][1] if segments else 1.0
    keyframes = []
    prev_x = default_x

    for start_t, end_t, speaker in segments:
        ci = speaker_mappings.get(speaker)
        target_x = clusters[ci]["crop_x"] if ci is not None and ci < len(clusters) else default_x

        if target_x != prev_x:
            if pan_duration > 0:
                keyframes.append((start_t, prev_x))
                keyframes.append((start_t + pan_duration, target_x))
            else:
                # Instant snap — single keyframe at new position, no transition frames
                keyframes.append((start_t, target_x))
        prev_x = target_x

    if not keyframes:
        return str(default_x)

    # Build FFmpeg expression
    expr_parts = []
    for i in range(0, len(keyframes) - 1, 2):
        t0, x0 = keyframes[i]
        t1, x1 = keyframes[i + 1]
        dt = max(0.01, t1 - t0)
        expr_parts.append(
            f"if(between(t\\,{t0:.2f}\\,{t1:.2f})\\,"
            f"{x0}+(({x1}-{x0})*(t-{t0:.2f})/{dt:.2f})\\,"
        )
        next_t = keyframes[i + 2][0] if i + 2 < len(keyframes) else duration
        expr_parts.append(
            f"if(between(t\\,{t1:.2f}\\,{next_t:.2f})\\,"
            f"{x1}\\,"
        )

    if len(expr_parts) > 30:
        return str(default_x)

    return "".join(expr_parts) + str(default_x) + ")" * len(expr_parts)


def _build_speaker_aware_crop(
    video_path: str,
    width: int,
    height: int,
    target_ratio: float,
    transcript_words: list,
    clip_start: float,
) -> Optional[str]:
    """
    Build a speaker-aware crop that pans to whoever is speaking.

    Layout-adaptive: handles Riverside-style recordings that dynamically
    switch between split-screen (both faces visible) and single-person
    (one speaker fullscreen) layouts. Instead of static cluster positions,
    tracks the actual face position per frame and adapts the crop to
    wherever the active speaker's face actually is.

    Returns (x_expr, y_expr, adjusted_h) tuple or None if detection fails.
    """
    try:
        import cv2
        import numpy as np
        from services.face_detector import create_detector, detect_faces

        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            return None

        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        fps = cap.get(cv2.CAP_PROP_FPS) or 30
        duration = total_frames / fps

        detector = create_detector(width, height)
        if detector is None:
            cap.release()
            return None

        # Zoom to 75% of frame height for tight face framing
        adjusted_h = int(height * 0.75)
        adj_w = int(adjusted_h * target_ratio)
        if adj_w > width:
            adjusted_h = height
            adj_w = int(height * target_ratio)
        mid_x = width // 2

        # ── Phase 1: Dense frame sampling ──────────────────────────────
        # Record ALL faces per frame, plus per-frame layout classification.
        sample_count = min(150, max(40, int(duration * 6)))

        # Per-frame data: list of (time, faces_list)
        # Each face: (cx, cy, fw, conf)
        frame_data = []

        for i in range(sample_count):
            t = i * duration / sample_count
            cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
            ret, frame = cap.read()
            if not ret:
                continue

            faces_raw = detect_faces(detector, frame, width, height)

            faces = []
            for f in faces_raw:
                faces.append((f["cx"], f["cy"], f["fw"], f["confidence"]))

            frame_data.append((t, faces))

        cap.release()

        # Count total detections
        total_faces = sum(len(faces) for _, faces in frame_data)
        if total_faces < 5:
            return None

        # ── Phase 2: Establish speaker ↔ side mapping from split frames ─
        # Only use frames with 2+ faces (split-screen) to determine which
        # speaker is on which side. Single-face frames can't tell us this.

        split_frames = [(t, faces) for t, faces in frame_data if len(faces) >= 2]
        single_frames = [(t, faces) for t, faces in frame_data if len(faces) == 1]
        has_split = len(split_frames) >= 3
        has_single = len(single_frames) >= 3
        is_mixed_layout = has_split and has_single

        # Get unique speakers and build speaker segments
        speakers = sorted(set(w.get("speaker") for w in transcript_words if w.get("speaker")))

        # Build speaker segments from transcript words
        segments = []
        current_speaker = None
        seg_start = 0

        for w in sorted(transcript_words, key=lambda x: x["start"]):
            sp = w.get("speaker")
            t = max(0, w["start"] - clip_start)
            if sp != current_speaker and sp is not None:
                if current_speaker is not None:
                    segments.append((seg_start, t, current_speaker))
                current_speaker = sp
                seg_start = t

        if current_speaker is not None:
            segments.append((seg_start, duration, current_speaker))

        if not segments:
            # No speaker data — use median of all faces
            all_cx = [cx for _, faces in frame_data for cx, cy, fw, conf in faces]
            all_cy = [cy for _, faces in frame_data for cx, cy, fw, conf in faces]
            if all_cx:
                mcx = int(np.median(all_cx))
                mcy = int(np.median(all_cy))
                crop_x = max(0, min(mcx - adj_w // 2, width - adj_w))
                crop_y = max(0, min(mcy - int(adjusted_h * 0.33), height - adjusted_h))
                return str(crop_x), str(crop_y), adjusted_h
            return None

        # Merge very short segments (<1.0s) to avoid jitter
        merged = []
        for seg in segments:
            if merged and seg[2] == merged[-1][2]:
                merged[-1] = (merged[-1][0], seg[1], seg[2])
            elif merged and (seg[1] - seg[0]) < 1.0:
                merged[-1] = (merged[-1][0], seg[1], merged[-1][2])
            else:
                merged.append(list(seg))
        segments = merged

        # Determine speaker-to-side mapping from split-screen frames
        # In split-screen: left face belongs to one speaker, right to another
        speaker_side = {}  # speaker → "left" or "right"

        if has_split and len(speakers) >= 2:
            # Use first-word heuristic: first speaker to talk → left face
            speaker_talk_time = {}
            speaker_first_word = {}
            for w in transcript_words:
                sp = w.get("speaker")
                if sp:
                    speaker_talk_time[sp] = speaker_talk_time.get(sp, 0) + (w["end"] - w["start"])
                    if sp not in speaker_first_word:
                        speaker_first_word[sp] = w["start"]

            speakers_by_talk = sorted(speakers, key=lambda s: speaker_talk_time.get(s, 0), reverse=True)
            top_2 = speakers_by_talk[:2]
            top_2_by_first_word = sorted(top_2, key=lambda s: speaker_first_word.get(s, float("inf")))
            speaker_side[top_2_by_first_word[0]] = "left"
            speaker_side[top_2_by_first_word[1]] = "right"
            # Extra speakers inherit dominant speaker's side
            for sp in speakers_by_talk[2:]:
                speaker_side[sp] = speaker_side[speakers_by_talk[0]]

        # ── Phase 3: Per-frame face selection for active speaker ───────
        # For each sampled frame, pick the face belonging to the active
        # speaker, adapting to whether the frame is split or single-person.

        def _active_speaker_at(t):
            """Which speaker is active at time t in the clip."""
            for start_t, end_t, sp in segments:
                if start_t <= t <= end_t:
                    return sp
            return segments[0][2] if segments else None

        def _pick_face_for_speaker(faces, speaker, n_faces):
            """
            Select the correct face for a speaker from detected faces.

            Split-screen (2+ faces): pick the face on the speaker's assigned side.
            Single-person (1 face): always use that face (it's whoever is on-screen).
            """
            if not faces:
                return None

            if n_faces >= 2 and speaker in speaker_side:
                # Split-screen: pick face on the speaker's side
                side = speaker_side[speaker]
                if side == "left":
                    # Pick the leftmost face
                    return min(faces, key=lambda f: f[0])
                else:
                    # Pick the rightmost face
                    return max(faces, key=lambda f: f[0])

            # Single face or no side mapping: use the best (most confident) face
            return max(faces, key=lambda f: f[3])

        def _crop_xy_for_face(cx, cy):
            """Compute crop_x and crop_y for a face at (cx, cy)."""
            crop_x = cx - adj_w // 2
            crop_x = max(0, min(crop_x, width - adj_w))
            crop_y = cy - int(adjusted_h * 0.33)
            crop_y = max(0, min(crop_y, height - adjusted_h))
            return crop_x, crop_y

        # Build per-timestamp crop positions
        timed_crops = []  # (time, crop_x, crop_y, speaker)
        for t, faces in frame_data:
            if not faces:
                continue
            speaker = _active_speaker_at(t)
            face = _pick_face_for_speaker(faces, speaker, len(faces))
            if face is None:
                continue
            cx, cy, fw, conf = face
            crop_x, crop_y = _crop_xy_for_face(cx, cy)
            timed_crops.append((t, crop_x, crop_y, speaker))

        if not timed_crops:
            return None

        # ── Phase 4: Build keyframes per speaker segment ───────────────
        # Default position: dominant speaker's median
        speaker_durations = {}
        for start_t, end_t, sp in segments:
            speaker_durations[sp] = speaker_durations.get(sp, 0) + (end_t - start_t)
        dominant_speaker = max(speaker_durations, key=speaker_durations.get)

        dom_crops = [(cx, cy) for _, cx, cy, sp in timed_crops if sp == dominant_speaker]
        default_x = int(np.median([cx for cx, cy in dom_crops])) if dom_crops else width // 2 - adj_w // 2
        default_y = int(np.median([cy for cx, cy in dom_crops])) if dom_crops else 0

        x_keyframes = []
        y_keyframes = []

        for start_t, end_t, speaker in segments:
            # Get crop positions for frames within this segment
            seg_crops = [(t, cx, cy) for t, cx, cy, sp in timed_crops if start_t <= t <= end_t]

            if seg_crops:
                for t, cx, cy in seg_crops:
                    x_keyframes.append((t, cx))
                    y_keyframes.append((t, cy))
            else:
                # No frames in segment — use speaker's median from all frames
                sp_crops = [(cx, cy) for _, cx, cy, sp in timed_crops if sp == speaker]
                if sp_crops:
                    med_x = int(np.median([cx for cx, cy in sp_crops]))
                    med_y = int(np.median([cy for cx, cy in sp_crops]))
                    x_keyframes.append((start_t, med_x))
                    y_keyframes.append((start_t, med_y))
                else:
                    x_keyframes.append((start_t, default_x))
                    y_keyframes.append((start_t, default_y))

        # Deduplicate and sort both keyframe lists
        def _smooth_keyframes(kf, min_interval=0.5):
            kf.sort(key=lambda k: k[0])
            smoothed = []
            for t, v in kf:
                if smoothed and abs(t - smoothed[-1][0]) < min_interval:
                    continue
                smoothed.append((t, v))
            return smoothed

        x_keyframes = _smooth_keyframes(x_keyframes)
        y_keyframes = _smooth_keyframes(y_keyframes)

        if not x_keyframes and not y_keyframes:
            return str(default_x), str(default_y), adjusted_h

        # Build FFmpeg expressions for X and Y independently
        def _build_expr_1d(kf_list, default_val, max_parts=30, jump_threshold=150):
            """Build FFmpeg expression from [(time, value), ...] keyframes.

            Uses smooth lerp between consecutive keyframes, but instant-cuts
            for large jumps (layout changes like split→single in Riverside).

            jump_threshold: if value changes by more than this between adjacent
            keyframes, use instant cut instead of smooth interpolation to avoid
            sweeping across the split-screen seam.
            """
            if not kf_list:
                return str(default_val)

            # Ensure first keyframe is at t=0 so the start of the clip is covered
            if kf_list[0][0] > 0.1:
                kf_list = [(0, kf_list[0][1])] + kf_list

            if len(kf_list) >= 2:
                parts = []
                for i in range(len(kf_list) - 1):
                    t0, v0 = kf_list[i]
                    t1, v1 = kf_list[i + 1]
                    dt = max(0.01, t1 - t0)
                    value_jump = abs(v1 - v0)

                    if dt > 1.5 or value_jump > jump_threshold:
                        # Large time gap OR large value jump (layout change):
                        # hold previous value, then instant-cut to next.
                        # Avoids interpolating through the split-screen seam.
                        jump_t = t1 - 0.01
                        parts.append(
                            f"if(between(t\\,{t0:.2f}\\,{jump_t:.2f})\\,{v0}\\,"
                        )
                        parts.append(
                            f"if(between(t\\,{jump_t:.2f}\\,{t1:.2f})\\,{v1}\\,"
                        )
                    elif value_jump > jump_threshold * 0.6:
                        # Medium jump (significant lean/shift): fast reframe (~0.15s)
                        pan_t = min(0.15, dt)
                        mid_t = t0 + pan_t
                        parts.append(
                            f"if(between(t\\,{t0:.2f}\\,{mid_t:.2f})\\,"
                            f"{v0}+(({v1}-{v0})*(t-{t0:.2f})/{pan_t:.2f})\\,"
                        )
                        if mid_t < t1:
                            parts.append(
                                f"if(between(t\\,{mid_t:.2f}\\,{t1:.2f})\\,{v1}\\,"
                            )
                    else:
                        # Close keyframes with small movement: smooth interpolation
                        parts.append(
                            f"if(between(t\\,{t0:.2f}\\,{t1:.2f})\\,"
                            f"{v0}+(({v1}-{v0})*(t-{t0:.2f})/{dt:.2f})\\,"
                        )
                if len(parts) > max_parts:
                    return str(default_val)
                return "".join(parts) + str(kf_list[-1][1]) + ")" * len(parts)

            return str(kf_list[0][1])

        x_expr = _build_expr_1d(x_keyframes, default_x, max_parts=120)
        y_expr = _build_expr_1d(y_keyframes, default_y, max_parts=120)

        return x_expr, y_expr, adjusted_h

    except ImportError:
        return None
    except Exception as e:
        import traceback
        print(f"Warning: speaker-aware crop failed: {e}\n{traceback.format_exc()}", file=sys.stderr)
        return None


def _create_gradient_png(output_path: str, width: int = 1080, height: int = 1920, opacity: float = 0.7) -> str:
    """
    Create a transparent-to-black gradient PNG for the bottom 50% of the frame.
    Uses Python to generate the image — no external deps needed (uses raw PPM → FFmpeg).
    """
    # Generate a 1-pixel wide gradient strip, then FFmpeg scales it
    # Build gradient with FFmpeg: black fading from 0% to opacity% over bottom half
    # We create a gradient using lavfi color + geq
    max_alpha = int(opacity * 255)

    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi",
        "-i", f"color=black@0.0:size={width}x{height}:duration=1,format=rgba,"
              f"geq="
              f"r=0:"
              f"g=0:"
              f"b=0:"
              f"a='if(lt(Y,H/2),0,min({max_alpha},{max_alpha}*(Y-H/2)/(H/2)))'",
        "-frames:v", "1",
        output_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=_FFMPEG_TIMEOUT)
    if result.returncode != 0:
        raise RuntimeError(f"Gradient creation failed: {result.stderr[-300:]}")
    return output_path


def burn_captions(
    input_path: str,
    ass_path: str,
    output_path: str,
    gradient_overlay: bool = False,
    gradient_opacity: float = 0.7,
    logo_path: Optional[str] = None,
    logo_height: int = 80,
    logo_margin_x: int = 30,
    logo_margin_y: int = 40,
) -> str:
    """
    Burn ASS subtitles into the video.

    Optionally adds:
    - Bottom 50% smooth gradient overlay (transparent → black)
    - Logo image in top-left corner
    """
    safe_ass = ass_path.replace("\\", "/").replace(":", "\\:")

    # Get video dimensions for gradient
    if gradient_overlay:
        width, height = get_dimensions(input_path)
        gradient_path = output_path + ".gradient.png"
        _create_gradient_png(gradient_path, width, height, gradient_opacity)

    # Build filter_complex for all overlay inputs
    inputs = ["-i", input_path]
    input_idx = 1  # next input index

    filter_parts = []

    if gradient_overlay:
        inputs.extend(["-i", gradient_path])
        grad_idx = input_idx
        input_idx += 1
        # Overlay gradient on video
        filter_parts.append(
            f"[0:v][{grad_idx}:v]overlay=0:0:format=auto[grad]"
        )
        current_label = "grad"
    else:
        current_label = "0:v"

    if logo_path and os.path.exists(logo_path):
        inputs.extend(["-i", logo_path])
        logo_idx = input_idx
        input_idx += 1
        # Scale logo + overlay
        filter_parts.append(
            f"[{logo_idx}:v]scale=-1:{logo_height}[logo]"
        )
        filter_parts.append(
            f"[{current_label}][logo]overlay={logo_margin_x}:{logo_margin_y}[withlogo]"
        )
        current_label = "withlogo"

    # Burn ASS subtitles
    filter_parts.append(
        f"[{current_label}]ass='{safe_ass}'[out]"
    )

    filter_complex = ";".join(filter_parts)

    # Check if input has audio (some split-screen concat outputs may not)
    has_audio = _has_audio_stream(input_path)
    audio_map = ["-map", "0:a?", "-c:a", "copy"] if has_audio else []

    # Run with HW encoder, fallback to CPU if it fails
    enc_flags = get_video_encode_flags()
    cmd = [
        "ffmpeg", "-y",
        *inputs,
        "-filter_complex", filter_complex,
        "-map", "[out]",
        *audio_map,
        *enc_flags,
        "-movflags", "+faststart",
        output_path,
    ]

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=_FFMPEG_TIMEOUT)

    # Fallback to CPU if HW encoder failed
    if result.returncode != 0 and enc_flags != CPU_FLAGS:
        print(f"Warning: HW encoder failed for caption burn, falling back to libx264", file=sys.stderr)
        cmd_fallback = [
            "ffmpeg", "-y",
            *inputs,
            "-filter_complex", filter_complex,
            "-map", "[out]",
            *audio_map,
            *CPU_FLAGS,
            "-movflags", "+faststart",
            output_path,
        ]
        result = subprocess.run(cmd_fallback, capture_output=True, text=True, timeout=_FFMPEG_TIMEOUT)

    # Clean up gradient file
    if gradient_overlay and os.path.exists(gradient_path):
        os.remove(gradient_path)

    if result.returncode != 0:
        raise RuntimeError(f"FFmpeg caption burn failed: {result.stderr[-500:]}")
    return output_path


def concat_outro(
    input_path: str,
    outro_path: str,
    output_path: str,
    crossfade_duration: float = 0.5,
) -> str:
    """
    Append an outro video to the end of the main clip with a crossfade transition.

    Uses FFmpeg xfade filter for a smooth video crossfade and acrossfade for audio.
    Falls back to hard cut if xfade fails (older FFmpeg).
    """
    width, height = get_dimensions(input_path)

    # Get main clip duration for crossfade offset
    probe_cmd = [
        "ffprobe", "-v", "quiet", "-print_format", "json",
        "-show_format", input_path,
    ]
    probe = subprocess.run(probe_cmd, capture_output=True, text=True, timeout=_FFMPEG_TIMEOUT)
    try:
        main_duration = float(json.loads(probe.stdout)["format"]["duration"])
    except Exception:
        main_duration = 30.0

    fade_offset = max(0, main_duration - crossfade_duration)

    # Re-encode outro to match dimensions
    outro_scaled = output_path + ".outro_scaled.mp4"
    _run_ffmpeg_with_fallback(
        cmd_parts_before_enc=[
            "ffmpeg", "-y",
            "-i", outro_path,
            "-vf", f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
                   f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black",
        ],
        cmd_parts_after_enc=[
            "-c:a", "aac", "-b:a", "192k", "-ar", "44100",
            "-movflags", "+faststart",
        ],
        output_path=outro_scaled,
        label="outro_scale",
    )

    # Try crossfade first (requires FFmpeg 4.3+)
    try:
        xfade_cmd = [
            "ffmpeg", "-y",
            "-i", input_path,
            "-i", outro_scaled,
            "-filter_complex",
            f"[0:v][1:v]xfade=transition=fade:duration={crossfade_duration}:offset={fade_offset}[v];"
            f"[0:a][1:a]acrossfade=d={crossfade_duration}[a]",
            "-map", "[v]", "-map", "[a]",
        ]
        xfade_cmd += get_video_encode_flags()
        xfade_cmd += [
            "-c:a", "aac", "-b:a", "192k",
            "-movflags", "+faststart",
            output_path,
        ]
        result = subprocess.run(xfade_cmd, capture_output=True, text=True, timeout=_FFMPEG_TIMEOUT)
        if result.returncode == 0:
            # Clean up
            if os.path.exists(outro_scaled):
                os.remove(outro_scaled)
            return output_path
    except Exception:
        pass

    # Fallback: hard cut concat
    main_reenc = output_path + ".main_reenc.mp4"
    concat_list = os.path.join(os.path.dirname(output_path), "concat_list.txt")

    _run_ffmpeg_with_fallback(
        cmd_parts_before_enc=[
            "ffmpeg", "-y",
            "-i", input_path,
            "-vf", f"scale={width}:{height}",
        ],
        cmd_parts_after_enc=[
            "-c:a", "aac", "-b:a", "192k", "-ar", "44100",
            "-movflags", "+faststart",
        ],
        output_path=main_reenc,
        label="main_reenc",
    )

    try:
        with open(concat_list, "w") as f:
            f.write(f"file '{main_reenc}'\n")
            f.write(f"file '{outro_scaled}'\n")

        cmd = [
            "ffmpeg", "-y",
            "-f", "concat", "-safe", "0",
            "-i", concat_list,
            "-c", "copy",
            "-movflags", "+faststart",
            output_path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=_FFMPEG_TIMEOUT)
        if result.returncode != 0:
            raise RuntimeError(f"FFmpeg concat failed: {result.stderr[-500:]}")
        return output_path
    finally:
        for tmp in [concat_list, outro_scaled, main_reenc]:
            if os.path.exists(tmp):
                os.remove(tmp)


def normalize_audio(
    input_path: str,
    output_path: str,
    target_lufs: float = -14.0,
) -> str:
    """
    Normalize audio to target LUFS (loudness units).
    TikTok/YouTube Shorts standard is around -14 LUFS.
    """
    # First pass: measure current loudness
    measure_cmd = [
        "ffmpeg", "-y",
        "-i", input_path,
        "-af", f"loudnorm=I={target_lufs}:TP=-1.5:LRA=11:print_format=json",
        "-f", "null", "-",
    ]
    result = subprocess.run(measure_cmd, capture_output=True, text=True, timeout=_FFMPEG_TIMEOUT)

    # Try to parse loudnorm output from stderr
    stderr = result.stderr
    try:
        # Find the JSON block in stderr
        json_start = stderr.rfind("{")
        json_end = stderr.rfind("}") + 1
        if json_start >= 0 and json_end > json_start:
            loudnorm_data = json.loads(stderr[json_start:json_end])
        else:
            # Fallback: simple normalization without two-pass
            loudnorm_data = None
    except (json.JSONDecodeError, ValueError):
        loudnorm_data = None

    # Check for invalid measurements (e.g., -inf from silence/very short clips)
    if loudnorm_data:
        measured_i = str(loudnorm_data.get("input_i", ""))
        if "inf" in measured_i.lower() or measured_i == "":
            loudnorm_data = None

    if loudnorm_data:
        # Second pass: apply measured correction
        af_filter = (
            f"loudnorm=I={target_lufs}:TP=-1.5:LRA=11:"
            f"measured_I={loudnorm_data.get('input_i', '-24')}:"
            f"measured_TP={loudnorm_data.get('input_tp', '-1')}:"
            f"measured_LRA={loudnorm_data.get('input_lra', '7')}:"
            f"measured_thresh={loudnorm_data.get('input_thresh', '-34')}:"
            f"offset={loudnorm_data.get('target_offset', '0')}:"
            f"linear=true"
        )
    else:
        # Single-pass fallback
        af_filter = f"loudnorm=I={target_lufs}:TP=-1.5:LRA=11"

    cmd = [
        "ffmpeg", "-y",
        "-i", input_path,
        "-af", af_filter,
        "-c:v", "copy",
        "-c:a", "aac",
        "-b:a", "192k",
        "-ar", "44100",
        "-movflags", "+faststart",
        output_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=_FFMPEG_TIMEOUT)
    if result.returncode != 0:
        raise RuntimeError(f"FFmpeg normalize failed: {result.stderr[-500:]}")
    return output_path


def _detect_face_y(
    video_path: str,
    width: int,
    height: int,
    crop_w: int,
    crop_x_expr: str,
) -> Optional[int]:
    """
    Detect the median face Y position in the video.
    Used to vertically center the crop on the face when the person
    is sitting high or low in their webcam feed.

    Returns the median face center Y coordinate, or None.
    """
    try:
        import cv2
        import numpy as np
        from services.face_detector import create_detector, detect_faces

        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            return None

        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        fps = cap.get(cv2.CAP_PROP_FPS) or 30
        duration = total_frames / fps

        # Try to evaluate crop_x as a static value for ROI
        try:
            crop_x = int(float(crop_x_expr))
        except (ValueError, TypeError):
            crop_x = None

        # Detect on full frame, then filter by crop region
        detector = create_detector(width, height)
        if detector is None:
            cap.release()
            return None

        face_ys = []
        sample_count = min(20, max(5, int(duration)))

        for i in range(sample_count):
            t = (i + 1) * duration / (sample_count + 1)
            cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
            ret, frame = cap.read()
            if not ret:
                continue

            faces = detect_faces(detector, frame, width, height)

            for f in faces:
                # If we know crop_x, only use faces within the crop column
                if crop_x is not None:
                    if f["cx"] < crop_x or f["cx"] > crop_x + crop_w:
                        continue
                if f["fh"] > height * 0.05:
                    face_ys.append(f["cy"])

        cap.release()

        if len(face_ys) < 3:
            return None

        return int(np.median(face_ys))

    except Exception:
        return None


def _detect_face_center(
    video_path: str,
    width: int,
    height: int,
    target_ratio: float,
    crop_h: int = None,
) -> Optional[tuple]:
    """
    Simple static face detection — finds the dominant face and returns a
    STATIC crop position that centers the face horizontally. No tracking,
    no panning. The face stays centered for the entire clip.

    Returns (crop_x_str, median_face_cy) tuple, or None if no face detected.
    crop_h: if set, compute crop width from this height (for vertical zoom).
    """
    try:
        import cv2
        import numpy as np
        from services.face_detector import create_detector, detect_faces

        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            return None

        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        fps = cap.get(cv2.CAP_PROP_FPS) or 30
        duration = total_frames / fps
        crop_w = int((crop_h or height) * target_ratio)

        detector = create_detector(width, height)
        if detector is None:
            cap.release()
            return None

        # Sample frames across the clip
        sample_count = min(40, max(10, int(duration * 2)))
        face_positions = []
        face_cy_values = []

        for i in range(sample_count):
            t = i * duration / sample_count
            cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
            ret, frame = cap.read()
            if not ret:
                continue

            faces = detect_faces(detector, frame, width, height)

            # Pick the best face (largest × most confident)
            best_score = 0
            best_cx = None
            best_cy = None
            for f in faces:
                score = f["confidence"] * (f["fw"] ** 2)
                if score > best_score:
                    best_score = score
                    best_cx = f["cx"]
                    best_cy = f["cy"]

            if best_cx is not None:
                face_positions.append(best_cx)
                face_cy_values.append(best_cy)

        cap.release()

        if not face_positions:
            return None

        median_cy = int(np.median(face_cy_values)) if face_cy_values else None

        # Detect split-screen: check if faces appear in both halves
        positions = np.array(face_positions)
        mid_x = width // 2

        left_count = np.sum(positions < mid_x)
        right_count = np.sum(positions >= mid_x)

        if left_count >= 3 and right_count >= 3:
            # Split-screen — pick the side with the most detections,
            # clamp crop to that half with margin away from seam.
            seam_margin = 20
            if left_count >= right_count:
                side_pos = positions[positions < mid_x]
                face_x = int(np.median(side_pos))
                crop_x = max(0, min(face_x - crop_w // 2, mid_x - crop_w - seam_margin))
            else:
                side_pos = positions[positions >= mid_x]
                face_x = int(np.median(side_pos))
                crop_x = max(mid_x + seam_margin, min(face_x - crop_w // 2, width - crop_w))
        else:
            face_x = int(np.median(face_positions))
            crop_x = face_x - crop_w // 2
            crop_x = max(0, min(crop_x, width - crop_w))

        return (str(crop_x), median_cy)

    except ImportError:
        return None
    except Exception as e:
        print(f"Warning: face center detection failed: {e}", file=sys.stderr)
        return None


def _detect_face_offset(
    video_path: str,
    width: int,
    height: int,
    target_ratio: float,
    crop_h: int = None,
) -> Optional[tuple]:
    """
    Continuous face tracking — detects face position over time and returns
    an FFmpeg expression that smoothly follows the dominant face.

    Samples frames throughout the clip, picks the dominant face cluster,
    then builds a smoothed panning timeline so the crop follows head movement.

    Returns (x_expr, median_face_cy) tuple, or None if no face detected.
    crop_h: if set, compute crop width from this height (for vertical zoom).
    """
    try:
        import cv2
        import numpy as np
        from services.face_detector import create_detector, detect_faces

        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            return None

        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        fps = cap.get(cv2.CAP_PROP_FPS) or 30
        duration = total_frames / fps
        crop_w = int((crop_h or height) * target_ratio)

        # Sample ~4 frames/sec for smooth tracking (more than before)
        sample_count = min(240, max(30, int(duration * 4)))
        sample_times = [i * duration / sample_count for i in range(sample_count)]

        # (time, face_center_x) for each detection
        timed_positions = []
        face_cy_values = []

        detector = create_detector(width, height)
        if detector is None:
            cap.release()
            return None

        for t in sample_times:
            cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
            ret, frame = cap.read()
            if not ret:
                continue

            faces = detect_faces(detector, frame, width, height)

            # Pick the best face (largest × most confident)
            best_score = 0
            best_cx = None
            best_cy = None
            for f in faces:
                if f["fh"] < height * 0.05:
                    continue
                score = f["confidence"] * (f["fw"] ** 2)
                if score > best_score:
                    best_score = score
                    best_cx = f["cx"]
                    best_cy = f["cy"]

            if best_cx is not None:
                timed_positions.append((t, best_cx))
                face_cy_values.append(best_cy)

        cap.release()

        if len(timed_positions) < 3:
            return None

        # Median face Y for vertical positioning (computed once, used in all returns)
        median_cy = int(np.median(face_cy_values)) if face_cy_values else None

        # --- Cluster to find dominant face ---
        all_cx = np.array([p[1] for p in timed_positions])
        cluster_radius = crop_w * 0.35

        clusters = []
        used = np.zeros(len(all_cx), dtype=bool)
        for idx in np.argsort(all_cx):
            if used[idx]:
                continue
            mask = (np.abs(all_cx - all_cx[idx]) < cluster_radius) & ~used
            if np.any(mask):
                clusters.append(np.where(mask)[0])
                used |= mask

        if not clusters:
            return None

        # Pick largest cluster (dominant face)
        clusters.sort(key=lambda c: len(c), reverse=True)
        dominant_indices = set(clusters[0])

        # Filter to only dominant face detections
        tracked = [(t, cx) for i, (t, cx) in enumerate(timed_positions) if i in dominant_indices]
        if len(tracked) < 3:
            # Too few points — use static median
            median_x = int(np.median([cx for _, cx in tracked]))
            crop_x = max(0, min(median_x - crop_w // 2, width - crop_w))
            return (str(crop_x), median_cy)

        # --- Smooth the positions ---
        # 1) Convert face_center_x to crop_x (clamped with margin).
        #    Face should be in the center 70% of crop window, never at edge.
        margin = int(crop_w * 0.15)  # 15% margin on each side
        tracked_times = np.array([t for t, _ in tracked])
        tracked_crop_x = np.array([
            max(0, min(cx - crop_w // 2, width - crop_w))
            for _, cx in tracked
        ], dtype=float)

        # 2) Fill gaps: if face wasn't detected for some frames, the tracked
        #    array has gaps. That's fine — we interpolate between known points.

        # 3) Heavy smoothing to prevent jitter. Use a 2.5-second rolling average
        #    so only sustained movement (leaning, shifting) affects the crop.
        #    Small head bobs and natural sway are averaged out.
        if len(tracked_crop_x) > 5:
            sample_interval = tracked_times[-1] / len(tracked_times) if len(tracked_times) > 1 else 0.25
            window = max(5, int(2.5 / max(0.05, sample_interval)))
            # Pad edges so smoothing doesn't pull toward zero
            padded = np.pad(tracked_crop_x, (window // 2, window // 2), mode="edge")
            kernel = np.ones(window) / window
            smoothed = np.convolve(padded, kernel, mode="valid")[:len(tracked_crop_x)]
        else:
            smoothed = tracked_crop_x

        # 4) Round to ints and clamp
        smoothed = np.clip(np.round(smoothed), 0, width - crop_w).astype(int)

        # --- Check if tracking is even needed ---
        # If the face barely moves (range < 20% of crop width), just use static.
        # Most podcast clips have speakers sitting still — tracking should be rare.
        movement_range = int(smoothed.max() - smoothed.min())
        if movement_range < crop_w * 0.20:
            return (str(int(np.median(smoothed))), median_cy)

        # --- Build keyframes: only emit on MAJOR position changes ---
        # Very conservative: only pan when speaker physically moves to a new
        # position (leans far, switches seats). Normal head movement = static.
        keyframes = [(tracked_times[0], int(smoothed[0]))]
        min_time_gap = 3.0   # Don't place keyframes closer than 3s
        min_px_change = max(40, int(crop_w * 0.13))  # Proportional safe zone (~80px at 1080p)

        for i in range(1, len(smoothed)):
            t = tracked_times[i]
            x = int(smoothed[i])
            prev_t, prev_x = keyframes[-1]
            dt = t - prev_t
            dx = abs(x - prev_x)

            if dx >= min_px_change and dt >= min_time_gap:
                keyframes.append((t, x))
            elif i == len(smoothed) - 1:
                # Always include last point
                keyframes.append((t, x))

        if len(keyframes) < 2:
            return (str(keyframes[0][1]), median_cy)

        # --- Build FFmpeg expression ---
        # Piecewise linear interpolation between keyframes.
        # if(lt(t, t1), lerp(t0→t1), if(lt(t, t2), lerp(t1→t2), ...))
        # This is simpler than between() chains and nests one level per segment.
        expr_parts = []
        for i in range(len(keyframes) - 1):
            t0, x0 = keyframes[i]
            t1, x1 = keyframes[i + 1]
            dt = max(0.01, t1 - t0)
            if x0 == x1:
                # No movement in this segment — constant
                expr_parts.append(f"if(lt(t\\,{t1:.2f})\\,{x0}\\,")
            else:
                # Linear interpolation
                expr_parts.append(
                    f"if(lt(t\\,{t1:.2f})\\,"
                    f"{x0}+(({x1}-{x0})*(t-{t0:.2f})/{dt:.2f})\\,"
                )

        # Cap nesting depth
        if len(expr_parts) > 40:
            # Too many keyframes — subsample
            step = len(keyframes) // 20
            keyframes = keyframes[::step] + [keyframes[-1]]
            # Rebuild (recursive call would be cleaner but let's just return static)
            print(f"Warning: face tracking produced {len(expr_parts)} keyframes, using static", file=sys.stderr)
            return (str(int(np.median(smoothed))), median_cy)

        # Final value: last keyframe position
        last_x = keyframes[-1][1]
        expr = "".join(expr_parts) + str(last_x) + ")" * len(expr_parts)

        return (expr, median_cy)

    except ImportError:
        return None
    except Exception as e:
        print(f"Warning: face tracking failed: {e}", file=sys.stderr)
        return None
