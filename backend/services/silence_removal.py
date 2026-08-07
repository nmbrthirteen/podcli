"""Local full-episode silence analysis and rendering.

Silero VAD (MIT, https://github.com/snakers4/silero-vad) finds speech without
uploading media. Transcript word ranges are
unioned with VAD output before cuts are planned, so known words are never cut.
The derived video and remapped transcript keep every downstream Podcli feature
on one compact timeline while the original source remains untouched.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import urllib.request
import uuid
import wave
from pathlib import Path
from typing import Callable, Iterable, Optional

from config.paths import paths
from services.audio_extract import extract_wav_16k_mono
from services.media_probe import get_media_duration_seconds, has_audio_stream
from utils.proc import run as proc_run

try:
    import numpy as np
    import onnxruntime as ort
    _VAD_RUNTIME_AVAILABLE = True
except ImportError:
    _VAD_RUNTIME_AVAILABLE = False


ProgressCallback = Optional[Callable[[int, str], None]]

SILERO_MODEL_URL = (
    "https://raw.githubusercontent.com/snakers4/silero-vad/"
    "76e3dc408eb2a5c655c34e230d2d5459b4439daa/"
    "src/silero_vad/data/silero_vad_16k_op15.onnx"
)
SILERO_MODEL_SHA256 = "7ed98ddbad84ccac4cd0aeb3099049280713df825c610a8ed34543318f1b2c49"
SILERO_MODEL_FILENAME = "silero_vad_16k_op15.onnx"
SAMPLE_RATE = 16_000
WINDOW_SAMPLES = 512
CONTEXT_SAMPLES = 64


def _emit(callback: ProgressCallback, percent: int, message: str) -> None:
    if callback:
        callback(max(0, min(100, int(percent))), message)


def _model_path() -> Path:
    return Path(paths["cache"]) / "models" / SILERO_MODEL_FILENAME


def _sha256(file_path: Path) -> str:
    digest = hashlib.sha256()
    with file_path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def ensure_silero_model(progress_callback: ProgressCallback = None) -> Path:
    """Return verified model path, downloading the 1.3 MB model once if needed."""
    model_path = _model_path()
    if model_path.exists() and _sha256(model_path) == SILERO_MODEL_SHA256:
        return model_path

    model_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = model_path.with_name(f".{model_path.name}.{uuid.uuid4().hex}.download")
    _emit(progress_callback, 2, "Downloading local speech detector (first use only)")
    try:
        request = urllib.request.Request(
            SILERO_MODEL_URL,
            headers={"User-Agent": "podcli-silence-removal"},
        )
        with urllib.request.urlopen(request, timeout=60) as response, temp_path.open("wb") as target:
            shutil.copyfileobj(response, target)
        if _sha256(temp_path) != SILERO_MODEL_SHA256:
            raise RuntimeError("Silero VAD model checksum mismatch")
        os.replace(temp_path, model_path)
    finally:
        try:
            temp_path.unlink()
        except OSError:
            pass
    return model_path


def _merge_segments(
    segments: Iterable[dict],
    duration: float,
    merge_gap: float = 0.0,
) -> list[dict]:
    cleaned: list[dict] = []
    for raw in segments:
        try:
            start = max(0.0, min(duration, float(raw["start"])))
            end = max(0.0, min(duration, float(raw["end"])))
        except (KeyError, TypeError, ValueError):
            continue
        if end - start < 0.02:
            continue
        cleaned.append({"start": start, "end": end})
    cleaned.sort(key=lambda item: (item["start"], item["end"]))

    merged: list[dict] = []
    for segment in cleaned:
        if merged and segment["start"] <= merged[-1]["end"] + merge_gap:
            merged[-1]["end"] = max(merged[-1]["end"], segment["end"])
        else:
            merged.append(dict(segment))
    return merged


def probabilities_to_speech_segments(
    probabilities: list[float],
    audio_samples: int,
    *,
    threshold: float = 0.5,
    min_speech_ms: int = 250,
    min_silence_ms: int = 180,
) -> list[dict]:
    """Convert Silero probabilities into unpadded speech ranges."""
    negative_threshold = max(0.01, threshold - 0.15)
    min_speech_samples = SAMPLE_RATE * min_speech_ms / 1000
    min_silence_samples = SAMPLE_RATE * min_silence_ms / 1000
    triggered = False
    temporary_end = 0
    current_start = 0
    speech: list[dict] = []

    for index, probability in enumerate(probabilities):
        current_sample = index * WINDOW_SAMPLES
        if probability >= threshold:
            if not triggered:
                triggered = True
                current_start = current_sample
            temporary_end = 0
            continue

        if triggered and probability < negative_threshold:
            if not temporary_end:
                temporary_end = current_sample
            if current_sample - temporary_end >= min_silence_samples:
                if temporary_end - current_start >= min_speech_samples:
                    speech.append({
                        "start": current_start / SAMPLE_RATE,
                        "end": temporary_end / SAMPLE_RATE,
                    })
                triggered = False
                temporary_end = 0

    if triggered and audio_samples - current_start >= min_speech_samples:
        speech.append({
            "start": current_start / SAMPLE_RATE,
            "end": audio_samples / SAMPLE_RATE,
        })
    return speech


def detect_speech(
    video_path: str,
    *,
    threshold: float = 0.5,
    progress_callback: ProgressCallback = None,
) -> list[dict]:
    if not _VAD_RUNTIME_AVAILABLE:
        raise RuntimeError("Local silence detection requires numpy and onnxruntime")

    model_path = ensure_silero_model(progress_callback)
    _emit(progress_callback, 5, "Extracting episode audio")
    wav_path = extract_wav_16k_mono(video_path)
    try:
        session = ort.InferenceSession(str(model_path), providers=["CPUExecutionProvider"])
        state = np.zeros((2, 1, 128), dtype=np.float32)
        context = np.zeros((1, CONTEXT_SAMPLES), dtype=np.float32)
        probabilities: list[float] = []

        with wave.open(wav_path, "rb") as audio:
            if audio.getframerate() != SAMPLE_RATE or audio.getnchannels() != 1 or audio.getsampwidth() != 2:
                raise RuntimeError("Extracted audio is not 16 kHz mono PCM")
            total_samples = audio.getnframes()
            processed = 0
            last_percent = -1
            while True:
                frames = audio.readframes(WINDOW_SAMPLES)
                if not frames:
                    break
                chunk = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
                actual_samples = chunk.size
                if actual_samples < WINDOW_SAMPLES:
                    chunk = np.pad(chunk, (0, WINDOW_SAMPLES - actual_samples))
                model_input = np.concatenate((context, chunk.reshape(1, -1)), axis=1)
                output, state = session.run(None, {
                    "input": model_input,
                    "state": state,
                    "sr": np.array(SAMPLE_RATE, dtype=np.int64),
                })
                probabilities.append(float(output[0][0]))
                context = model_input[:, -CONTEXT_SAMPLES:]
                processed += actual_samples
                percent = 10 + int((processed / max(1, total_samples)) * 55)
                if percent >= last_percent + 3:
                    last_percent = percent
                    _emit(progress_callback, percent, "Finding spoken sections")

        return probabilities_to_speech_segments(probabilities, total_samples, threshold=threshold)
    finally:
        try:
            os.unlink(wav_path)
        except OSError:
            pass


def plan_silence_removal(
    duration: float,
    vad_segments: list[dict],
    transcript_words: list[dict],
    *,
    min_silence_seconds: float = 0.65,
    padding_seconds: float = 0.12,
) -> dict:
    """Build conservative keep ranges from VAD plus transcript word timings."""
    duration = max(0.0, float(duration))
    min_silence_seconds = max(0.2, min(5.0, float(min_silence_seconds)))
    padding_seconds = max(0.0, min(0.5, float(padding_seconds)))
    if duration <= 0:
        raise ValueError("Video duration must be positive")

    protected: list[dict] = []
    for segment in vad_segments:
        protected.append({
            "start": float(segment.get("start", 0)) - padding_seconds,
            "end": float(segment.get("end", 0)) + padding_seconds,
        })
    for word in transcript_words:
        try:
            protected.append({
                "start": float(word["start"]) - padding_seconds,
                "end": float(word["end"]) + padding_seconds,
            })
        except (KeyError, TypeError, ValueError):
            continue

    keep_segments = _merge_segments(protected, duration, merge_gap=min_silence_seconds)
    if not keep_segments:
        keep_segments = [{"start": 0.0, "end": duration}]

    removed_ranges: list[dict] = []
    cursor = 0.0
    for segment in keep_segments:
        if segment["start"] - cursor >= min_silence_seconds:
            removed_ranges.append({"start": cursor, "end": segment["start"]})
        elif cursor < segment["start"]:
            segment["start"] = cursor
        cursor = segment["end"]
    if duration - cursor >= min_silence_seconds:
        removed_ranges.append({"start": cursor, "end": duration})
    elif cursor < duration:
        keep_segments[-1]["end"] = duration

    # Rebuild exact keep ranges as the complement of accepted removals. This
    # prevents short leading/interstitial gaps from disappearing accidentally.
    keep_segments = []
    cursor = 0.0
    for removed in removed_ranges:
        if removed["start"] > cursor:
            keep_segments.append({"start": cursor, "end": removed["start"]})
        cursor = removed["end"]
    if cursor < duration:
        keep_segments.append({"start": cursor, "end": duration})
    if not keep_segments:
        keep_segments = [{"start": 0.0, "end": duration}]

    keep_segments = [
        {"start": round(item["start"], 3), "end": round(item["end"], 3)}
        for item in keep_segments if item["end"] - item["start"] >= 0.04
    ]
    removed_ranges = [
        {"start": round(item["start"], 3), "end": round(item["end"], 3)}
        for item in removed_ranges
    ]
    output_duration = sum(item["end"] - item["start"] for item in keep_segments)
    removed_duration = max(0.0, duration - output_duration)
    return {
        "keep_segments": keep_segments,
        "removed_ranges": removed_ranges,
        "source_duration": round(duration, 3),
        "output_duration": round(output_duration, 3),
        "removed_duration": round(removed_duration, 3),
        "removed_percent": round((removed_duration / duration) * 100, 1),
        "cut_count": len(removed_ranges),
        "min_silence_seconds": min_silence_seconds,
        "padding_seconds": padding_seconds,
        "method": "silero-vad+word-boundaries",
    }


def analyze_silence(
    video_path: str,
    transcript_words: list[dict],
    *,
    threshold: float = 0.5,
    min_silence_seconds: float = 0.65,
    padding_seconds: float = 0.12,
    progress_callback: ProgressCallback = None,
) -> dict:
    duration = get_media_duration_seconds(video_path)
    if duration <= 0:
        raise RuntimeError("Could not determine episode duration")
    speech = detect_speech(video_path, threshold=threshold, progress_callback=progress_callback)
    _emit(progress_callback, 75, "Protecting word boundaries")
    plan = plan_silence_removal(
        duration,
        speech,
        transcript_words,
        min_silence_seconds=min_silence_seconds,
        padding_seconds=padding_seconds,
    )
    plan["vad_threshold"] = threshold
    _emit(progress_callback, 100, "Silence analysis ready")
    return plan


def _map_range(start: float, end: float, keep_segments: list[dict]) -> Optional[tuple[float, float]]:
    output_cursor = 0.0
    mapped_parts: list[tuple[float, float]] = []
    for segment in keep_segments:
        overlap_start = max(start, segment["start"])
        overlap_end = min(end, segment["end"])
        if overlap_end > overlap_start:
            mapped_parts.append((
                output_cursor + overlap_start - segment["start"],
                output_cursor + overlap_end - segment["start"],
            ))
        output_cursor += segment["end"] - segment["start"]
    if not mapped_parts:
        return None
    return mapped_parts[0][0], mapped_parts[-1][1]


def remap_timed_items(items: list[dict], keep_segments: list[dict]) -> list[dict]:
    remapped: list[dict] = []
    for item in items:
        try:
            start = float(item["start"])
            end = float(item["end"])
        except (KeyError, TypeError, ValueError):
            continue
        mapped = _map_range(start, end, keep_segments)
        if not mapped or mapped[1] - mapped[0] < 0.01:
            continue
        remapped.append({**item, "start": round(mapped[0], 3), "end": round(mapped[1], 3)})
    return remapped


def remap_transcript(transcript: dict, keep_segments: list[dict]) -> dict:
    remapped = dict(transcript or {})
    remapped["words"] = remap_timed_items(list(remapped.get("words") or []), keep_segments)
    remapped["segments"] = remap_timed_items(list(remapped.get("segments") or []), keep_segments)
    remapped["duration"] = round(sum(s["end"] - s["start"] for s in keep_segments), 3)
    remapped["silence_removed"] = True
    return remapped


def _reserve_output_path(video_path: str, output_dir: str) -> Path:
    stem = Path(video_path).stem
    for suffix in range(1, 10_000):
        name = f"{stem}_silence_removed_podcli.mp4" if suffix == 1 else f"{stem}_silence_removed_podcli-{suffix}.mp4"
        candidate = Path(output_dir) / name
        if not candidate.exists():
            return candidate
    raise RuntimeError("Could not reserve silence-removed output filename")


def _render_batch(
    video_path: str,
    output_path: str,
    segments: list[dict],
    audio: bool,
) -> None:
    filters: list[str] = []
    concat_inputs: list[str] = []
    for index, segment in enumerate(segments):
        start = segment["start"]
        end = segment["end"]
        filters.append(f"[0:v:0]trim=start={start:.3f}:end={end:.3f},setpts=PTS-STARTPTS[v{index}]")
        concat_inputs.append(f"[v{index}]")
        if audio:
            filters.append(f"[0:a:0]atrim=start={start:.3f}:end={end:.3f},asetpts=PTS-STARTPTS[a{index}]")
            concat_inputs.append(f"[a{index}]")
    filters.append(
        "".join(concat_inputs)
        + f"concat=n={len(segments)}:v=1:a={1 if audio else 0}[vout]"
        + ("[aout]" if audio else "")
    )
    command = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", video_path,
        "-filter_complex", ";".join(filters),
        "-map", "[vout]",
    ]
    if audio:
        command += ["-map", "[aout]"]
    command += [
        "-c:v", "libx264", "-crf", "18", "-preset", "fast", "-profile:v", "high",
        "-pix_fmt", "yuv420p",
    ]
    if audio:
        command += ["-c:a", "aac", "-b:a", "192k"]
    command += ["-movflags", "+faststart", output_path]
    proc_run(command, timeout=3600, check=True)


def render_silence_removed(
    video_path: str,
    keep_segments: list[dict],
    transcript: dict,
    output_dir: str,
    *,
    progress_callback: ProgressCallback = None,
) -> dict:
    if not os.path.exists(video_path):
        raise FileNotFoundError(f"Video not found: {video_path}")
    duration = get_media_duration_seconds(video_path)
    normalized = _merge_segments(keep_segments, duration)
    if not normalized:
        raise ValueError("No valid speech segments to render")

    Path(output_dir).mkdir(parents=True, exist_ok=True)
    output_path = _reserve_output_path(video_path, output_dir)
    work_dir = Path(tempfile.mkdtemp(prefix="podcli_silence_", dir=paths["working"] if os.path.isdir(paths["working"]) else None))
    batch_size = 80
    chunks: list[Path] = []
    audio = has_audio_stream(video_path)
    try:
        batches = [normalized[index:index + batch_size] for index in range(0, len(normalized), batch_size)]
        for index, batch in enumerate(batches):
            _emit(progress_callback, 5 + int((index / len(batches)) * 85), f"Building compact episode {index + 1}/{len(batches)}")
            chunk_path = work_dir / f"chunk-{index:04d}.mp4"
            _render_batch(video_path, str(chunk_path), batch, audio)
            chunks.append(chunk_path)

        partial = work_dir / "finished.mp4"
        if len(chunks) == 1:
            shutil.copy2(chunks[0], partial)
        else:
            concat_path = work_dir / "chunks.txt"
            concat_path.write_text("".join(f"file '{chunk.as_posix()}'\n" for chunk in chunks), encoding="utf-8")
            proc_run([
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                "-f", "concat", "-safe", "0", "-i", str(concat_path),
                "-c", "copy", "-movflags", "+faststart", str(partial),
            ], timeout=1800, check=True)
        os.replace(partial, output_path)
        _emit(progress_callback, 96, "Remapping captions and clips")
        remapped = remap_transcript(transcript, normalized)
        manifest = output_path.with_suffix(".silence.json")
        manifest.write_text(json.dumps({
            "source_video": os.path.abspath(video_path),
            "output_video": str(output_path),
            "keep_segments": normalized,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        _emit(progress_callback, 100, "Compact episode ready")
        stat = output_path.stat()
        return {
            "output_path": str(output_path),
            "filename": output_path.name,
            "file_size_mb": round(stat.st_size / (1024 * 1024), 2),
            "duration": remapped["duration"],
            "transcript": remapped,
            "manifest_path": str(manifest),
        }
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)
