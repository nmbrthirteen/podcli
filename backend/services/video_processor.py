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

    cmd = [
        "ffmpeg", "-y",
        "-ss", str(start_second),
        "-i", input_path,
        "-t", str(duration),
        "-c:v", "libx264", "-crf", "18", "-preset", "ultrafast",
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
        # Check if we have multi-speaker data — if so, do speaker-aware crop
        has_speakers = (
            transcript_words
            and len(set(w.get("speaker") for w in transcript_words if w.get("speaker"))) > 1
        )

        if has_speakers:
            crop_x_expr = _build_speaker_aware_crop(
                input_path, width, height, target_ratio,
                transcript_words, clip_start,
            )
            if crop_x_expr is not None:
                crop_h = height
                crop_w = int(crop_h * target_ratio)
                # crop_x_expr is either a static int or an FFmpeg expression for dynamic panning
                vf = f"crop={crop_w}:{crop_h}:{crop_x_expr}:0,scale={target_w}:{target_h}"
            else:
                strategy = "center"
        else:
            # Single speaker or no speaker data — use static face detection
            crop_x = _detect_face_offset(input_path, width, height, target_ratio)
            if crop_x is not None:
                crop_h = height
                crop_w = int(crop_h * target_ratio)
                vf = f"crop={crop_w}:{crop_h}:{crop_x}:0,scale={target_w}:{target_h}"
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

    1. Detect ALL faces in the video and cluster them by position (left vs right).
    2. Map each cluster to a speaker using transcript word timing + face position.
    3. Build an FFmpeg expression that switches crop_x based on timestamp.

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

        # Sample frames and detect ALL faces with their positions and timestamps
        sample_count = min(80, max(30, int(duration * 4)))  # ~4 samples/sec
        face_observations = []  # list of (time_in_clip, face_center_x, face_width)

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

        cap.release()

        if len(face_observations) < 5:
            return None

        # Cluster faces into positions (typically 2 for a podcast: left + right)
        positions = np.array([obs[1] for obs in face_observations])
        cluster_radius = width * 0.15

        clusters = []
        used = np.zeros(len(positions), dtype=bool)
        sorted_idx = np.argsort(positions)

        for idx in sorted_idx:
            if used[idx]:
                continue
            mask = np.abs(positions - positions[idx]) < cluster_radius
            mask &= ~used
            cluster_indices = np.where(mask)[0]
            if len(cluster_indices) > 0:
                cluster_center = int(np.median(positions[mask]))
                clusters.append({
                    "center_x": cluster_center,
                    "count": len(cluster_indices),
                    "crop_x": max(0, min(cluster_center - crop_w // 2, width - crop_w)),
                })
                used |= mask

        if len(clusters) < 2:
            # Only one face position found — use static crop on it
            if clusters:
                return str(clusters[0]["crop_x"])
            return None

        # Sort clusters left to right
        clusters.sort(key=lambda c: c["center_x"])

        # Map speakers to face positions using transcript word timing.
        # For each speaker, find which face cluster is most visible when they talk.
        speakers = list(set(w.get("speaker") for w in transcript_words if w.get("speaker")))
        if len(speakers) < 2:
            # Can't distinguish — use the most frequent face
            clusters.sort(key=lambda c: c["count"], reverse=True)
            return str(clusters[0]["crop_x"])

        speaker_to_cluster = {}
        for speaker in speakers:
            # Get time ranges when this speaker is talking (relative to clip)
            speaker_times = []
            for w in transcript_words:
                if w.get("speaker") == speaker:
                    speaker_times.append(w["start"] - clip_start)

            # Count which face cluster is most visible during this speaker's words
            cluster_votes = [0] * len(clusters)
            for t in speaker_times:
                # Find face observations near this timestamp
                for obs_t, obs_cx, obs_fw in face_observations:
                    if abs(obs_t - t) < 1.0:  # within 1 second
                        for ci, cl in enumerate(clusters):
                            if abs(obs_cx - cl["center_x"]) < cluster_radius:
                                cluster_votes[ci] += 1

            if any(cluster_votes):
                best_cluster_idx = max(range(len(cluster_votes)), key=lambda i: cluster_votes[i])
                speaker_to_cluster[speaker] = clusters[best_cluster_idx]

        # Build an FFmpeg expression that pans based on who's speaking.
        # We create time-based segments and smooth-pan between positions.
        # Group consecutive words by speaker to create segments
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

        # Build FFmpeg crop x expression using nested if(between()) for each segment.
        # This creates smooth panning between speakers.
        # Default to the most-detected face
        default_cluster = max(clusters, key=lambda c: c["count"])
        default_x = default_cluster["crop_x"]

        # Simplify: if only 2 face positions, use a clean between() chain
        expr_parts = []
        for start_t, end_t, speaker in segments:
            cl = speaker_to_cluster.get(speaker)
            if cl and cl["crop_x"] != default_x:
                expr_parts.append(f"if(between(t\\,{start_t:.1f}\\,{end_t:.1f})\\,{cl['crop_x']}\\,")

        if not expr_parts:
            return str(default_x)

        # Build nested expression: if(cond1, val1, if(cond2, val2, default))
        expr = ""
        for part in expr_parts:
            expr += part
        expr += str(default_x)
        expr += ")" * len(expr_parts)

        return expr

    except ImportError:
        return None
    except Exception:
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
) -> str:
    """
    Append an outro video to the end of the main clip.
    Re-encodes the outro to match the main clip's resolution and codec.
    """
    width, height = get_dimensions(input_path)

    concat_list = os.path.join(os.path.dirname(output_path), "concat_list.txt")

    # Re-encode outro to match main clip's dimensions
    outro_scaled = output_path + ".outro_scaled.mp4"
    _run_ffmpeg_with_fallback(
        cmd_parts_before_enc=[
            "ffmpeg", "-y",
            "-i", outro_path,
            "-vf", f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
                   f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black",
        ],
        cmd_parts_after_enc=[
            "-c:a", "aac", "-b:a", "128k", "-ar", "44100",
            "-movflags", "+faststart",
        ],
        output_path=outro_scaled,
        label="outro_scale",
    )

    # Re-encode main clip to ensure compatible streams for concat
    main_reenc = output_path + ".main_reenc.mp4"
    _run_ffmpeg_with_fallback(
        cmd_parts_before_enc=[
            "ffmpeg", "-y",
            "-i", input_path,
            "-vf", f"scale={width}:{height}",
        ],
        cmd_parts_after_enc=[
            "-c:a", "aac", "-b:a", "128k", "-ar", "44100",
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
        "-b:a", "128k",
        "-ar", "44100",
        "-movflags", "+faststart",
        output_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"FFmpeg normalize failed: {result.stderr[-500:]}")
    return output_path


def _detect_face_offset(
    video_path: str,
    width: int,
    height: int,
    target_ratio: float,
) -> Optional[int]:
    """
    Detect face position across the video and return crop x-offset.
    Uses DNN-based face detector (more robust than Haar cascades).
    Strongly favors the largest/closest face and clusters positions
    to lock onto the dominant speaker when multiple faces are visible.
    Returns None if no face detected (caller should fall back to center).
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

        # Sample more frames across the full clip for better coverage
        sample_count = 60
        sample_times = [i * duration / sample_count for i in range(sample_count)]

        face_positions = []

        # Try DNN detector first (much more robust than Haar cascades)
        dnn_detector = None

        # Look for bundled DNN model first, then fall back to OpenCV's data dir
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

        # Fallback: load multiple Haar cascades for better coverage
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
                # DNN-based detection: handles frontal, profile, angled faces
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
                    if confidence > 0.5:  # Higher threshold to reduce false positives
                        x1 = int(detections[0, 0, i, 3] * w)
                        y1 = int(detections[0, 0, i, 4] * h)
                        x2 = int(detections[0, 0, i, 5] * w)
                        y2 = int(detections[0, 0, i, 6] * h)
                        face_w = x2 - x1
                        face_h = y2 - y1
                        # Skip tiny detections (likely false positives)
                        if face_w < w * 0.05 or face_h < h * 0.05:
                            continue
                        # Quadratic face-size weighting: strongly favor larger/closer faces
                        score = confidence * (face_w ** 2)
                        if score > best_score:
                            best_score = score
                            best_cx = (x1 + x2) // 2

                if best_cx is not None:
                    face_positions.append(best_cx)
            else:
                # Haar cascade fallback: try all loaded cascades
                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                for cascade in cascades:
                    faces = cascade.detectMultiScale(
                        gray, 1.1, 5, minSize=(60, 60)
                    )
                    if len(faces) > 0:
                        largest = max(faces, key=lambda f: f[2] * f[3])
                        face_center_x = largest[0] + largest[2] // 2
                        face_positions.append(face_center_x)
                        break  # found face with this cascade, move to next frame

        cap.release()

        if not face_positions:
            return None

        # Cluster face positions to find the dominant speaker.
        # When two speakers are visible, positions will form two groups.
        # Pick the largest cluster (most frequently detected face).
        crop_w = int(height * target_ratio)
        cluster_radius = crop_w * 0.25  # positions within 25% of crop width = same face

        positions = np.array(face_positions)

        # Find all distinct clusters
        clusters = []
        used = np.zeros(len(positions), dtype=bool)
        sorted_idx = np.argsort(positions)

        for idx in sorted_idx:
            if used[idx]:
                continue
            mask = np.abs(positions - positions[idx]) < cluster_radius
            mask &= ~used
            cluster = positions[mask]
            if len(cluster) > 0:
                clusters.append(cluster)
                used |= mask

        if not clusters:
            clusters = [positions]

        # Sort clusters by size (largest first)
        clusters.sort(key=lambda c: len(c), reverse=True)

        # Always pick the largest cluster (most frequently detected face).
        # This ensures we lock onto the dominant speaker rather than
        # splitting the crop between two equally-visible speakers.
        best_cluster = clusters[0]

        avg_face_x = int(np.median(best_cluster))

        # Calculate crop window centered on face
        crop_x = avg_face_x - crop_w // 2

        # Clamp to valid range
        crop_x = max(0, min(crop_x, width - crop_w))

        return crop_x

    except ImportError:
        # OpenCV not installed, fall back to center
        return None
    except Exception:
        return None
