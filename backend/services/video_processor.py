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

CPU_FLAGS = ["-c:v", "libx264", "-crf", "23", "-preset", "medium"]


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
    Uses -ss before -i for fast seeking.
    """
    duration = end_second - start_second

    cmd = [
        "ffmpeg", "-y",
        "-ss", str(start_second),
        "-i", input_path,
        "-t", str(duration),
        "-c", "copy",  # No re-encoding for speed
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
    strategy: str = "center",
) -> str:
    """
    Crop/scale video to 1080x1920 (9:16 vertical).

    Strategies:
    - center: Take center column of the frame, scale to fit
    - face: Detect face position, center crop on face (falls back to center)
    """
    width, height = get_dimensions(input_path)
    target_w, target_h = 1080, 1920
    target_ratio = target_w / target_h  # 0.5625

    source_ratio = width / height

    if strategy == "face":
        crop_x = _detect_face_offset(input_path, width, height, target_ratio)
        if crop_x is not None:
            crop_h = height
            crop_w = int(crop_h * target_ratio)
            vf = f"crop={crop_w}:{crop_h}:{crop_x}:0,scale={target_w}:{target_h}"
        else:
            # Fallback to center
            strategy = "center"

    if strategy == "center":
        if source_ratio > target_ratio:
            # Source is wider than target — crop sides
            crop_h = height
            crop_w = int(crop_h * target_ratio)
            crop_x = (width - crop_w) // 2
            vf = f"crop={crop_w}:{crop_h}:{crop_x}:0,scale={target_w}:{target_h}"
        else:
            # Source is taller or same — crop top/bottom or pad
            crop_w = width
            crop_h = int(crop_w / target_ratio)
            if crop_h > height:
                # Need to pad (letterbox)
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
            "-b:a", "128k",
            "-ar", "44100",
            "-movflags", "+faststart",
        ],
        output_path=output_path,
        label="crop",
    )


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
    Detect face position in first few seconds and return crop x-offset.
    Returns None if no face detected (caller should fall back to center).
    """
    try:
        import cv2
        import numpy as np

        face_cascade = cv2.CascadeClassifier(
            cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
        )

        cap = cv2.VideoCapture(video_path)
        face_positions = []

        # Sample ~30 frames from first 5 seconds
        for _ in range(30):
            ret, frame = cap.read()
            if not ret:
                break

            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            faces = face_cascade.detectMultiScale(gray, 1.3, 5, minSize=(50, 50))

            if len(faces) > 0:
                # Get largest face
                largest = max(faces, key=lambda f: f[2] * f[3])
                face_center_x = largest[0] + largest[2] // 2
                face_positions.append(face_center_x)

        cap.release()

        if not face_positions:
            return None

        # Average face center x position
        avg_face_x = int(np.mean(face_positions))

        # Calculate crop window centered on face
        crop_w = int(height * target_ratio)
        crop_x = avg_face_x - crop_w // 2

        # Clamp to valid range
        crop_x = max(0, min(crop_x, width - crop_w))

        return crop_x

    except ImportError:
        # OpenCV not installed, fall back to center
        return None
    except Exception:
        return None
