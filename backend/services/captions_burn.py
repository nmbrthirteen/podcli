"""Burn ASS captions + optional gradient overlay + logo into a video.

Extracted from video_processor.py. Takes a rendered clip and a pre-
generated ASS file and produces the final captioned video.

All ffmpeg calls route through utils.proc.run for timeout + logging.
"""

from __future__ import annotations

import os
import sys
from typing import Optional

from services.encoder import get_video_encode_flags
from services.media_probe import (
    CPU_FLAGS,
    FFMPEG_TIMEOUT,
    get_dimensions,
    has_audio_stream,
)
from utils.proc import run as proc_run


def create_gradient_png(
    output_path: str,
    width: int = 1080,
    height: int = 1920,
    opacity: float = 0.7,
) -> str:
    """Create a transparent-to-black gradient PNG for the bottom 50%.

    Uses ffmpeg's lavfi source + geq filter so we don't need Pillow
    for this specific asset. The top half is fully transparent; the
    bottom half fades from 0 to `opacity`.
    """
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
    result = proc_run(cmd, timeout=FFMPEG_TIMEOUT, check=False)
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
    """Burn ASS subtitles into the video.

    Optionally adds:
    - Bottom 50% smooth gradient overlay (transparent → black)
    - Logo image in top-left corner
    """
    safe_ass = ass_path.replace("\\", "/").replace(":", "\\:")

    gradient_path: Optional[str] = None
    if gradient_overlay:
        width, height = get_dimensions(input_path)
        gradient_path = output_path + ".gradient.png"
        create_gradient_png(gradient_path, width, height, gradient_opacity)

    inputs = ["-i", input_path]
    input_idx = 1
    filter_parts: list[str] = []

    if gradient_overlay and gradient_path:
        inputs.extend(["-i", gradient_path])
        grad_idx = input_idx
        input_idx += 1
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
        filter_parts.append(f"[{logo_idx}:v]scale=-1:{logo_height}[logo]")
        filter_parts.append(
            f"[{current_label}][logo]overlay={logo_margin_x}:{logo_margin_y}[withlogo]"
        )
        current_label = "withlogo"

    # Burn ASS subtitles
    filter_parts.append(f"[{current_label}]ass='{safe_ass}'[out]")
    filter_complex = ";".join(filter_parts)

    # Some split-screen concat outputs may not have audio
    has_audio = has_audio_stream(input_path)
    audio_map = ["-map", "0:a?", "-c:a", "copy"] if has_audio else []

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

    result = proc_run(cmd, timeout=FFMPEG_TIMEOUT, check=False)

    # Fallback to CPU if HW encoder failed
    if result.returncode != 0 and enc_flags != CPU_FLAGS:
        print(
            "Warning: HW encoder failed for caption burn, falling back to libx264",
            file=sys.stderr,
        )
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
        result = proc_run(cmd_fallback, timeout=FFMPEG_TIMEOUT, check=False)

    if gradient_overlay and gradient_path and os.path.exists(gradient_path):
        os.remove(gradient_path)

    if result.returncode != 0:
        raise RuntimeError(f"FFmpeg caption burn failed: {result.stderr[-500:]}")
    return output_path
