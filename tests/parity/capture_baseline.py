"""Layer-2 engine baseline capture.

Runs the CURRENT transcription engine (openai-whisper) on real-audio fixtures
and freezes its output as ground truth. When the engine is later swapped to
whisper.cpp, `compare.py` measures the candidate against this baseline with
explicit tolerances.

The baseline is the transcript JSON contract (`words` + `segments`) plus a
metrics summary and the captions rendered from those real words — i.e. exactly
the things whisper.cpp must reproduce.

Usage:
    venv/bin/python3 tests/parity/capture_baseline.py FIXTURE [FIXTURE ...]
    venv/bin/python3 tests/parity/capture_baseline.py            # scans tests/parity/local/

Outputs (gitignored) under tests/parity/baseline/<stem>/:
    transcript.json      full {words, segments, duration, language}
    metrics.json         n_words, n_segments, duration, language, engine, model
    captions_<style>.ass captions rendered from the real transcript words

Capture this WHILE the current system still works — it is the frozen reference
everything else is measured against.
"""

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
BACKEND_ROOT = os.path.join(ROOT, "backend")
if BACKEND_ROOT not in sys.path:
    sys.path.insert(0, BACKEND_ROOT)

LOCAL_DIR = os.path.join(HERE, "local")
BASELINE_DIR = os.path.join(HERE, "baseline")
MEDIA_EXTS = (".mp4", ".mov", ".mkv", ".wav", ".mp3", ".m4a", ".aac")
STYLES = ["hormozi", "karaoke", "subtle", "branded"]
MODEL = os.environ.get("PARITY_MODEL", "base")


def _discover():
    if not os.path.isdir(LOCAL_DIR):
        return []
    return [
        os.path.join(LOCAL_DIR, f)
        for f in sorted(os.listdir(LOCAL_DIR))
        if f.lower().endswith(MEDIA_EXTS)
    ]


def capture(media_path: str):
    from services.transcription import transcribe_file
    from services.caption_renderer import render_captions

    stem = os.path.splitext(os.path.basename(media_path))[0]
    out_dir = os.path.join(BASELINE_DIR, stem)
    os.makedirs(out_dir, exist_ok=True)

    print(f"  transcribing {os.path.basename(media_path)} (model={MODEL}) ...")
    result = transcribe_file(
        file_path=media_path,
        model_size=MODEL,
        enable_diarization=False,  # deterministic; diarization is optional/off by default
    )
    words = result.get("words", [])
    segments = result.get("segments", [])

    with open(os.path.join(out_dir, "transcript.json"), "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    metrics = {
        "engine": "openai-whisper",
        "model": MODEL,
        "n_words": len(words),
        "n_segments": len(segments),
        "duration": result.get("duration"),
        "language": result.get("language"),
    }
    with open(os.path.join(out_dir, "metrics.json"), "w", encoding="utf-8") as f:
        json.dump(metrics, f, indent=2)

    for style in STYLES:
        render_captions(words, style, os.path.join(out_dir, f"captions_{style}.ass"))

    print(f"  -> {out_dir}  ({len(words)} words, {len(segments)} segments)")
    return metrics


def main(argv):
    fixtures = argv[1:] or _discover()
    if not fixtures:
        print(
            "No fixtures. Pass media paths, or drop clips into tests/parity/local/ .\n"
            "Use short representative clips (single speaker, two speakers, music-heavy, "
            "fast speech). They stay local — never committed.",
            file=sys.stderr,
        )
        return 1
    for media in fixtures:
        if not os.path.exists(media):
            print(f"  skip (not found): {media}", file=sys.stderr)
            continue
        capture(media)
    print("\nBaseline captured. Compare a new engine later with tests/parity/compare.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
