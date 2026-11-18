import argparse
import importlib.util
import os
import sys
import time

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "backend"))

from services import transcription as tr


def bench_whispercpp(path: str, model: str) -> tuple[float, int]:
    started = time.perf_counter()
    result = tr._transcribe_with_whispercpp(path, model, None, None)
    return time.perf_counter() - started, len(result.get("words") or [])


def prepare_mlx(model: str) -> tuple[str, float]:
    from huggingface_hub import snapshot_download

    started = time.perf_counter()
    local = snapshot_download(f"mlx-community/whisper-{model}-mlx")
    return local, time.perf_counter() - started


def bench_mlx(path: str, model_dir: str) -> tuple[float, int]:
    import mlx_whisper

    started = time.perf_counter()
    result = mlx_whisper.transcribe(path, path_or_hf_repo=model_dir, word_timestamps=True)
    words = sum(len(seg.get("words") or []) for seg in result.get("segments") or [])
    return time.perf_counter() - started, words


def main() -> int:
    parser = argparse.ArgumentParser(description="Time whisper.cpp and mlx-whisper on one file.")
    parser.add_argument("file")
    parser.add_argument("--model", default="small")
    args = parser.parse_args()

    if not os.path.exists(args.file):
        print(f"File not found: {args.file}", file=sys.stderr)
        return 1

    print(f"file: {args.file}")
    print(f"model: {args.model}")
    print(f"whisper.cpp threads: {tr._whisper_threads()}")

    rows = []
    if tr._whispercpp_ready(args.model):
        rows.append(("whisper.cpp", *bench_whispercpp(args.file, args.model)))
    else:
        print(f"whisper.cpp skipped: binary or ggml-{args.model}.bin not found")

    if importlib.util.find_spec("mlx_whisper") is not None:
        model_dir, prepare_seconds = prepare_mlx(args.model)
        print(f"mlx-whisper model ready in {prepare_seconds:.1f}s (not counted below)")
        rows.append(("mlx-whisper", *bench_mlx(args.file, model_dir)))
    else:
        print("mlx-whisper skipped: pip install mlx-whisper to include it")

    for name, seconds, words in rows:
        print(f"{name:<12} {seconds:8.1f}s  {words} words")

    times = dict((name, seconds) for name, seconds, _ in rows)
    if "whisper.cpp" in times and "mlx-whisper" in times:
        print(f"mlx speedup: {times['whisper.cpp'] / times['mlx-whisper']:.2f}x")
    return 0


if __name__ == "__main__":
    sys.exit(main())
