"""End-to-end render check: synthetic video in, real clip out.

CI only ever ran unit tests, so a change that broke the render pipeline (a
Remotion runtime mismatch, a bad ffmpeg flag, a caption regression) could pass
every check and still ship. This drives the pipeline the product actually runs
and asserts the artifact is real.

Captions are proven by rendering the same clip twice, with and without words,
and diffing the pixels: probing the container cannot tell a captioned clip from
an uncaptioned one.
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

ARTIFACT_DIR = ROOT / "e2e-artifacts"

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

# Clip-relative seconds, each one mid-word so some chunk is always on screen.
SAMPLE_TIMES = [0.6, 2.6, 5.0]

# All four styles anchor captions to the bottom of the frame (margins 200-420px
# on a 1920-tall canvas), and nothing is ever drawn in the top half.
CAPTION_BAND = (0.55, 1.0)
CONTROL_BAND = (0.0, 0.45)

PIXEL_DELTA = 40  # a caption stroke moves a pixel far further than a re-encode does
MIN_CAPTION_COVERAGE = 0.004
MIN_SIGNAL_RATIO = 5.0


class CheckFailed(Exception):
    pass


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


def extract_frame(video: str, second: float, path: str) -> None:
    subprocess.run(
        [
            "ffmpeg", "-y", "-v", "error",
            "-ss", str(second), "-i", video, "-frames:v", "1", path,
        ],
        check=True,
        capture_output=True,
    )


def band(frame, bounds: tuple[float, float]):
    top, bottom = bounds
    height = frame.shape[0]
    return frame[int(height * top):int(height * bottom)]


def changed_fractions(captioned: str, plain: str) -> tuple[float, float]:
    import numpy as np
    from PIL import Image

    a = np.asarray(Image.open(captioned).convert("L"), dtype=np.int16)
    b = np.asarray(Image.open(plain).convert("L"), dtype=np.int16)
    if a.shape != b.shape:
        raise CheckFailed(f"frames differ in size: {a.shape} vs {b.shape}")

    delta = np.abs(a - b)
    return (
        float((band(delta, CAPTION_BAND) > PIXEL_DELTA).mean()),
        float((band(delta, CONTROL_BAND) > PIXEL_DELTA).mean()),
    )


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  ok    {name}")
        return
    raise CheckFailed(f"{name}{': ' + detail if detail else ''}")


def keep_artifacts(style: str, work: str) -> None:
    dest = ARTIFACT_DIR / style
    dest.mkdir(parents=True, exist_ok=True)
    for found in Path(work).rglob("*"):
        if found.suffix in (".mp4", ".png") and found.is_file():
            flattened = "-".join(found.relative_to(work).parts)
            shutil.copy2(found, dest / flattened)
    print(f"  saved failure artifacts to {dest}")


def render(style: str, source: str, out_dir: str, words: list) -> str:
    from services.clip_generator import generate_clip

    os.makedirs(out_dir, exist_ok=True)
    result = generate_clip(
        video_path=source,
        start_second=CLIP_START,
        end_second=CLIP_END,
        caption_style=style,
        crop_strategy="speaker",
        format="vertical",
        transcript_words=words,
        title=f"e2e {style}",
        output_dir=out_dir,
        clean_fillers=False,
    )
    path = result.get("output_path") or result.get("path")
    check("pipeline reported success", bool(path), json.dumps(result)[:300])
    check("output file exists", os.path.exists(path), str(path))
    return path


def run(style: str, work: str) -> None:
    source = os.path.join(work, "source.mp4")

    print(f"[{style}] building synthetic source")
    make_source(source)

    print(f"[{style}] rendering clip {CLIP_START}s to {CLIP_END}s")
    path = render(style, source, os.path.join(work, "captioned"), WORDS)
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

    print(f"[{style}] re-rendering the same clip with no words, to diff against")
    plain = render(style, source, os.path.join(work, "plain"), [])

    coverage = []
    noise = []
    for i, second in enumerate(SAMPLE_TIMES):
        a = os.path.join(work, "captioned", f"frame-{i}.png")
        b = os.path.join(work, "plain", f"frame-{i}.png")
        extract_frame(path, second, a)
        extract_frame(plain, second, b)
        captioned_frac, control_frac = changed_fractions(a, b)
        coverage.append(captioned_frac)
        noise.append(control_frac)
        print(f"  t={second}s  caption band {captioned_frac:.3%}  control band {control_frac:.3%}")

    check(
        "captions are burned into every sampled frame",
        min(coverage) >= MIN_CAPTION_COVERAGE,
        f"lowest caption-band coverage was {min(coverage):.3%}, needs {MIN_CAPTION_COVERAGE:.3%}",
    )
    check(
        "the caption band changed, not the whole frame",
        min(coverage) >= MIN_SIGNAL_RATIO * max(noise),
        f"caption band {min(coverage):.3%} vs control band {max(noise):.3%}",
    )

    print(f"[{style}] {width}x{height}, {duration:.2f}s, {os.path.getsize(path) // 1024}KB")


def main() -> int:
    style = sys.argv[1] if len(sys.argv) > 1 else "branded"
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        print("ffmpeg/ffprobe not on PATH")
        return 1

    work = tempfile.mkdtemp(prefix="podcli-e2e-")
    try:
        run(style, work)
        return 0
    except CheckFailed as failure:
        print(f"  FAIL  {failure}")
        keep_artifacts(style, work)
        return 1
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
