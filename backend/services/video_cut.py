"""Video cutting helpers — extract time ranges and stitch them together.

Extracted from video_processor.py. These are the two entry points used
by the clip generator to slice the source video before any cropping or
caption rendering.
"""

from __future__ import annotations

import os

from services.media_probe import FFMPEG_TIMEOUT, get_media_duration_seconds
from utils.proc import run as proc_run

# One frame at 24fps: a keyframe this close to the requested boundary keeps
# caption/audio sync within a frame, so stream copy is safe.
KEYFRAME_SNAP_TOLERANCE = 0.042


def _has_keyframe_near(input_path: str, t: float, tolerance: float = KEYFRAME_SNAP_TOLERANCE) -> bool:
    """Probe whether a video keyframe lands within tolerance of time t."""
    lo = max(0.0, t - 0.5)
    cmd = [
        "ffprobe", "-v", "error",
        "-select_streams", "v:0",
        "-skip_frame", "nokey",
        "-show_entries", "frame=pts_time",
        "-of", "csv=p=0",
        "-read_intervals", f"{lo:.3f}%{t + 0.5:.3f}",
        input_path,
    ]
    try:
        result = proc_run(cmd, timeout=30, check=False)
    except Exception:
        return False
    if result.returncode != 0:
        return False
    for line in (result.stdout or "").splitlines():
        try:
            pts = float(line.strip().rstrip(","))
        except ValueError:
            continue
        if abs(pts - t) <= tolerance:
            return True
    return False


def _try_stream_copy_cut(
    input_path: str,
    output_path: str,
    start_second: float,
    duration: float,
) -> bool:
    cmd = [
        "ffmpeg", "-y",
        "-ss", str(start_second),
        "-i", input_path,
        "-t", str(duration),
        "-c", "copy",
        "-avoid_negative_ts", "make_zero",
        output_path,
    ]
    result = proc_run(cmd, timeout=FFMPEG_TIMEOUT, check=False)
    if result.returncode != 0 or not os.path.exists(output_path):
        return False
    got = get_media_duration_seconds(output_path, default=0.0)
    return abs(got - duration) <= 0.5


def cut_segment(
    input_path: str,
    output_path: str,
    start_second: float,
    end_second: float,
    allow_stream_copy: bool = True,
) -> str:
    """Extract a single time segment from a video file.

    When both boundaries land on keyframes the segment is stream-copied —
    no generation loss and no encode time. Otherwise it re-encodes with
    `-ss` before `-i` for frame-accurate timestamps. CRF 16 keeps this
    intermediate visually lossless through the later crop/caption/audio
    re-encodes; the fast preset holds the speed cost to a few percent
    over the old CRF 18 pass.
    """
    duration = end_second - start_second

    if allow_stream_copy and _has_keyframe_near(input_path, start_second) and _has_keyframe_near(input_path, end_second):
        if _try_stream_copy_cut(input_path, output_path, start_second, duration):
            return output_path
        if os.path.exists(output_path):
            os.remove(output_path)

    cmd = [
        "ffmpeg", "-y",
        "-ss", str(start_second),
        "-i", input_path,
        "-t", str(duration),
        "-c:v", "libx264", "-crf", "16", "-preset", "fast", "-profile:v", "high",
        "-c:a", "aac", "-b:a", "192k",
        "-avoid_negative_ts", "make_zero",
        output_path,
    ]
    result = proc_run(cmd, timeout=FFMPEG_TIMEOUT, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"FFmpeg cut failed: {result.stderr[-500:]}")
    return output_path


def cut_multi_segment(
    input_path: str,
    output_path: str,
    segments: list[dict],
) -> str:
    """Cut multiple time ranges and concatenate them seamlessly.

    segments: [{"start": 10.5, "end": 25.0}, {"start": 30.2, "end": 45.0}]

    Each segment is cut individually with frame-accurate encoding,
    then concatenated with stream copy (matching codecs means no
    re-encode needed).
    """
    if len(segments) == 1:
        return cut_segment(
            input_path, output_path, segments[0]["start"], segments[0]["end"]
        )

    work_dir = os.path.dirname(output_path) or "."
    part_paths: list[str] = []
    concat_file = os.path.join(work_dir, "_concat_parts.txt")

    try:
        for i, seg in enumerate(segments):
            part_path = os.path.join(work_dir, f"_part_{i}.mp4")
            # No stream copy here: the parts are concatenated with -c copy,
            # which needs identical codec parameters across every part.
            cut_segment(input_path, part_path, seg["start"], seg["end"], allow_stream_copy=False)
            part_paths.append(part_path)

        with open(concat_file, "w", encoding="utf-8") as f:
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
        result = proc_run(cmd, timeout=FFMPEG_TIMEOUT, check=False)
        if result.returncode != 0:
            raise RuntimeError(f"FFmpeg concat failed: {result.stderr[-500:]}")

        return output_path

    finally:
        for p in part_paths:
            if os.path.exists(p):
                os.remove(p)
        if os.path.exists(concat_file):
            os.remove(concat_file)
