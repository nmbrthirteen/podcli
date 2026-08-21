"""Episodes that were never filmed.

A podcast recorded as audio has no frame to crop, no face to follow and nothing
to burn captions onto. Everything downstream of the transcript assumes
otherwise: `get_dimensions` raises "No video stream found". It raises it after
transcription, so an hour of audio pays for its full Whisper run to be told that
it was never going to produce a clip, and the message it gets reads like a bug
in the tool rather than a fact about the file.

The header knew all along. This is the part that reads it.
"""

import os
from typing import Optional

import numpy as np

from services.media_probe import get_video_info
from utils.proc import run as proc_run


def _moving_picture(streams: list) -> bool:
    """Whether any stream is something you could actually crop."""
    for stream in streams or []:
        if stream.get("codec_type") != "video":
            continue
        # Cover art in an mp3 is a video stream by ffprobe's reckoning, and a
        # single still is not something to crop or follow a face around.
        if (stream.get("disposition") or {}).get("attached_pic"):
            continue
        if str(stream.get("avg_frame_rate") or "0/0").split("/")[0] in ("0", ""):
            continue
        return True
    return False


def is_audio_only(path: str) -> bool:
    """Positively sound, positively no picture.

    Deliberately stricter than "no video found", because this one is allowed to
    stop a run. A file ffprobe cannot read at all is not evidence of anything:
    it might be a stub, a truncated download, or a container this build has no
    demuxer for. Refusing those with a message about audio would swap one
    unhelpful failure for a differently unhelpful one, so anything short of "I
    read this, it has sound, and it has no moving picture" is left to the path
    it has always taken.
    """
    try:
        info = get_video_info(path)
    except Exception:
        return False
    streams = info.get("streams") or []
    if not streams:
        return False
    if not any(stream.get("codec_type") == "audio" for stream in streams):
        return False
    return not _moving_picture(streams)


def cover_art(path: str) -> bool:
    """Whether the file carries embedded artwork worth drawing behind the bars."""
    try:
        info = get_video_info(path)
    except Exception:
        return False
    return any(
        (stream.get("disposition") or {}).get("attached_pic")
        for stream in info.get("streams", []) or []
    )


def extract_cover(path: str, out_path: str) -> Optional[str]:
    """Pull the embedded artwork out, or return None if there is none."""
    if not cover_art(path):
        return None
    result = proc_run(
        [os.environ.get("PODCLI_FFMPEG", "ffmpeg"), "-y", "-i", path,
         "-an", "-vcodec", "copy", out_path, "-loglevel", "error"],
        timeout=60, check=False,
    )
    return out_path if result.returncode == 0 and os.path.exists(out_path) else None


