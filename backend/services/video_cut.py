"""Video cutting helpers — extract time ranges and stitch them together.

Extracted from video_processor.py. These are the two entry points used
by the clip generator to slice the source video before any cropping or
caption rendering.
"""

from __future__ import annotations

import os

from services.media_probe import FFMPEG_TIMEOUT
from utils.proc import run as proc_run


def cut_segment(
    input_path: str,
    output_path: str,
    start_second: float,
    end_second: float,
) -> str:
    """Extract a single time segment from a video file.

    Uses `-ss` before `-i` with re-encoding for frame-accurate
    timestamps. The fast preset + CRF 18 is a deliberate quality
    trade-off: we'll re-encode again in the final pipeline, so this
    pass prioritizes speed.
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
            cut_segment(input_path, part_path, seg["start"], seg["end"])
            part_paths.append(part_path)

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
