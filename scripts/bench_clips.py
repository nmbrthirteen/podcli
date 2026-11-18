import argparse
import csv
import json
import os
import re
import subprocess
import sys
import time

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BACKEND = os.path.join(ROOT, "backend")
sys.path.insert(0, BACKEND)

STAGE_RE = re.compile(r"\[([1-4])/4\]")
STAGE_NAMES = {"1": "transcribe", "2": "audio", "3": "select", "4": "render"}
CLIP_FIELDS = [
    "clip_id", "format", "start", "end", "duration", "title", "crop_strategy",
    "engine", "model", "transcribe_s", "audio_s", "select_s", "render_s", "total_s",
]
SCORE_FIELDS = [
    "clip_id", "file", "start_clean", "end_clean", "hook", "standalone",
    "captions", "framing", "post_as_is", "notes",
]


def stage_times(marks: list[tuple[str, float]], finished: float) -> dict[str, float]:
    times = {name: 0.0 for name in STAGE_NAMES.values()}
    for i, (stage, started) in enumerate(marks):
        ended = marks[i + 1][1] if i + 1 < len(marks) else finished
        times[STAGE_NAMES[stage]] += ended - started
    return {k: round(v, 1) for k, v in times.items()}


def transcript_info(video: str) -> tuple[str, str]:
    from config.paths import paths
    from services.transcript_packer import compute_cache_hash

    cache_dir = paths["transcripts"]
    cache_hash = compute_cache_hash(video)
    try:
        names = [n for n in os.listdir(cache_dir) if n.startswith(cache_hash) and n.endswith(".json")]
    except FileNotFoundError:
        return "", ""
    if not names:
        return "", ""
    newest = max(names, key=lambda n: os.path.getmtime(os.path.join(cache_dir, n)))
    with open(os.path.join(cache_dir, newest), encoding="utf-8") as f:
        data = json.load(f)
    model_match = re.search(r"-m([a-z0-9]+)", newest[len(cache_hash):])
    model = data.get("model_size") or (model_match.group(1) if model_match else "base")
    return data.get("engine") or "whisper-py", model


def run_format(video: str, fmt: str, out_dir: str, top: int) -> tuple[dict, dict[str, float], float]:
    cmd = [
        sys.executable, os.path.join(BACKEND, "cli.py"), "process", video,
        "--format", fmt, "--output", out_dir, "--top", str(top),
        "--no-resume", "--json", "--yes",
    ]
    marks: list[tuple[str, float]] = []
    started = time.perf_counter()
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1)
    assert proc.stderr is not None
    for line in proc.stderr:
        sys.stderr.write(line)
        match = STAGE_RE.search(line)
        if match and (not marks or marks[-1][0] != match.group(1)):
            marks.append((match.group(1), time.perf_counter()))
    stdout = proc.stdout.read() if proc.stdout else ""
    proc.wait()
    finished = time.perf_counter()
    if proc.returncode != 0:
        raise SystemExit(f"podcli process failed for format {fmt} (exit {proc.returncode})")
    payload = json.loads(stdout.strip().splitlines()[-1])
    return payload, stage_times(marks, finished), round(finished - started, 1)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run podcli on one episode and write benchmark CSVs.")
    parser.add_argument("video")
    parser.add_argument("output_dir")
    parser.add_argument("--formats", default="vertical,topic")
    parser.add_argument("--top", type=int, default=5)
    args = parser.parse_args()

    video = os.path.abspath(args.video)
    if not os.path.exists(video):
        print(f"File not found: {video}", file=sys.stderr)
        return 1
    os.makedirs(args.output_dir, exist_ok=True)

    clip_rows, score_rows = [], []
    for fmt in [f.strip() for f in args.formats.split(",") if f.strip()]:
        fmt_dir = os.path.join(os.path.abspath(args.output_dir), fmt)
        payload, times, total = run_format(video, fmt, fmt_dir, args.top)
        engine, model = transcript_info(video)
        for clip in payload.get("clips", []):
            clip_id = f"{fmt}-{clip.get('index')}"
            clip_rows.append({
                "clip_id": clip_id,
                "format": fmt,
                "start": clip.get("start_second"),
                "end": clip.get("end_second"),
                "duration": clip.get("output_duration") or clip.get("duration"),
                "title": clip.get("title"),
                "crop_strategy": clip.get("crop_strategy", ""),
                "engine": engine,
                "model": model,
                "transcribe_s": times["transcribe"],
                "audio_s": times["audio"],
                "select_s": times["select"],
                "render_s": times["render"],
                "total_s": total,
            })
            score_rows.append({"clip_id": clip_id, "file": clip.get("path", "")})

    clips_csv = os.path.join(args.output_dir, "clips.csv")
    scores_csv = os.path.join(args.output_dir, "scores.csv")
    with open(clips_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CLIP_FIELDS)
        writer.writeheader()
        writer.writerows(clip_rows)
    with open(scores_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=SCORE_FIELDS)
        writer.writeheader()
        writer.writerows(score_rows)
    print(f"{len(clip_rows)} clips. Wrote {clips_csv} and {scores_csv}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
