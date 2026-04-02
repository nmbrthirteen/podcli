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
        speakers_in_clip = {
            w.get("speaker") for w in (transcript_words or []) if w.get("speaker")
        }
        # Any clip with speaker labels should prefer clip-local tracking over the
        # episode-wide face_map. Global speaker→side mappings are too coarse for
        # monologues and mixed-layout edits; they can pin a single-speaker clip
        # to the wrong person for the whole render.
        if speakers_in_clip:
            result = _track_and_crop(
                input_path, output_path,
                width, height, target_w, target_h,
                transcript_words, clip_start,
                face_map=face_map,
            )
            if result:
                return result

        if face_map:
            crop_h = height
            crop_y = max(0, (height - crop_h) // 2)
            x_expr = _use_face_map(
                face_map=face_map,
                transcript_words=transcript_words,
                clip_start=clip_start,
                width=width,
                height=height,
                target_ratio=target_ratio,
                crop_h=crop_h,
            )
            if x_expr:
                crop_w = int(crop_h * target_ratio)
                crop_w = min(crop_w, width)
                vf = f"crop={crop_w}:{crop_h}:{x_expr}:{crop_y},scale={target_w}:{target_h}"
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
                    label="crop_face_map",
                )

        result = _track_and_crop(
            input_path, output_path,
            width, height, target_w, target_h,
            transcript_words, clip_start,
            face_map=face_map,
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

    def _eased_move_expr(start_t: float, move_t: float, start_x: int, end_x: int) -> str:
        # Smootherstep gives a softer start/landing than smoothstep while still
        # finishing inside the bounded reframe window.
        progress = f"((t-{start_t:.3f})/{move_t:.3f})"
        eased = (
            f"((6*({progress})*({progress})*({progress})*({progress})*({progress}))"
            f"-(15*({progress})*({progress})*({progress})*({progress}))"
            f"+(10*({progress})*({progress})*({progress})))"
        )
        return f"{start_x}+(({end_x}-{start_x})*{eased})"

    parts = []
    for i in range(len(keyframes) - 1):
        t0, x0 = keyframes[i]
        t1, x1 = keyframes[i + 1]
        dt = max(0.01, t1 - t0)
        jump = abs(x1 - x0)
        blurred_cut_jump = 90 if is_split else 180
        quick_reframe_jump = 40 if is_split else 90

        if jump < 2:
            # Negligible movement — hold
            parts.append(f"if(between(t\\,{t0:.3f}\\,{t1:.3f})\\,{x0}\\,")
        elif jump >= blurred_cut_jump:
            # Very large jumps look worse as animated crop slides than as a
            # short blurred cut. The blur/zoom filters carry the transition.
            parts.append(f"if(between(t\\,{t0:.3f}\\,{t1:.3f})\\,{x1}\\,")
        elif jump >= quick_reframe_jump:
            # Moderate jump: use a short bounded reframe, but keep it much
            # tighter than a literal camera pan.
            pan_t = min(0.14 if is_split else 0.18, dt)
            pan_end_t = round(t0 + pan_t, 3)
            parts.append(
                f"if(between(t\\,{t0:.3f}\\,{pan_end_t:.3f})\\,"
                f"{_eased_move_expr(t0, pan_t, x0, x1)}\\,"
            )
            if pan_end_t < t1:
                parts.append(f"if(between(t\\,{pan_end_t:.3f}\\,{t1:.3f})\\,{x1}\\,")
        else:
            # Small pans still benefit from eased starts/stops so they read like
            # a camera operator, not a value sliding on rails.
            parts.append(
                f"if(between(t\\,{t0:.3f}\\,{t1:.3f})\\,"
                f"{_eased_move_expr(t0, dt, x0, x1)}\\,"
            )

    if len(parts) > max_parts:
        return None

    return "".join(parts) + str(keyframes[-1][1]) + ")" * len(parts)


def _motion_windows_from_keyframes(
    keyframes: list,
    min_jump: int = 60,
    max_window_duration: float = 0.5,
    max_windows: int = 16,
) -> list[tuple[float, float]]:
    """Find short reframe windows worth accenting."""
    windows = []
    for i in range(len(keyframes) - 1):
        t0, x0 = keyframes[i]
        t1, x1 = keyframes[i + 1]
        if abs(x1 - x0) < min_jump:
            continue
        dt = t1 - t0
        if dt <= 0.01 or dt > max_window_duration:
            continue
        windows.append((t0, t1))

    if not windows or len(windows) > max_windows:
        return []
    return windows


def _expand_motion_windows(
    windows: list[tuple[float, float]],
    pad_before: float = 0.03,
    pad_after: float = 0.05,
) -> list[tuple[float, float]]:
    """Pad and merge motion windows so the blur eases in and out."""
    if not windows:
        return []

    expanded: list[tuple[float, float]] = []
    for t0, t1 in windows:
        start = max(0.0, t0 - pad_before)
        end = t1 + pad_after
        if expanded and start <= expanded[-1][1] + 0.01:
            expanded[-1] = (expanded[-1][0], max(expanded[-1][1], end))
        else:
            expanded.append((start, end))
    return expanded


def _build_motion_blur_filter(
    keyframes: list,
    min_jump: int = 60,
    max_window_duration: float = 0.5,
    max_windows: int = 16,
    sigma: float = 5.4,
    steps: int = 2,
    pad_before: float = 0.03,
    pad_after: float = 0.05,
) -> str:
    """
    Add a stronger full-frame blur only while the camera is actively reframing.

    The goal is to hide the crop move, not just soften it slightly.
    """
    windows = _expand_motion_windows(_motion_windows_from_keyframes(
        keyframes=keyframes,
        min_jump=min_jump,
        max_window_duration=max_window_duration,
        max_windows=max_windows,
    ), pad_before=pad_before, pad_after=pad_after)
    if not windows:
        return ""

    enable_expr = "+".join(
        f"between(t\\,{t0:.3f}\\,{t1:.3f})"
        for t0, t1 in windows
    )
    return f",gblur=sigma={sigma:.1f}:steps={steps}:enable='{enable_expr}'"


def _build_motion_zoom_filter(
    keyframes: list,
    target_w: int,
    target_h: int,
    min_jump: int = 60,
    max_window_duration: float = 0.5,
    max_windows: int = 16,
    max_zoom: float = 0.018,
) -> str:
    """
    Add a tiny center zoom bump during short reframes.

    The goal is not visible punch-in. It just helps the move read as an
    intentional editorial transition instead of a hard crop shove.
    """
    windows = _motion_windows_from_keyframes(
        keyframes=keyframes,
        min_jump=min_jump,
        max_window_duration=max_window_duration,
        max_windows=max_windows,
    )
    if not windows:
        return ""

    bumps = []
    for t0, t1 in windows:
        dt = max(0.01, t1 - t0)
        progress = f"((t-{t0:.3f})/{dt:.3f})"
        bumps.append(
            f"(16*pow({progress}\\,2)*pow((1-{progress})\\,2)*between(t\\,{t0:.3f}\\,{t1:.3f}))"
        )

    zoom_expr = "+".join(bumps)
    return (
        f",scale=w='iw*(1+{max_zoom:.4f}*({zoom_expr}))'"
        f":h='ih*(1+{max_zoom:.4f}*({zoom_expr}))'"
        f":eval=frame,crop={target_w}:{target_h}:(iw-{target_w})/2:(ih-{target_h})/2"
    )


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


def _resolve_speaker_sides(
    segments: list,
    detections: list,
    width: int,
    face_map: dict = None,
) -> dict:
    """
    Build left/right speaker hints for mixed split-screen clips.
    Only use precomputed face_map mappings here.

    Guessing speaker sides from transcript order is low-confidence and can
    easily reverse the camera on host/guest clips. Clip-local track evidence
    should make that decision instead.
    """
    speaker_side = {}
    mid_x = width // 2

    if face_map:
        clusters = face_map.get("clusters", [])
        mappings = face_map.get("speaker_mappings", {})
        for speaker, cluster_index in mappings.items():
            if cluster_index is None or cluster_index >= len(clusters):
                continue
            speaker_side[speaker] = "left" if clusters[cluster_index]["center_x"] < mid_x else "right"

    return speaker_side


def _assign_face_tracks(
    detections: list,
    width: int,
    max_gap: float = 1.2,
) -> list:
    """
    Give detected faces stable local track ids.

    This mirrors the useful part of OpenShorts: a lightweight identity pass
    based on horizontal continuity. We only need clip-local stickiness, not
    perfect biometric identity.
    """
    known_tracks = []
    next_track_id = 0
    tracked_detections = []

    for t, faces in detections:
        known_tracks = [track for track in known_tracks if (t - track["last_t"]) <= max_gap]
        used_track_ids = set()
        tracked_faces = []

        for face in sorted(faces, key=lambda f: f["fw"], reverse=True):
            best_track = None
            best_dist = None
            match_radius = max(width * 0.10, face["fw"] * 1.6)

            for track in known_tracks:
                if track["id"] in used_track_ids:
                    continue
                dx = abs(face["cx"] - track["cx"])
                dy = abs(face.get("cy", 0) - track["cy"])
                size_ratio = abs(face["fw"] - track["fw"]) / max(face["fw"], track["fw"], 1.0)
                allowed_dist = max(match_radius, track["fw"] * 1.6)
                if dx > allowed_dist:
                    continue
                score = dx + dy * 0.35 + size_ratio * width * 0.4
                if best_dist is None or score < best_dist:
                    best_track = track
                    best_dist = score

            if best_track is None:
                track_id = next_track_id
                next_track_id += 1
                known_tracks.append({
                    "id": track_id,
                    "cx": float(face["cx"]),
                    "cy": float(face.get("cy", 0)),
                    "fw": float(face["fw"]),
                    "last_t": t,
                })
            else:
                track_id = best_track["id"]
                best_track["cx"] = float(face["cx"])
                best_track["cy"] = float(face.get("cy", 0))
                best_track["fw"] = float(face["fw"])
                best_track["last_t"] = t

            used_track_ids.add(track_id)
            tracked_face = dict(face)
            tracked_face["track_id"] = track_id
            tracked_faces.append(tracked_face)

        tracked_faces.sort(key=lambda f: f["cx"])
        tracked_detections.append((t, tracked_faces))

    return tracked_detections


def _choose_segment_tracks(
    segments: list,
    tracked_detections: list,
    speaker_side: dict,
    speaker_anchor_x: dict,
    width: int,
) -> tuple[list, dict, dict]:
    """
    Choose one persistent visual track for each merged speaker segment.

    The selected track is later followed for the whole turn. Other visible
    faces in the same turn are ignored, which prevents reaction shots from
    stealing the camera.
    """
    from statistics import median

    segment_tracks = []
    learned_anchor_x = dict(speaker_anchor_x or {})
    learned_side = dict(speaker_side or {})
    last_track_for_speaker = {}

    for start_t, end_t, speaker in segments:
        candidates = {}
        for t, faces in tracked_detections:
            if t < start_t or t > end_t:
                continue
            is_split_frame = len(faces) >= 2
            for face in faces:
                data = candidates.setdefault(face["track_id"], {
                    "frames": 0,
                    "split_frames": 0,
                    "solo_frames": 0,
                    "cxs": [],
                    "fws": [],
                    "first_t": t,
                })
                data["frames"] += 1
                data["split_frames"] += 1 if is_split_frame else 0
                data["solo_frames"] += 0 if is_split_frame else 1
                data["cxs"].append(face["cx"])
                data["fws"].append(face["fw"])
                data["first_t"] = min(data["first_t"], t)

        if not candidates:
            segment_tracks.append((start_t, end_t, speaker, None, None))
            continue

        prev_track_id = last_track_for_speaker.get(speaker)
        hint_anchor = learned_anchor_x.get(speaker)
        hint_side = learned_side.get(speaker)
        side_hint_strength = 1.1
        anchor_hint_penalty = 1.8

        if hint_side is None:
            other_sides = {sp: side for sp, side in learned_side.items() if sp != speaker}
            if len(other_sides) == 1:
                only_side = next(iter(other_sides.values()))
                hint_side = "right" if only_side == "left" else "left"
                side_hint_strength = 0.6
                anchor_hint_penalty = 0.9

        def _local_score(item):
            track_id, data = item
            median_x = float(median(data["cxs"]))
            median_fw = float(median(data["fws"]))
            score = (
                data["frames"] * 1.0
                + data["split_frames"] * 2.4
                + data["solo_frames"] * 0.35
                + median_fw / 140.0
            )

            if prev_track_id == track_id:
                score += 2.5

            score += max(0.0, 1.2 - max(0.0, data["first_t"] - start_t))
            return score

        def _score(item):
            track_id, data = item
            score = _local_score(item)
            median_x = float(median(data["cxs"]))

            if hint_side:
                on_hint_side = median_x < (width / 2) if hint_side == "left" else median_x >= (width / 2)
                score += side_hint_strength if on_hint_side else -side_hint_strength * 0.72

            if hint_anchor is not None:
                score -= min(anchor_hint_penalty, abs(median_x - hint_anchor) / max(width * 0.30, 1))
            return score

        local_best_track_id, local_best_data = max(candidates.items(), key=_local_score)
        chosen_track_id, chosen_data = max(candidates.items(), key=_score)

        local_best_score = _local_score((local_best_track_id, local_best_data))
        chosen_local_score = _local_score((chosen_track_id, chosen_data))
        hinted_score = _score((chosen_track_id, chosen_data))
        local_scored_with_hints = _score((local_best_track_id, local_best_data))
        local_best_x = float(median(local_best_data["cxs"]))
        local_best_opposes_hint = False
        if learned_side.get(speaker):
            expected_left = learned_side[speaker] == "left"
            local_best_left = local_best_x < (width / 2)
            local_best_opposes_hint = expected_left != local_best_left
        local_best_has_decisive_local_evidence = (
            local_best_score >= chosen_local_score + 2.5
            or local_best_data["split_frames"] >= chosen_data["split_frames"] + 2
        )
        hint_is_inferred_or_weak = hint_anchor is None or side_hint_strength < 1.0

        if (
            local_best_track_id != chosen_track_id
            and (
                (
                    local_best_score >= chosen_local_score + 1.2
                    and local_scored_with_hints >= hinted_score - 0.6
                )
                or (
                    local_best_opposes_hint
                    and local_best_has_decisive_local_evidence
                    and hint_is_inferred_or_weak
                )
            )
        ):
            chosen_track_id, chosen_data = local_best_track_id, local_best_data

        chosen_x = float(median(chosen_data["cxs"]))

        segment_tracks.append((start_t, end_t, speaker, chosen_track_id, chosen_x))
        last_track_for_speaker[speaker] = chosen_track_id

        prev_anchor = learned_anchor_x.get(speaker)
        if prev_anchor is None or abs(chosen_x - prev_anchor) > width * 0.18:
            learned_anchor_x[speaker] = chosen_x
        else:
            learned_anchor_x[speaker] = prev_anchor * 0.65 + chosen_x * 0.35
        learned_side[speaker] = "left" if learned_anchor_x[speaker] < (width / 2) else "right"

    return segment_tracks, learned_anchor_x, learned_side


def _update_tripod_camera(
    current_center_x: float,
    target_center_x: float | None,
    crop_w: int,
    video_width: int,
    dt: float,
    force_snap: bool = False,
) -> float:
    """
    Heavy-tripod camera movement inspired by OpenShorts.

    The camera stays still while the subject remains inside a safe zone, then
    moves at a bounded speed instead of constantly chasing every face twitch.
    """
    half_crop = crop_w / 2.0
    min_center = half_crop
    max_center = max(half_crop, video_width - half_crop)

    if target_center_x is None:
        return min(max(current_center_x, min_center), max_center)

    if force_snap:
        current_center_x = target_center_x
    else:
        diff = target_center_x - current_center_x
        # Keep the hold zone fairly tight so the speaker stays composed near
        # center, but still ignore ordinary head movement and detector noise.
        safe_zone_radius = crop_w * 0.14
        if abs(diff) > safe_zone_radius:
            slow_speed = 72.0
            fast_speed = 360.0
            speed = fast_speed if abs(diff) > crop_w * 0.5 else slow_speed
            step = min(abs(diff), speed * max(dt, 0.01))
            current_center_x += step if diff > 0 else -step

    return min(max(current_center_x, min_center), max_center)


def _choose_track_segment_targets(
    segment_tracks: list,
    tracked_detections: list,
    speaker_anchor_x: dict,
    width: int,
    crop_w: int,
) -> list[tuple[float, float, int, str | None]]:
    """
    Choose one horizontal crop target per speaker turn.

    This keeps the speaker-tracked path from "rolling" across the frame.
    We pick a stable representative position for the chosen visual track, then
    let FFmpeg do one short reframe at the segment boundary.
    """
    from statistics import median

    max_crop_x = max(0, width - crop_w)

    def _crop_x_for_center(center_x: float) -> int:
        crop_x = int(round(center_x - crop_w / 2.0))
        return max(0, min(crop_x, max_crop_x))

    def _pick_representative_center(points: list[tuple[float, float]]) -> float:
        first_x = points[0][1]
        median_x = float(median([cx for _, cx in points]))
        seed_x = first_x * 0.72 + median_x * 0.28
        return min(points, key=lambda p: abs(p[1] - seed_x))[1]

    def _split_points_into_stable_runs(
        points: list[tuple[float, float]],
        end_t: float,
    ) -> list[tuple[float, float, list[tuple[float, float]]]]:
        if not points:
            return []

        jump_threshold = max(120.0, crop_w * 0.22)
        gap_threshold = 0.55
        runs = []

        run_start_t = points[0][0]
        run_points = [points[0]]
        candidate_points: list[tuple[float, float]] = []

        def _run_center(ps: list[tuple[float, float]]) -> float:
            return float(median([cx for _, cx in ps]))

        for point in points[1:]:
            gap = point[0] - run_points[-1][0]
            stable_center = _run_center(run_points)
            far_from_run = abs(point[1] - stable_center) > jump_threshold
            gap_reposition = gap > gap_threshold and abs(point[1] - stable_center) > jump_threshold * 0.45
            should_probe_new_run = far_from_run or gap_reposition

            if not should_probe_new_run:
                if candidate_points:
                    run_points.extend(candidate_points)
                    candidate_points = []
                run_points.append(point)
                continue

            candidate_points.append(point)

            candidate_center = _run_center(candidate_points)
            candidate_far = abs(candidate_center - stable_center) > jump_threshold * 0.85
            enough_evidence = len(candidate_points) >= 2 or gap > gap_threshold
            if candidate_far and enough_evidence:
                split_t = candidate_points[0][0]
                runs.append((run_start_t, split_t, run_points))
                run_start_t = split_t
                run_points = candidate_points[:]
                candidate_points = []

        if candidate_points:
            candidate_center = _run_center(candidate_points)
            stable_center = _run_center(run_points)
            if abs(candidate_center - stable_center) > jump_threshold and len(candidate_points) >= 2:
                split_t = candidate_points[0][0]
                runs.append((run_start_t, split_t, run_points))
                run_start_t = split_t
                run_points = candidate_points[:]
            else:
                run_points.extend(candidate_points)

        runs.append((run_start_t, end_t, run_points))
        return runs

    segment_targets = []
    for start_t, end_t, speaker, track_id, _ in segment_tracks:
        points = []
        for t, faces in tracked_detections:
            if t < start_t or t > end_t:
                continue
            face = next((f for f in faces if f["track_id"] == track_id), None)
            if face is not None:
                points.append((t, float(face["cx"])))

        if points:
            for idx, (sub_start_t, sub_end_t, run_points) in enumerate(_split_points_into_stable_runs(points, end_t)):
                if idx == 0:
                    sub_start_t = start_t
                center_x = _pick_representative_center(run_points)
                segment_targets.append((sub_start_t, sub_end_t, _crop_x_for_center(center_x), speaker))
        elif speaker is not None and speaker in speaker_anchor_x:
            center_x = float(speaker_anchor_x[speaker])
            segment_targets.append((start_t, end_t, _crop_x_for_center(center_x), speaker))
        else:
            continue

    return segment_targets


def _build_track_turn_keyframes(
    segment_targets: list,
    default_x: int,
) -> list[tuple[float, int]]:
    """
    Build short bounded reframes between stable speaker-turn targets.

    The camera should hold through the turn, then move quickly onto the new
    speaker instead of slowly chasing the face for seconds.
    """
    keyframes = [(0.0, default_x)]
    prev_x = default_x
    prev_speaker = None
    left_edge_margin = 90

    for target in segment_targets:
        if len(target) >= 4:
            start_t, end_t, target_x, speaker = target[:4]
        else:
            start_t, end_t, target_x = target[:3]
            speaker = None

        if abs(target_x - prev_x) <= 6:
            prev_speaker = speaker
            continue

        seg_duration = max(0.01, end_t - start_t)
        jump = abs(target_x - prev_x)
        same_speaker_continuation = prev_speaker is not None and speaker == prev_speaker
        if same_speaker_continuation and prev_x <= left_edge_margin and jump > 180:
            hold_t = max(0.0, round(start_t - 0.01, 3))
            if hold_t > keyframes[-1][0]:
                keyframes.append((hold_t, prev_x))
            keyframes.append((round(start_t, 3), target_x))
            prev_x = target_x
            prev_speaker = speaker
            continue

        if same_speaker_continuation:
            if jump > 180:
                reframe_t = min(0.18, max(0.10, seg_duration * 0.15))
            else:
                reframe_t = min(0.14, max(0.08, seg_duration * 0.12))
        elif jump > 180:
            reframe_t = min(0.30, max(0.16, seg_duration * 0.24))
        else:
            reframe_t = min(0.22, max(0.12, seg_duration * 0.18))

        settle_t = min(end_t, round(start_t + reframe_t, 3))
        keyframes.append((start_t, prev_x))
        keyframes.append((settle_t, target_x))
        prev_x = target_x
        prev_speaker = speaker

    return keyframes


def _pick_tracking_face(
    faces: list,
    speaker: str,
    speaker_side: dict,
    width: int,
    last_target_x: float | None,
    last_speaker: str | None,
    speaker_anchor_x: dict,
    has_any_split: bool,
) -> tuple[dict | None, bool]:
    """
    Pick a tracking target for the active speaker.

    Returns (face_dict_or_none, reliable_selection).
    reliable_selection means the face is trustworthy enough to update a
    speaker anchor, not merely good enough to keep the camera steady.
    """
    if not faces:
        return None, False

    mid_x = width // 2
    side_hint = speaker_side.get(speaker)
    anchor_x = speaker_anchor_x.get(speaker)

    if len(faces) >= 2:
        if side_hint == "left":
            return min(faces, key=lambda f: f["cx"]), True
        if side_hint == "right":
            return max(faces, key=lambda f: f["cx"]), True
        if last_target_x is not None:
            return min(faces, key=lambda f: abs(f["cx"] - last_target_x)), False
        return max(faces, key=lambda f: f["fw"]), False

    face = faces[0]
    if not has_any_split or side_hint is None:
        return face, True

    side_ok = face["cx"] < mid_x if side_hint == "left" else face["cx"] >= mid_x
    near_anchor = anchor_x is not None and abs(face["cx"] - anchor_x) < width * 0.18

    # On a fresh speaker turn, accept the solo face so we can move with the cut.
    if speaker != last_speaker:
        return face, side_ok or near_anchor

    # Same speaker continuing: reject large contradictory jumps that usually
    # come from reaction shots or layout inserts of the other person.
    if last_target_x is not None and abs(face["cx"] - last_target_x) > width * 0.22 and not (side_ok or near_anchor):
        return None, False

    return face, side_ok or near_anchor


def _choose_camera_speaker(
    transcript_speaker: str | None,
    transcript_duration: float,
    active_speaker: str | None,
    pending_speaker: str | None,
    pending_count: int,
    min_turn_duration: float = 2.6,
    confirmation_frames: int = 3,
) -> tuple[str | None, str | None, int, bool]:
    """
    Stabilize diarization before the camera switches.

    Returns (camera_speaker, pending_speaker, pending_count, switched_now).
    """
    if transcript_speaker is None:
        return active_speaker, pending_speaker, pending_count, False

    if active_speaker is None:
        return transcript_speaker, None, 0, True

    if transcript_speaker == active_speaker:
        return active_speaker, None, 0, False

    # Brief interjections should not move the camera.
    if transcript_duration < min_turn_duration:
        return active_speaker, None, 0, False

    if pending_speaker != transcript_speaker:
        return active_speaker, transcript_speaker, 1, False

    pending_count += 1
    if pending_count < confirmation_frames:
        return active_speaker, pending_speaker, pending_count, False

    return transcript_speaker, None, 0, True


def _track_and_crop(
    input_path: str,
    output_path: str,
    width: int,
    height: int,
    target_w: int,
    target_h: int,
    transcript_words: list = None,
    clip_start: float = 0,
    face_map: dict = None,
) -> Optional[str]:
    """
    Adaptive face tracking with sticky visual tracks and heavy-tripod movement.

    Works for both split-screen and single-camera layouts:
    1. Dense face sampling (~10 fps) with YuNet
    2. Clip-local face tracks (OpenShorts-style identity stickiness)
    3. One chosen visual track per merged speaker turn
    4. Heavy-tripod camera movement with a safe zone
    5. Vertical position fixed for full-height crop
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

    tracked_detections = _assign_face_tracks(detections, width)

    speaker_side = _resolve_speaker_sides(segments, tracked_detections, width, face_map)
    speaker_anchor_x = {}
    if face_map:
        clusters = face_map.get("clusters", [])
        mappings = face_map.get("speaker_mappings", {})
        for speaker, cluster_index in mappings.items():
            if cluster_index is None or cluster_index >= len(clusters):
                continue
            speaker_anchor_x[speaker] = float(clusters[cluster_index]["center_x"])

    segment_tracks = []
    if segments:
        segment_tracks, speaker_anchor_x, speaker_side = _choose_segment_tracks(
            segments=segments,
            tracked_detections=tracked_detections,
            speaker_side=speaker_side,
            speaker_anchor_x=speaker_anchor_x,
            width=width,
        )

    fallback_track_id = None
    if not segment_tracks:
        track_counts = {}
        for _, faces in tracked_detections:
            for face in faces:
                track_id = face["track_id"]
                track_counts[track_id] = track_counts.get(track_id, 0) + 1
        if track_counts:
            fallback_track_id = max(track_counts, key=lambda tid: track_counts[tid])

    # ── Vertical position ────────────────────────────────────────
    # Full-height crop → crop_y is always 0 (or centered if
    # crop_h < height due to aspect-ratio clamping).
    crop_y = max(0, (height - crop_h) // 2)

    keyframes_x = []
    if segment_tracks:
        segment_targets = _choose_track_segment_targets(
            segment_tracks=segment_tracks,
            tracked_detections=tracked_detections,
            speaker_anchor_x=speaker_anchor_x,
            width=width,
            crop_w=crop_w,
        )
        if segment_targets:
            default_x = segment_targets[0][2] if segment_targets[0][0] <= 0.3 else max(0, (width - crop_w) // 2)
            keyframes_x = _build_track_turn_keyframes(
                segment_targets=segment_targets,
                default_x=default_x,
            )

    if not keyframes_x:
        cam_x = float(width) / 2
        first_target_x = None
        if segment_tracks:
            first_track_id = segment_tracks[0][3]
            first_speaker = segment_tracks[0][2]
            for _, faces in tracked_detections:
                face = next((f for f in faces if f["track_id"] == first_track_id), None)
                if face is not None:
                    first_target_x = float(face["cx"])
                    break
            if first_target_x is None and first_speaker is not None:
                first_target_x = speaker_anchor_x.get(first_speaker)
        elif fallback_track_id is not None:
            for _, faces in tracked_detections:
                face = next((f for f in faces if f["track_id"] == fallback_track_id), None)
                if face is not None:
                    first_target_x = float(face["cx"])
                    break

        if first_target_x is not None:
            cam_x = _update_tripod_camera(
                current_center_x=cam_x,
                target_center_x=first_target_x,
                crop_w=crop_w,
                video_width=width,
                dt=0.0,
                force_snap=True,
            )

        prev_t = 0.0
        segment_index = 0

        for t, faces in tracked_detections:
            while segment_index + 1 < len(segment_tracks) and t > segment_tracks[segment_index][1]:
                segment_index += 1

            speaker = None
            target_track_id = fallback_track_id
            if segment_tracks:
                _, _, speaker, target_track_id, _ = segment_tracks[segment_index]

            chosen_face = None
            if target_track_id is not None:
                chosen_face = next((f for f in faces if f["track_id"] == target_track_id), None)

            if chosen_face is not None:
                target_x = float(chosen_face["cx"])
                if speaker is not None:
                    prev_anchor = speaker_anchor_x.get(speaker, target_x)
                    speaker_anchor_x[speaker] = prev_anchor * 0.75 + target_x * 0.25
            elif speaker is not None and speaker in speaker_anchor_x:
                target_x = float(speaker_anchor_x[speaker])
            elif faces:
                target_x = float(max(faces, key=lambda f: f["fw"])["cx"])
            else:
                target_x = None

            cam_x = _update_tripod_camera(
                current_center_x=cam_x,
                target_center_x=target_x,
                crop_w=crop_w,
                video_width=width,
                dt=max(0.01, t - prev_t),
            )

            crop_x = int(cam_x - crop_w / 2)
            crop_x = max(0, min(crop_x, width - crop_w))

            if not keyframes_x or crop_x != keyframes_x[-1][1]:
                keyframes_x.append((round(t, 3), crop_x))

            prev_t = t

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
        blur_filter = _build_motion_blur_filter(keyframes_x)
        zoom_filter = _build_motion_zoom_filter(keyframes_x, target_w=target_w, target_h=target_h)
        if x_expr is None:
            xs = [x for _, x in keyframes_x]
            med_x = sorted(xs)[len(xs) // 2]
            vf = f"crop={crop_w}:{crop_h}:{med_x}:{crop_y}{blur_filter},scale={target_w}:{target_h}{zoom_filter}"
        else:
            vf = f"crop={crop_w}:{crop_h}:{x_expr}:{crop_y}{blur_filter},scale={target_w}:{target_h}{zoom_filter}"

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
    face_map: dict = None,
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

        # Full-height crop is the safest baseline for podcasts. The horizontal
        # crop already creates a strong zoom on 16:9 sources; extra vertical
        # zoom made solo and mixed-layout shots feel too aggressive.
        adjusted_h = height
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

        if face_map:
            clusters = face_map.get("clusters", [])
            mappings = face_map.get("speaker_mappings", {})
            for sp, ci in mappings.items():
                if ci is None or ci >= len(clusters):
                    continue
                speaker_side[sp] = "left" if clusters[ci]["center_x"] < mid_x else "right"

        if has_split and len(speakers) >= 2 and len(speaker_side) < 2:
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
            crop_y = max(0, (height - adjusted_h) // 2)
            return crop_x, crop_y

        # Build per-timestamp crop positions
        timed_crops = []  # (time, crop_x, crop_y, speaker, reliable)
        for t, faces in frame_data:
            if not faces:
                continue
            speaker = _active_speaker_at(t)
            face = _pick_face_for_speaker(faces, speaker, len(faces))
            if face is None:
                continue
            cx, cy, fw, conf = face
            crop_x, crop_y = _crop_xy_for_face(cx, cy)
            reliable = len(faces) >= 2 or speaker not in speaker_side
            timed_crops.append((t, crop_x, crop_y, speaker, reliable))

        if not timed_crops:
            return None

        # ── Phase 4: Build keyframes per speaker segment ───────────────
        # Default position: dominant speaker's median
        speaker_durations = {}
        for start_t, end_t, sp in segments:
            speaker_durations[sp] = speaker_durations.get(sp, 0) + (end_t - start_t)
        dominant_speaker = max(speaker_durations, key=speaker_durations.get)

        dom_crops = [(cx, cy) for _, cx, cy, sp, _ in timed_crops if sp == dominant_speaker]
        default_x = int(np.median([cx for cx, cy in dom_crops])) if dom_crops else width // 2 - adj_w // 2
        default_y = int(np.median([cy for cx, cy in dom_crops])) if dom_crops else 0

        segment_targets = _choose_segment_targets(
            segments=segments,
            timed_crops=timed_crops,
            speakers=speakers,
            default_x=default_x,
            default_y=default_y,
            max_crop_x=max(0, width - adj_w),
            preferred_margin=max(24, int(adj_w * 0.10)),
        )

        # Start locked on the first actual speaker target when available.
        if segment_targets and segment_targets[0][0] <= 0.3:
            default_x = segment_targets[0][2]
            default_y = segment_targets[0][3]

        # Build transition-aware keyframes from segment targets. This keeps the
        # crop stable within a speaker turn and only moves around turn changes.
        x_keyframes, y_keyframes = _build_transition_keyframes(
            segment_targets=segment_targets,
            default_x=default_x,
            default_y=default_y,
            adj_w=adj_w,
            adjusted_h=adjusted_h,
        )

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

                    if dt > 1.5:
                        # Large unsupported gap: hold until the next reliable
                        # keyframe instead of inventing motion through missing data.
                        jump_t = t1 - 0.01
                        parts.append(
                            f"if(between(t\\,{t0:.2f}\\,{jump_t:.2f})\\,{v0}\\,"
                        )
                        parts.append(
                            f"if(between(t\\,{jump_t:.2f}\\,{t1:.2f})\\,{v1}\\,"
                        )
                    elif value_jump > jump_threshold:
                        # Host/guest switches should move with intent, not snap.
                        pan_t = min(0.30, dt)
                        mid_t = t0 + pan_t
                        parts.append(
                            f"if(between(t\\,{t0:.2f}\\,{mid_t:.2f})\\,"
                            f"{v0}+(({v1}-{v0})*(t-{t0:.2f})/{pan_t:.2f})\\,"
                        )
                        if mid_t < t1:
                            parts.append(
                                f"if(between(t\\,{mid_t:.2f}\\,{t1:.2f})\\,{v1}\\,"
                            )
                    elif value_jump > jump_threshold * 0.6:
                        # Medium jump: still move briskly, but slower than a cut.
                        pan_t = min(0.24, dt)
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


def _choose_segment_targets(
    segments: list,
    timed_crops: list,
    speakers: list,
    default_x: int,
    default_y: int,
    max_crop_x: int,
    preferred_margin: int,
) -> list:
    """
    Choose one crop target per speaker segment.

    timed_crops entries are (time, crop_x, crop_y, speaker, reliable).
    Reliable crops come from frames where speaker identity is trustworthy,
    e.g. split-screen frames with known side mapping.
    """
    from statistics import median

    def _pick_representative(points: list[tuple]) -> tuple[int, int]:
        first_x = points[0][1]
        first_y = points[0][2]
        median_x = int(median([cx for _, cx, _ in points]))
        median_y = int(median([cy for _, _, cy in points]))

        # Bias toward how the speaker first appears in the turn so the camera
        # starts in the right place, then settle around the segment median.
        seed_x = int(round(first_x * 0.7 + median_x * 0.3))
        seed_y = int(round(first_y * 0.7 + median_y * 0.3))

        def _score(point):
            _, cx, cy = point
            edge_margin = min(cx, max_crop_x - cx) if max_crop_x > 0 else preferred_margin
            edge_penalty = max(0, preferred_margin - edge_margin) * 3
            return abs(cx - seed_x) + abs(cy - seed_y) * 0.35 + edge_penalty

        _, best_x, best_y = min(points, key=_score)
        return int(best_x), int(best_y)

    speaker_anchors = {}
    for speaker in speakers:
        reliable = [
            (t, cx, cy)
            for t, cx, cy, sp, is_reliable in timed_crops
            if sp == speaker and is_reliable
        ]
        if reliable:
            speaker_anchors[speaker] = _pick_representative(reliable)

    segment_targets = []
    for start_t, end_t, speaker in segments:
        reliable_seg_crops = [
            (t, cx, cy)
            for t, cx, cy, sp, is_reliable in timed_crops
            if start_t <= t <= end_t and sp == speaker and is_reliable
        ]
        seg_crops = [
            (t, cx, cy)
            for t, cx, cy, sp, _ in timed_crops
            if start_t <= t <= end_t and sp == speaker
        ]

        if reliable_seg_crops:
            med_x, med_y = _pick_representative(reliable_seg_crops)
        elif seg_crops and speaker in speaker_anchors:
            local_x, local_y = _pick_representative(seg_crops)
            anchor_x, anchor_y = speaker_anchors[speaker]
            local_is_centerish = preferred_margin <= local_x <= max_crop_x - preferred_margin
            local_overrides_anchor = (
                start_t <= 0.35
                or (local_is_centerish and abs(local_x - anchor_x) > max(80, int(max_crop_x * 0.18)))
            )
            if local_overrides_anchor:
                med_x, med_y = local_x, local_y
            else:
                med_x, med_y = anchor_x, anchor_y
        elif speaker in speaker_anchors:
            med_x, med_y = speaker_anchors[speaker]
        elif seg_crops:
            med_x, med_y = _pick_representative(seg_crops)
        else:
            sp_crops = [(t, cx, cy) for t, cx, cy, sp, _ in timed_crops if sp == speaker]
            if sp_crops:
                med_x, med_y = _pick_representative(sp_crops)
            else:
                med_x = default_x
                med_y = default_y

        segment_targets.append((start_t, end_t, med_x, med_y))

    return segment_targets


def _build_transition_keyframes(
    segment_targets: list,
    default_x: int,
    default_y: int,
    adj_w: int,
    adjusted_h: int,
) -> tuple[list, list]:
    """
    Convert per-segment crop targets into eased transition keyframes.
    """
    x_keyframes = [(0.0, default_x)]
    y_keyframes = [(0.0, default_y)]
    prev_x = default_x
    prev_y = default_y

    for start_t, end_t, target_x, target_y in segment_targets:
        seg_duration = max(0.01, end_t - start_t)
        delta_x = abs(target_x - prev_x)
        delta_y = abs(target_y - prev_y)

        if delta_x <= 6 and delta_y <= 6:
            continue

        x_keyframes.append((start_t, prev_x))
        y_keyframes.append((start_t, prev_y))

        if delta_x > adj_w * 0.33 or delta_y > adjusted_h * 0.12:
            pan_t = min(0.38, seg_duration * 0.45)
        else:
            pan_t = min(0.26, seg_duration * 0.35)

        settle_t = min(end_t, start_t + max(0.08, pan_t))
        x_keyframes.append((settle_t, target_x))
        y_keyframes.append((settle_t, target_y))
        prev_x = target_x
        prev_y = target_y

    return x_keyframes, y_keyframes


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
