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

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode == 0:
        return output_path

    # If not already CPU, retry with libx264
    if enc_flags != CPU_FLAGS:
        print(f"Warning: HW encoder failed for {label}, falling back to libx264", file=sys.stderr)
        cmd_fallback = cmd_parts_before_enc + CPU_FLAGS + cmd_parts_after_enc + [output_path]
        result2 = subprocess.run(cmd_fallback, capture_output=True, text=True)
        if result2.returncode == 0:
            return output_path
        raise RuntimeError(f"FFmpeg {label} failed (both HW and CPU): {result2.stderr[-500:]}")

    raise RuntimeError(f"FFmpeg {label} failed: {result.stderr[-500:]}")


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
    result = subprocess.run(cmd, capture_output=True, text=True)
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
    Stream-copy (-c copy) snaps to keyframes which can be seconds off,
    causing caption-audio desync. Re-encoding with ultrafast is still
    fast and guarantees the output starts at exactly the requested time.
    """
    duration = end_second - start_second

    # Use lossless copy for this intermediate step — the crop/caption
    # pipeline will re-encode with the proper encoder anyway.
    # -ss before -i seeks to nearest keyframe, -noaccurate_seek is fast.
    # We still re-encode video to get frame-accurate start, but use
    # high quality CRF to minimize generation loss.
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
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"FFmpeg cut failed: {result.stderr[-500:]}")
    return output_path


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
        crop_x_expr = None

        # Fast path: use pre-computed face_map from transcription
        # Only use if we have speaker mappings — without them, face_map
        # can't know which face to follow in a multi-face layout
        if face_map and face_map.get("clusters") and face_map.get("speaker_mappings"):
            crop_x_expr = _use_face_map(
                face_map, transcript_words, clip_start,
                width, height, target_ratio,
            )

        # Slow path: analyze video on-the-fly (no face_map cached)
        if crop_x_expr is None:
            has_speakers = (
                transcript_words
                and len(set(w.get("speaker") for w in transcript_words if w.get("speaker"))) > 1
            )

            if has_speakers:
                crop_x_expr = _build_speaker_aware_crop(
                    input_path, width, height, target_ratio,
                    transcript_words, clip_start,
                )
            else:
                # Single speaker: find face and center on it (static crop).
                # Use _detect_face_center for a stable, centered position.
                crop_x_expr = _detect_face_center(input_path, width, height, target_ratio)

        if crop_x_expr is not None:
            crop_h = height
            crop_w = int(crop_h * target_ratio)
            vf = f"crop={crop_w}:{crop_h}:{crop_x_expr}:0,scale={target_w}:{target_h}"
        else:
            strategy = "center"

    if strategy == "center":
        if source_ratio > target_ratio:
            crop_h = height
            crop_w = int(crop_h * target_ratio)
            crop_x = (width - crop_w) // 2
            vf = f"crop={crop_w}:{crop_h}:{crop_x}:0,scale={target_w}:{target_h}"
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


def _use_face_map(
    face_map: dict,
    transcript_words: list,
    clip_start: float,
    width: int,
    height: int,
    target_ratio: float,
) -> Optional[str]:
    """
    Use pre-computed face_map from transcription to determine crop position.
    Much faster than re-scanning the video.

    For multi-speaker clips: builds speaker-aware panning expression.
    For single-speaker: returns the dominant speaker's crop position.
    """
    clusters = face_map.get("clusters", [])
    speaker_mappings = face_map.get("speaker_mappings", {})
    dominant = face_map.get("dominant_speaker")
    crop_w = int(height * target_ratio)

    if not clusters:
        return None

    # Verify face_map was computed at the same resolution
    map_w = face_map.get("video_width", width)
    if map_w != width:
        # Resolution mismatch — face_map positions don't apply
        return None

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
        # Fallback to dominant speaker's position
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
        t = w["start"] - clip_start
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

    # Build keyframes
    pan_duration = 0.4
    duration = segments[-1][1] if segments else 1.0
    keyframes = []
    prev_x = default_x

    for start_t, end_t, speaker in segments:
        ci = speaker_mappings.get(speaker)
        target_x = clusters[ci]["crop_x"] if ci is not None and ci < len(clusters) else default_x

        if target_x != prev_x:
            keyframes.append((start_t, prev_x))
            keyframes.append((start_t + pan_duration, target_x))
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

    Split-screen aware: in a podcast layout where both faces are always
    visible, we detect left/right face positions, sort speakers by their
    average word position in the timeline, and map the first speaker to
    the left face and the second to the right (standard podcast layout).

    For non-split-screen (single face visible at a time), falls back to
    tracking whichever face is visible when each speaker talks.

    Returns an FFmpeg crop x expression string, or None if detection fails.
    """
    try:
        import cv2
        import numpy as np

        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            return None

        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        fps = cap.get(cv2.CAP_PROP_FPS) or 30
        duration = total_frames / fps
        crop_w = int(height * target_ratio)

        # Load DNN face detector
        backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        proto = os.path.join(backend_dir, "models", "deploy.prototxt")
        model = os.path.join(backend_dir, "models", "res10_300x300_ssd_iter_140000.caffemodel")
        if not (os.path.exists(proto) and os.path.exists(model)):
            cap.release()
            return None

        detector = cv2.dnn.readNetFromCaffe(proto, model)

        # Sample frames and detect ALL faces
        sample_count = min(80, max(30, int(duration * 4)))
        face_observations = []  # (time, face_center_x, face_width)
        faces_per_frame = []    # count of faces per sampled frame

        for i in range(sample_count):
            t = i * duration / sample_count
            cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
            ret, frame = cap.read()
            if not ret:
                continue

            h, w = frame.shape[:2]
            blob = cv2.dnn.blobFromImage(cv2.resize(frame, (300, 300)), 1.0, (300, 300), (104.0, 177.0, 123.0))
            detector.setInput(blob)
            detections = detector.forward()

            frame_faces = 0
            for j in range(detections.shape[2]):
                conf = detections[0, 0, j, 2]
                if conf > 0.5:
                    x1 = int(detections[0, 0, j, 3] * w)
                    x2 = int(detections[0, 0, j, 5] * w)
                    fw = x2 - x1
                    if fw < w * 0.04:
                        continue
                    cx = (x1 + x2) // 2
                    face_observations.append((t, cx, fw))
                    frame_faces += 1
            faces_per_frame.append(frame_faces)

        cap.release()

        if len(face_observations) < 5:
            return None

        # Cluster faces by position (left vs right).
        # Simple split: faces in the left half vs right half of the frame.
        # This is more robust than radius-based clustering which can merge
        # faces that are close to the center line.
        positions = np.array([obs[1] for obs in face_observations])
        mid_x = width // 2

        left_pos = positions[positions < mid_x]
        right_pos = positions[positions >= mid_x]

        clusters = []
        if len(left_pos) >= 3:
            cx = int(np.median(left_pos))
            clusters.append({
                "center_x": cx,
                "count": len(left_pos),
                "crop_x": max(0, min(cx - crop_w // 2, mid_x - crop_w)),
            })
        if len(right_pos) >= 3:
            cx = int(np.median(right_pos))
            clusters.append({
                "center_x": cx,
                "count": len(right_pos),
                "crop_x": max(mid_x, min(cx - crop_w // 2, width - crop_w)),
            })

        if len(clusters) < 2:
            # Not a clear split-screen — try single cluster from all positions
            if len(positions) >= 3:
                cx = int(np.median(positions))
                crop_x = max(0, min(cx - crop_w // 2, width - crop_w))
                return str(crop_x)
            return None

        # Sort clusters left to right
        clusters.sort(key=lambda c: c["center_x"])

        # Get unique speakers
        speakers = sorted(set(w.get("speaker") for w in transcript_words if w.get("speaker")))
        if len(speakers) < 2:
            clusters.sort(key=lambda c: c["count"], reverse=True)
            return str(clusters[0]["crop_x"])

        # Detect split-screen: if most frames have 2+ faces, it's split-screen
        avg_faces = np.mean(faces_per_frame) if faces_per_frame else 0
        is_split_screen = avg_faces >= 1.5

        speaker_to_cluster = {}

        if is_split_screen and len(clusters) >= 2 and len(speakers) == 2:
            # Split-screen: both faces are always visible, so we can't use
            # audio-visual correlation (both clusters get equal votes).
            # Use positional heuristic: whoever speaks first in this clip
            # is mapped to the LEFT face (standard podcast layout: host left,
            # host does intro). Sort speakers by first word time, not label.
            speaker_first_word = {}
            for w in transcript_words:
                sp = w.get("speaker")
                if sp and sp not in speaker_first_word:
                    speaker_first_word[sp] = w["start"]
            speakers_by_first_word = sorted(speakers, key=lambda s: speaker_first_word.get(s, float("inf")))
            speaker_to_cluster[speakers_by_first_word[0]] = clusters[0]  # left
            speaker_to_cluster[speakers_by_first_word[1]] = clusters[1]  # right
        else:
            # Non-split-screen: only one face visible at a time.
            # Map by which face is visible when each speaker talks.
            for speaker in speakers:
                speaker_times = [
                    w["start"] - clip_start
                    for w in transcript_words
                    if w.get("speaker") == speaker
                ]
                cluster_votes = [0] * len(clusters)
                for t in speaker_times:
                    for obs_t, obs_cx, obs_fw in face_observations:
                        if abs(obs_t - t) < 1.0:
                            for ci, cl in enumerate(clusters):
                                if abs(obs_cx - cl["center_x"]) < cluster_radius:
                                    cluster_votes[ci] += 1
                if any(cluster_votes):
                    best = max(range(len(cluster_votes)), key=lambda i: cluster_votes[i])
                    speaker_to_cluster[speaker] = clusters[best]

        # Build speaker segments from transcript words
        segments = []  # (start_time_in_clip, end_time_in_clip, speaker)
        current_speaker = None
        seg_start = 0

        for w in sorted(transcript_words, key=lambda x: x["start"]):
            sp = w.get("speaker")
            t = w["start"] - clip_start
            if sp != current_speaker and sp is not None:
                if current_speaker is not None:
                    segments.append((seg_start, t, current_speaker))
                current_speaker = sp
                seg_start = t

        if current_speaker is not None:
            segments.append((seg_start, duration, current_speaker))

        if not segments:
            clusters.sort(key=lambda c: c["count"], reverse=True)
            return str(clusters[0]["crop_x"])

        # Merge very short segments (<1.0s) into neighbors to avoid jitter.
        # 0.5s was too aggressive — fast back-and-forth crosstalk caused rapid panning.
        merged = []
        for seg in segments:
            if merged and seg[2] == merged[-1][2]:
                # Same speaker: extend previous segment
                merged[-1] = (merged[-1][0], seg[1], seg[2])
            elif merged and (seg[1] - seg[0]) < 1.0:
                # Too short to pan — absorb into previous segment
                merged[-1] = (merged[-1][0], seg[1], merged[-1][2])
            else:
                merged.append(list(seg))
        segments = merged

        # Default position: whichever speaker talks most
        speaker_durations = {}
        for start_t, end_t, sp in segments:
            speaker_durations[sp] = speaker_durations.get(sp, 0) + (end_t - start_t)
        dominant_speaker = max(speaker_durations, key=speaker_durations.get)
        default_cluster = speaker_to_cluster.get(dominant_speaker) or clusters[0]
        default_x = default_cluster["crop_x"]

        # Build a timeline: list of (time, target_x) keyframes with smooth transitions.
        # Instead of nested if/between (breaks with many segments), use a linear
        # interpolation chain: lerp between keyframes.
        pan_duration = 0.4  # 400ms smooth pan (was 300ms, felt jerky)

        # Build keyframe list: (time, crop_x)
        # In split-screen, verify target face is visible before panning.
        # If face isn't detected at that time, stay at current position.
        keyframes = []
        prev_x = default_x
        for start_t, end_t, speaker in segments:
            cl = speaker_to_cluster.get(speaker)
            target_x = cl["crop_x"] if cl else default_x

            # Verify target face is visible at this time (avoid panning to empty space)
            if is_split_screen and cl:
                # Check if any face detection near the target cluster exists at segment start
                face_near_target = any(
                    abs(obs_t - start_t) < 1.5 and abs(obs_cx - cl["center_x"]) < cluster_radius
                    for obs_t, obs_cx, obs_fw in face_observations
                )
                if not face_near_target:
                    target_x = prev_x  # Stay at current position

            if target_x != prev_x:
                # Ease into the new position over pan_duration
                keyframes.append((start_t, prev_x))
                keyframes.append((start_t + pan_duration, target_x))
            prev_x = target_x

        if not keyframes:
            return str(default_x)

        # Build FFmpeg expression using chained lerp:
        # For each keyframe pair, if t is in [t0, t1], lerp from x0 to x1
        # Otherwise fall through to next pair or default
        expr_parts = []
        for i in range(0, len(keyframes) - 1, 2):
            t0, x0 = keyframes[i]
            t1, x1 = keyframes[i + 1]
            # During transition: linear interpolation
            expr_parts.append(
                f"if(between(t\\,{t0:.2f}\\,{t1:.2f})\\,"
                f"{x0}+(({x1}-{x0})*(t-{t0:.2f})/{max(0.01, t1 - t0):.2f})\\,"
            )
            # After transition until next one: hold at target
            if i + 2 < len(keyframes):
                next_t = keyframes[i + 2][0]
            else:
                next_t = duration
            expr_parts.append(
                f"if(between(t\\,{t1:.2f}\\,{next_t:.2f})\\,"
                f"{x1}\\,"
            )

        # Cap nesting depth to avoid FFmpeg expression parser limits
        if len(expr_parts) > 30:
            print(f"Warning: {len(expr_parts)} pan segments, simplifying to avoid FFmpeg limits", file=sys.stderr)
            # Fallback: just use the dominant speaker position
            return str(default_x)

        expr = "".join(expr_parts) + str(default_x) + ")" * len(expr_parts)

        return expr

    except ImportError:
        return None
    except Exception as e:
        print(f"Warning: speaker-aware crop failed: {e}", file=sys.stderr)
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
    result = subprocess.run(cmd, capture_output=True, text=True)
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

    # Run with HW encoder, fallback to CPU if it fails
    enc_flags = get_video_encode_flags()
    cmd = [
        "ffmpeg", "-y",
        *inputs,
        "-filter_complex", filter_complex,
        "-map", "[out]",
        "-map", "0:a",
        *enc_flags,
        "-c:a", "copy",
        "-movflags", "+faststart",
        output_path,
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)

    # Fallback to CPU if HW encoder failed
    if result.returncode != 0 and enc_flags != CPU_FLAGS:
        print(f"Warning: HW encoder failed for caption burn, falling back to libx264", file=sys.stderr)
        cmd_fallback = [
            "ffmpeg", "-y",
            *inputs,
            "-filter_complex", filter_complex,
            "-map", "[out]",
            "-map", "0:a",
            *CPU_FLAGS,
            "-c:a", "copy",
            "-movflags", "+faststart",
            output_path,
        ]
        result = subprocess.run(cmd_fallback, capture_output=True, text=True)

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
    probe = subprocess.run(probe_cmd, capture_output=True, text=True)
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
        result = subprocess.run(xfade_cmd, capture_output=True, text=True)
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
        result = subprocess.run(cmd, capture_output=True, text=True)
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
    result = subprocess.run(measure_cmd, capture_output=True, text=True)

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
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"FFmpeg normalize failed: {result.stderr[-500:]}")
    return output_path


def _detect_face_center(
    video_path: str,
    width: int,
    height: int,
    target_ratio: float,
) -> Optional[str]:
    """
    Simple static face detection — finds the dominant face and returns a
    STATIC crop position that centers the face horizontally. No tracking,
    no panning. The face stays centered for the entire clip.
    """
    try:
        import cv2
        import numpy as np

        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            return None

        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        fps = cap.get(cv2.CAP_PROP_FPS) or 30
        duration = total_frames / fps
        crop_w = int(height * target_ratio)

        # Load DNN face detector
        backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        proto = os.path.join(backend_dir, "models", "deploy.prototxt")
        model = os.path.join(backend_dir, "models", "res10_300x300_ssd_iter_140000.caffemodel")
        if not (os.path.exists(proto) and os.path.exists(model)):
            cap.release()
            return None

        detector = cv2.dnn.readNetFromCaffe(proto, model)

        # Sample frames across the clip
        sample_count = min(40, max(10, int(duration * 2)))
        face_positions = []

        for i in range(sample_count):
            t = i * duration / sample_count
            cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
            ret, frame = cap.read()
            if not ret:
                continue

            h, w = frame.shape[:2]
            blob = cv2.dnn.blobFromImage(
                cv2.resize(frame, (300, 300)), 1.0, (300, 300),
                (104.0, 177.0, 123.0)
            )
            detector.setInput(blob)
            detections = detector.forward()

            best_score = 0
            best_cx = None
            for j in range(detections.shape[2]):
                conf = detections[0, 0, j, 2]
                if conf > 0.5:
                    x1 = int(detections[0, 0, j, 3] * w)
                    x2 = int(detections[0, 0, j, 5] * w)
                    face_w = x2 - x1
                    if face_w < w * 0.05:
                        continue
                    score = conf * (face_w ** 2)
                    if score > best_score:
                        best_score = score
                        best_cx = (x1 + x2) // 2

            if best_cx is not None:
                face_positions.append(best_cx)

        cap.release()

        if not face_positions:
            return None

        # Detect split-screen: check if faces appear in both halves
        positions = np.array(face_positions)
        mid_x = width // 2

        left_count = np.sum(positions < mid_x)
        right_count = np.sum(positions >= mid_x)

        if left_count >= 3 and right_count >= 3:
            # Split-screen — pick the side with the most detections,
            # clamp crop to that half so we never show the seam.
            if left_count >= right_count:
                side_pos = positions[positions < mid_x]
                face_x = int(np.median(side_pos))
                crop_x = max(0, min(face_x - crop_w // 2, mid_x - crop_w))
            else:
                side_pos = positions[positions >= mid_x]
                face_x = int(np.median(side_pos))
                crop_x = max(mid_x, min(face_x - crop_w // 2, width - crop_w))
        else:
            face_x = int(np.median(face_positions))
            crop_x = face_x - crop_w // 2
            crop_x = max(0, min(crop_x, width - crop_w))

        return str(crop_x)

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
) -> Optional[str]:
    """
    Continuous face tracking — detects face position over time and returns
    an FFmpeg expression that smoothly follows the dominant face.

    Samples frames throughout the clip, picks the dominant face cluster,
    then builds a smoothed panning timeline so the crop follows head movement.

    Returns an FFmpeg crop-x expression string, or None if no face detected.
    """
    try:
        import cv2
        import numpy as np

        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            return None

        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        fps = cap.get(cv2.CAP_PROP_FPS) or 30
        duration = total_frames / fps
        crop_w = int(height * target_ratio)

        # Sample ~4 frames/sec for smooth tracking (more than before)
        sample_count = min(240, max(30, int(duration * 4)))
        sample_times = [i * duration / sample_count for i in range(sample_count)]

        # (time, face_center_x) for each detection
        timed_positions = []

        # Load DNN face detector
        dnn_detector = None
        backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        bundled_proto = os.path.join(backend_dir, "models", "deploy.prototxt")
        bundled_model = os.path.join(backend_dir, "models",
                                     "res10_300x300_ssd_iter_140000.caffemodel")
        cv2_proto = os.path.join(os.path.dirname(cv2.__file__), "data",
                                 "deploy.prototxt")
        cv2_model = os.path.join(os.path.dirname(cv2.__file__), "data",
                                 "res10_300x300_ssd_iter_140000.caffemodel")
        proto_path = bundled_proto if os.path.exists(bundled_proto) else cv2_proto
        model_path = bundled_model if os.path.exists(bundled_model) else cv2_model
        if os.path.exists(proto_path) and os.path.exists(model_path):
            dnn_detector = cv2.dnn.readNetFromCaffe(proto_path, model_path)

        # Fallback: Haar cascades
        cascades = []
        if dnn_detector is None:
            for cascade_name in [
                "haarcascade_frontalface_default.xml",
                "haarcascade_frontalface_alt2.xml",
                "haarcascade_profileface.xml",
            ]:
                cascade_path = cv2.data.haarcascades + cascade_name
                if os.path.exists(cascade_path):
                    cascades.append(cv2.CascadeClassifier(cascade_path))

        for t in sample_times:
            cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
            ret, frame = cap.read()
            if not ret:
                continue

            h, w = frame.shape[:2]

            if dnn_detector is not None:
                blob = cv2.dnn.blobFromImage(
                    cv2.resize(frame, (300, 300)), 1.0, (300, 300),
                    (104.0, 177.0, 123.0)
                )
                dnn_detector.setInput(blob)
                detections = dnn_detector.forward()

                best_score = 0
                best_cx = None
                for i in range(detections.shape[2]):
                    confidence = detections[0, 0, i, 2]
                    if confidence > 0.5:
                        x1 = int(detections[0, 0, i, 3] * w)
                        y1 = int(detections[0, 0, i, 4] * h)
                        x2 = int(detections[0, 0, i, 5] * w)
                        y2 = int(detections[0, 0, i, 6] * h)
                        face_w = x2 - x1
                        face_h = y2 - y1
                        if face_w < w * 0.05 or face_h < h * 0.05:
                            continue
                        score = confidence * (face_w ** 2)
                        if score > best_score:
                            best_score = score
                            best_cx = (x1 + x2) // 2

                if best_cx is not None:
                    timed_positions.append((t, best_cx))
            else:
                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                for cascade in cascades:
                    faces = cascade.detectMultiScale(
                        gray, 1.1, 5, minSize=(60, 60)
                    )
                    if len(faces) > 0:
                        largest = max(faces, key=lambda f: f[2] * f[3])
                        face_center_x = largest[0] + largest[2] // 2
                        timed_positions.append((t, face_center_x))
                        break

        cap.release()

        if len(timed_positions) < 3:
            return None

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
            return str(crop_x)

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
        # If the face barely moves (range < 8% of frame width), just use static.
        # Most podcast clips have speakers sitting still — tracking should be rare.
        movement_range = int(smoothed.max() - smoothed.min())
        if movement_range < width * 0.08:
            return str(int(np.median(smoothed)))

        # --- Build keyframes: only emit on MAJOR position changes ---
        # Very conservative: only pan when speaker physically moves to a new
        # position (leans far, switches seats). Normal head movement = static.
        keyframes = [(tracked_times[0], int(smoothed[0]))]
        min_time_gap = 3.0   # Don't place keyframes closer than 3s
        min_px_change = 80   # Ignore movement smaller than 80px (~7% of frame)

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
            return str(keyframes[0][1])

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
            return str(int(np.median(smoothed)))

        # Final value: last keyframe position
        last_x = keyframes[-1][1]
        expr = "".join(expr_parts) + str(last_x) + ")" * len(expr_parts)

        return expr

    except ImportError:
        return None
    except Exception as e:
        print(f"Warning: face tracking failed: {e}", file=sys.stderr)
        return None
