"""Two-pass audio loudness normalization via ffmpeg loudnorm filter.

Extracted from video_processor.py. Takes a rendered clip and brings
its audio to the target LUFS (−14 by default, which is the TikTok /
YouTube Shorts standard).
"""

from __future__ import annotations

import json

from services.media_probe import FFMPEG_TIMEOUT
from utils.proc import run as proc_run


def normalize_audio(
    input_path: str,
    output_path: str,
    target_lufs: float = -14.0,
) -> str:
    """Normalize audio to target LUFS.

    Uses a two-pass loudnorm filter when possible: first pass measures
    the clip's loudness stats, second pass applies linear correction.
    Falls back to a single-pass loudnorm when measurement is missing
    or returns −inf (silent / very short clips).
    """
    # First pass: measure current loudness.
    measure_cmd = [
        "ffmpeg", "-y",
        "-i", input_path,
        "-af", f"loudnorm=I={target_lufs}:TP=-1.5:LRA=11:print_format=json",
        "-f", "null", "-",
    ]
    result = proc_run(measure_cmd, timeout=FFMPEG_TIMEOUT, check=False)

    loudnorm_data = _parse_loudnorm_stats(result.stderr)

    if loudnorm_data:
        # Second pass: apply measured correction.
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
        # Single-pass fallback.
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
    result = proc_run(cmd, timeout=FFMPEG_TIMEOUT, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"FFmpeg normalize failed: {result.stderr[-500:]}")
    return output_path


def _parse_loudnorm_stats(stderr: str) -> dict | None:
    """Extract the loudnorm JSON block from ffmpeg stderr.

    Returns None if the block is missing, unparseable, or contains
    -inf input_i (which signals silence / a too-short clip where the
    two-pass correction isn't meaningful).
    """
    if not stderr:
        return None
    try:
        json_start = stderr.rfind("{")
        json_end = stderr.rfind("}") + 1
        if json_start < 0 or json_end <= json_start:
            return None
        data = json.loads(stderr[json_start:json_end])
    except (json.JSONDecodeError, ValueError):
        return None

    measured_i = str(data.get("input_i", ""))
    if "inf" in measured_i.lower() or measured_i == "":
        return None
    return data
