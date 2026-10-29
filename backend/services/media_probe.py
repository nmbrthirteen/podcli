"""FFmpeg / ffprobe utility helpers.

Extracted from video_processor.py so small probing functions can be
reused (and unit-tested) without pulling in the 3600+ lines of the
rendering pipeline. The rendering module re-exports these to keep the
historical import surface stable.

All functions here route subprocess calls through utils.proc.run so
they inherit the mandatory timeout + structured logging.
"""

from __future__ import annotations

import json
import math
import os
import sys
from typing import Optional

from services.encoder import get_video_encode_flags
from utils.proc import run as proc_run

# Max time for a short probe. Encode timeouts scale with duration via
# ffmpeg_timeout(); a 40s 1080x1920 libx264 slower pass on CPU can exceed 300s.
FFMPEG_TIMEOUT = 300

# Quality presets: name → (crf, preset) for libx264.
# Lower CRF = higher quality = larger file. 18 is visually lossless.
QUALITY_PRESETS = {
    "low":    {"crf": "28", "preset": "fast"},       # ~2-4 MB/min, fast encode
    "medium": {"crf": "23", "preset": "medium"},     # ~4-8 MB/min, balanced
    "high":   {"crf": "18", "preset": "slow"},       # ~8-15 MB/min, great quality
    "max":    {"crf": "14", "preset": "slower"},     # ~15-30 MB/min, near-lossless
}


def cpu_flags() -> list:
    """libx264 flags for the current PODCLI_QUALITY. Read at call time so a
    `process --quality` set after import is what actually encodes."""
    quality = os.environ.get("PODCLI_QUALITY", "high")
    qp = QUALITY_PRESETS.get(quality, QUALITY_PRESETS["high"])
    return [
        "-c:v", "libx264",
        "-crf", qp["crf"],
        "-preset", qp["preset"],
        "-profile:v", "high",
        "-pix_fmt", "yuv420p",
    ]


CPU_FLAGS = cpu_flags()


def ffmpeg_timeout(duration_s: float | None = None) -> float:
    """Seconds allowed for one encode. Probes keep using FFMPEG_TIMEOUT."""
    base = float(FFMPEG_TIMEOUT)
    if duration_s and duration_s > 0:
        return min(1800.0, max(base, duration_s * 20.0 + 120.0))
    return base


def run_ffmpeg_with_fallback(
    cmd_parts_before_enc: list,
    cmd_parts_after_enc: list,
    output_path: str,
    label: str = "encode",
    duration_s: float | None = None,
) -> str:
    """Run an FFmpeg command with the best encoder; retry libx264 on failure.

    Final command shape:
        cmd_parts_before_enc + <encoder_flags> + cmd_parts_after_enc + [output_path]

    If the preferred hardware encoder fails, the function automatically
    retries with libx264 before raising.
    """
    enc_flags = get_video_encode_flags()
    fallback_flags = cpu_flags()
    cmd = cmd_parts_before_enc + enc_flags + cmd_parts_after_enc + [output_path]
    timeout = ffmpeg_timeout(duration_s)

    result = proc_run(cmd, timeout=timeout, check=False)
    if result.returncode == 0:
        return output_path

    if enc_flags != fallback_flags:
        print(
            f"Warning: HW encoder failed for {label}, falling back to libx264",
            file=sys.stderr,
        )
        cmd_fallback = cmd_parts_before_enc + fallback_flags + cmd_parts_after_enc + [output_path]
        result2 = proc_run(cmd_fallback, timeout=timeout, check=False)
        if result2.returncode == 0:
            return output_path
        raise RuntimeError(
            f"FFmpeg {label} failed (both HW and CPU): {result2.stderr[-500:]}"
        )

    raise RuntimeError(f"FFmpeg {label} failed: {result.stderr[-500:]}")


def get_video_info(video_path: str) -> dict:
    """Probe a media file and return the full ffprobe JSON payload."""
    cmd = [
        "ffprobe",
        "-v", "quiet",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        video_path,
    ]
    result = proc_run(cmd, timeout=FFMPEG_TIMEOUT, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {result.stderr}")
    return json.loads(result.stdout)


def has_audio_stream(video_path: str) -> bool:
    """Return True if the file has any audio stream (best-effort)."""
    try:
        info = get_video_info(video_path)
        return any(s.get("codec_type") == "audio" for s in info.get("streams", []))
    except Exception:
        # Assume yes if we can't check — missing audio is reasonably rare
        # and downstream concat filters will surface the real error.
        return True


def parse_duration_seconds(value: object) -> Optional[float]:
    """Parse a duration-like value, rejecting non-finite / non-positive numbers."""
    try:
        parsed = float(str(value).strip())
    except Exception:
        return None
    if not math.isfinite(parsed) or parsed <= 0:
        return None
    return parsed


def get_media_duration_seconds(video_path: str, default: float = 0.0) -> float:
    """Best-effort media duration in seconds.

    Prefers the max valid duration from the format block and per-stream
    durations; falls back to `default` if nothing parseable is found.
    """
    try:
        info = get_video_info(video_path)
    except Exception:
        return default

    candidates: list[float] = []
    fmt_duration = parse_duration_seconds(info.get("format", {}).get("duration"))
    if fmt_duration is not None:
        candidates.append(fmt_duration)

    for stream in info.get("streams", []):
        stream_duration = parse_duration_seconds(stream.get("duration"))
        if stream_duration is not None:
            candidates.append(stream_duration)

    return max(candidates) if candidates else default


def get_dimensions(video_path: str) -> tuple[int, int]:
    """Return the (width, height) of the first video stream."""
    info = get_video_info(video_path)
    for stream in info.get("streams", []):
        if stream.get("codec_type") == "video":
            return int(stream["width"]), int(stream["height"])
    raise ValueError(f"No video stream found in {video_path}")


def stream_durations(video_path: str) -> tuple[float | None, float | None]:
    """Return (video_stream_duration, audio_stream_duration) in seconds."""
    try:
        info = get_video_info(video_path)
    except Exception:
        return None, None
    video_d = None
    audio_d = None
    for stream in info.get("streams", []):
        dur = parse_duration_seconds(stream.get("duration"))
        if dur is None:
            continue
        kind = stream.get("codec_type")
        if kind == "video" and video_d is None:
            video_d = dur
        elif kind == "audio" and audio_d is None:
            audio_d = dur
    return video_d, audio_d


def align_av_duration(path: str, slack: float = 0.2) -> str:
    """Trim a frozen video tail (or pad a short picture) so A/V durations match.

    Thumbnail prepend and a timed-out re-encode can leave video longer than
    audio. Trim to the shorter stream; keep the original if they already agree
    or if the rewrite fails.
    """
    video_d, audio_d = stream_durations(path)
    if video_d is None or audio_d is None:
        return path
    gap = abs(video_d - audio_d)
    if gap <= slack:
        return path
    target = min(video_d, audio_d)
    tmp = path + ".avalign.mp4"
    cmd = [
        "ffmpeg", "-y",
        "-i", path,
        "-t", f"{target:.3f}",
        "-c:v", "copy",
        "-c:a", "copy",
        "-movflags", "+faststart",
        tmp,
    ]
    try:
        result = proc_run(cmd, timeout=FFMPEG_TIMEOUT, check=False)
        if result.returncode == 0 and os.path.exists(tmp) and os.path.getsize(tmp) > 0:
            os.replace(tmp, path)
            return path
    except Exception:
        pass
    if os.path.exists(tmp):
        try:
            os.remove(tmp)
        except OSError:
            pass
    return path