def envelope(
    audio_path: str,
    start: float,
    end: float,
    fps: int = 30,
    bars: int = 48,
    wav_path: Optional[str] = None,
) -> list[list[float]]:
    """Loudness per bar per frame, for the window between start and end.

    One row per rendered frame, each row `bars` values in 0..1. Computed here
    rather than in the browser because these samples are already being read on
    this side for moment detection, and shipping an hour of PCM into a Remotion
    render to have it averaged there would be the same arithmetic somewhere
    slower and harder to test.

    Root mean square rather than peak: peaks make every bar full height on any
    voice that clips, and the whole point of the bars is that they move.
    """
    from services.audio_events import _read_waveform_16k_mono

    if end <= start or fps <= 0 or bars <= 0:
        return []

    samples = _read_waveform_16k_mono(audio_path, wav_path=wav_path)
    if samples is None or samples.size == 0:
        return []

    rate = 16_000
    first = max(0, int(start * rate))
    last = min(samples.size, int(end * rate))
    if last <= first:
        return []

    window = samples[first:last]
    frames = max(1, int(round((end - start) * fps)))
    per_frame = max(1, window.size // frames)

    # Trimmed to a whole number of frames and bars so the reshape is exact; the
    # remainder is a fraction of one frame at the tail.
    per_bar = max(1, per_frame // bars)
    usable = frames * bars * per_bar
    if usable > window.size:
        frames = max(1, window.size // (bars * per_bar))
        usable = frames * bars * per_bar

    block = window[:usable].reshape(frames, bars, per_bar)
    rms = np.sqrt(np.mean(np.square(block, dtype=np.float64), axis=2))

    # Scaled against the loudest bar in this window rather than against full
    # scale, so a quietly recorded episode still moves.
    peak = float(rms.max())
    if peak <= 0:
        return [[0.0] * bars for _ in range(frames)]
    scaled = np.clip(rms / peak, 0.0, 1.0)
    return [[round(float(v), 4) for v in row] for row in scaled]


def render_audiogram(
    audio_path: str,
    start_second: float,
    end_second: float,
    caption_style: str,
    spec,
    transcript_words: Optional[list] = None,
    title: str = "clip",
    output_dir: Optional[str] = None,
    fps: int = 30,
    bars: int = 48,
    progress_callback=None,
) -> dict:
    """One clip from an episode with no picture.

    Returns what generate_clip returns, because it is called in its place and
    nothing above it should have to know which road the file took.
    """
    import json
    import shutil
    import tempfile

    def say(percent, message):
        if progress_callback:
            progress_callback(percent, message)

    out_dir = output_dir or os.path.join(os.getcwd(), "output")
    os.makedirs(out_dir, exist_ok=True)
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in title)[:60] or "clip"
    final_path = os.path.join(out_dir, f"{safe}.mp4")

    say(10, "Reading the waveform")
    levels = envelope(audio_path, start_second, end_second, fps=fps, bars=bars)
    if not levels:
        raise ValueError(f"No audio to draw between {start_second}s and {end_second}s")

    # Word timings are absolute in the episode; the render starts at zero.
    words = [
        {
            "word": w.get("word", ""),
            "start": max(0.0, float(w.get("start", 0)) - start_second),
            "end": max(0.0, float(w.get("end", 0)) - start_second),
        }
        for w in (transcript_words or [])
        if start_second <= float(w.get("start", 0)) < end_second
    ]

    work = tempfile.mkdtemp(prefix="audiogram_")
    try:
        cover = extract_cover(audio_path, os.path.join(work, "cover.jpg"))
        props_path = os.path.join(work, "props.json")
        with open(props_path, "w", encoding="utf-8") as fh:
            json.dump({
                "words": words,
                "levels": levels,
                "styleName": caption_style,
                "bg": "#0B0B0F",
                "accent": "#FFE000",
                "coverSrc": cover,
                "title": title if title != "clip" else None,
            }, fh)

        say(35, "Drawing the waveform")
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        script = os.path.join(os.path.dirname(root), "remotion", "render-audiogram.mjs")
        result = proc_run(
            [os.environ.get("PODCLI_NODE", "node"), script,
             "--props", props_path, "--audio", audio_path,
             "--start", str(start_second), "--end", str(end_second),
             "--output", final_path,
             "--fps", str(fps), "--width", str(spec.width), "--height", str(spec.height)],
            timeout=1800, check=False,
        )
        if result.returncode != 0 or not os.path.exists(final_path):
            tail = (getattr(result, "stderr", "") or "")[-600:]
            raise RuntimeError(f"Audiogram render failed:\n{tail}")
    finally:
        shutil.rmtree(work, ignore_errors=True)

    say(100, "Done")
    size_mb = round(os.path.getsize(final_path) / (1024 * 1024), 2)
    return {
        "output_path": final_path,
        "duration": round(end_second - start_second, 2),
        "file_size_mb": size_mb,
        "title": title,
        "start_second": start_second,
        "end_second": end_second,
        "caption_style": caption_style,
        "crop_strategy": "audiogram",
        "format": spec.name,
    }


def audio_only_message(path: str) -> str:
    """What to tell somebody whose file has no picture in it."""
    return (
        f"{os.path.basename(path)} has no video track, so there is nothing to crop "
        "or burn captions onto. Clips are made from video; pass the recording that "
        "has the picture in it."
    )
