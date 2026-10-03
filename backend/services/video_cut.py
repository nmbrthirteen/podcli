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

    Always re-encodes with `-ss` before `-i` for frame-accurate timestamps.
    Stream copy is not an option here: with `-c copy` ffmpeg can only start at
    the keyframe at-or-before the cut point, so any boundary off the GOP grid
    silently emits up to a full GOP of earlier content while the duration still
    checks out, which offsets every burned-in caption. CRF 16 keeps this
    intermediate visually lossless through the later crop/caption/audio
    re-encodes; the fast preset holds the speed cost to a few percent
    over the old CRF 18 pass.
    """
    duration = end_second - start_second

    cmd = [
        "ffmpeg", "-y",
        "-ss", str(start_second),
        "-i", input_path,
        "-t", str(duration),
        "-c:v", "libx264", "-crf", "16", "-preset", "fast", "-profile:v", "high",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k",
        "-avoid_negative_ts", "make_zero",
        output_path,
    ]
    result = proc_run(cmd, timeout=FFMPEG_TIMEOUT, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"FFmpeg cut failed: {result.stderr[-500:]}")
    return output_path


def probe_duration(path: str) -> float:
    """Read media duration in seconds (best effort, 0.0 if unreadable)."""
    try:
        cmd = [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=nk=1:nw=1",
            path,
        ]
        result = proc_run(cmd, timeout=10, check=False)
        if result.returncode != 0:
            return 0.0
        return float((result.stdout or "0").strip() or 0.0)
    except Exception:
        return 0.0


def cut_multi_segment(
    input_path: str,
    output_path: str,
    segments: list[dict],
) -> tuple[str, list[float]]:
    """Cut multiple time ranges and concatenate them seamlessly.

    segments: [{"start": 10.5, "end": 25.0}, {"start": 30.2, "end": 45.0}]

    Each segment is cut individually with frame-accurate encoding, then
    concatenated with stream copy (matching codecs means no re-encode
    needed). Returns (output_path, part_durations): the probed duration of
    each encoded part, not the requested one. An encoder snaps a cut to
    whole frames, so the actual part is typically a few milliseconds off
    the requested end - start; captions timed from the requested length
    instead of the probed one drift further with every cut concatenated in.
    """
    if len(segments) == 1:
        out = cut_segment(
            input_path, output_path, segments[0]["start"], segments[0]["end"]
        )
        return out, [probe_duration(out)]

    work_dir = os.path.dirname(output_path) or "."
    part_paths: list[str] = []
    concat_file = os.path.join(work_dir, "_concat_parts.txt")

    try:
        part_durations: list[float] = []
        for i, seg in enumerate(segments):
            part_path = os.path.join(work_dir, f"_part_{i}.mp4")
            cut_segment(input_path, part_path, seg["start"], seg["end"])
            part_paths.append(part_path)
            part_durations.append(probe_duration(part_path))

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

        return output_path, part_durations

    finally:
        for p in part_paths:
            if os.path.exists(p):
                os.remove(p)
        if os.path.exists(concat_file):
            os.remove(concat_file)


def probe_has_audio_stream(path: str) -> bool:
    """True if ffprobe finds at least one audio stream in the file."""
    cmd = [
        "ffprobe", "-v", "error",
        "-select_streams", "a",
        "-show_entries", "stream=codec_type",
        "-of", "csv=p=0",
        path,
    ]
    result = proc_run(cmd, timeout=FFMPEG_TIMEOUT, check=False)
    if result.returncode != 0:
        return False
    return "audio" in (result.stdout or "")


def verify_full_decode(path: str, max_error_lines: int = 3) -> str | None:
    """Decode the whole file and return ffmpeg's error output, or None if clean.

    An ffmpeg render that exits 0 can still have written a truncated or
    corrupt file: a moov atom cut short, a partial frame at the tail, a
    stream copy/concat mismatch. Those only surface on a full decode, which
    is what this runs: the same check as `ffmpeg -v error -i x -f null -`
    from the command line. -threads auto keeps that decode from running
    single-threaded on a multi-core box.

    A nonzero exit always fails. Otherwise, `-v error` also logs a handful
    of lines ffmpeg can emit on an otherwise-fine file (e.g. a non-monotonic
    DTS warning from a concat/re-encode), a single clip used to get deleted
    for one such line even though it decoded and played fine. Only treat it
    as a real decode failure once more than a few such lines show up.
    """
    cmd = ["ffmpeg", "-v", "error", "-threads", "auto", "-i", path, "-f", "null", "-"]
    result = proc_run(cmd, timeout=FFMPEG_TIMEOUT, check=False)
    stderr = (result.stderr or "").strip()
    if result.returncode != 0:
        return stderr[-1000:] if stderr else f"ffmpeg exited {result.returncode} decoding the output"
    if not stderr:
        return None
    error_lines = [line for line in stderr.splitlines() if line.strip()]
    if len(error_lines) > max_error_lines:
        return stderr[-1000:]
    return None
