"""End-to-end render check: synthetic video in, real clip out.

CI only ever ran unit tests, so a change that broke the render pipeline (a
Remotion runtime mismatch, a bad ffmpeg flag, a caption regression) could pass
every check and still ship. This drives the pipeline the product actually runs
and asserts the artifact is real.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend"))

CLIP_START = 2.0
CLIP_END = 8.0
EXPECTED_DURATION = CLIP_END - CLIP_START
DURATION_TOLERANCE = 1.5  # outro/intro-free clip, but encoders round frame counts

WORDS = [
    {"word": "the", "start": 2.1, "end": 2.35},
    {"word": "secret", "start": 2.35, "end": 2.8},
    {"word": "to", "start": 2.8, "end": 2.95},
    {"word": "growth", "start": 2.95, "end": 3.5},
    {"word": "is", "start": 4.2, "end": 4.4},
    {"word": "boring", "start": 4.4, "end": 4.9},
    {"word": "work", "start": 4.9, "end": 5.4},
    {"word": "done", "start": 5.6, "end": 6.0},
    {"word": "every", "start": 6.0, "end": 6.4},
    {"word": "single", "start": 6.4, "end": 6.9},
    {"word": "day", "start": 6.9, "end": 7.4},
]


def ffprobe(path: str) -> dict:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", path],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(out.stdout)


def make_source(path: str) -> None:
    subprocess.run(
        [
            "ffmpeg", "-y", "-v", "error",
            "-f", "lavfi", "-i", "testsrc=size=1280x720:rate=30:duration=12",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=12",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-shortest", path,
        ],
        check=True,
        capture_output=True,
    )


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  ok    {name}")
        return
    print(f"  FAIL  {name}{': ' + detail if detail else ''}")
    sys.exit(1)


def main() -> int:
    style = sys.argv[1] if len(sys.argv) > 1 else "branded"
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        print("ffmpeg/ffprobe not on PATH")
        return 1

    from services.clip_generator import generate_clip

    work = tempfile.mkdtemp(prefix="podcli-e2e-")
    try:
        source = os.path.join(work, "source.mp4")
        out_dir = os.path.join(work, "out")
        os.makedirs(out_dir)

        print(f"[{style}] building synthetic source")
        make_source(source)

        print(f"[{style}] rendering clip {CLIP_START}s to {CLIP_END}s")
        result = generate_clip(
            video_path=source,
            start_second=CLIP_START,
            end_second=CLIP_END,
            caption_style=style,
            crop_strategy="center",
            format="vertical",
            transcript_words=WORDS,
            title=f"e2e {style}",
            output_dir=out_dir,
            clean_fillers=False,
        )

        path = result.get("output_path") or result.get("path")
        check("pipeline reported success", bool(path), json.dumps(result)[:300])
        check("output file exists", os.path.exists(path), str(path))
        check("output is not empty", os.path.getsize(path) > 20_000, f"{os.path.getsize(path)} bytes")

        probe = ffprobe(path)
        streams = {s["codec_type"]: s for s in probe["streams"]}
        check("has a video stream", "video" in streams)
        check("has an audio stream", "audio" in streams)

        duration = float(probe["format"]["duration"])
        check(
            "duration matches the requested clip",
            abs(duration - EXPECTED_DURATION) <= DURATION_TOLERANCE,
            f"expected ~{EXPECTED_DURATION}s, got {duration:.2f}s",
        )

        width = int(streams["video"]["width"])
        height = int(streams["video"]["height"])
        check("is vertical", height > width, f"{width}x{height}")

        print(f"[{style}] {width}x{height}, {duration:.2f}s, {os.path.getsize(path) // 1024}KB")
        return 0
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
