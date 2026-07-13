"""
Audio-event detection (laughter, cheering, applause, screaming) for reaction-based
clip scoring.

Runs YAMNet (AudioSet, 521 classes) as a self-contained ONNX graph via onnxruntime,
which imports neither torch nor tensorflow — so this belongs on the native /
whisper.cpp runtime path. Audio is extracted at 16 kHz mono (the same format
whisper.cpp uses) and fed to the graph as a raw waveform; the log-mel front-end is
baked into the model, so there is no librosa dependency.

A laugh is the strongest language-independent "something funny happened" signal, and
it anti-correlates with speech at the frame level, which makes it a reliable anchor
for extending a clip backwards to the moment that caused the reaction.
"""

import csv
import os
import subprocess
import tempfile
import wave
from pathlib import Path
from typing import Optional, Callable

import numpy as np

from utils.proc import run as proc_run

try:
    import onnxruntime as ort
    _ORT_AVAILABLE = True
except ImportError:
    _ORT_AVAILABLE = False

_MODELS_DIR = Path(__file__).resolve().parents[1] / "models"
_MODEL_PATH = _MODELS_DIR / "yamnet.onnx"
_CLASS_MAP_PATH = _MODELS_DIR / "yamnet_class_map.csv"

# AudioSet display names, grouped into the reaction channels we score on. Laughter is
# spread across fine-grained children in the ontology, so we collapse the whole family.
_LAUGHTER_KEYS = ("laugh", "giggle", "chuckle", "snicker", "chortle")
_CHEER_NAMES = ("Cheering", "Applause", "Whoop")
_SCREAM_NAMES = ("Screaming",)
_SPEECH_NAMES = ("Speech",)

_session = None
_class_index: dict[str, list[int]] = {}


def _load_class_index() -> dict[str, list[int]]:
    names: dict[int, str] = {}
    with open(_CLASS_MAP_PATH, newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        next(reader)  # header: index,mid,display_name
        for row in reader:
            names[int(row[0])] = row[2]

    def by_substr(keys) -> list[int]:
        return [i for i, n in names.items() if any(k in n.lower() for k in keys)]

    def by_name(wanted) -> list[int]:
        return [i for i, n in names.items() if n in wanted]

    return {
        "laughter": by_substr(_LAUGHTER_KEYS),
        "cheering": by_name(_CHEER_NAMES),
        "screaming": by_name(_SCREAM_NAMES),
        "speech": by_name(_SPEECH_NAMES),
    }


def _get_session():
    global _session, _class_index
    if not _ORT_AVAILABLE or not _MODEL_PATH.exists():
        return None
    if _session is None:
        _session = ort.InferenceSession(
            str(_MODEL_PATH), providers=["CPUExecutionProvider"]
        )
        _class_index = _load_class_index()
    return _session


def is_available() -> bool:
    """True if onnxruntime and the YAMNet model are both present."""
    return _ORT_AVAILABLE and _MODEL_PATH.exists()


def _read_waveform_16k_mono(video_path: str, wav_path: Optional[str] = None) -> Optional[np.ndarray]:
    """Extract audio as a float32 [-1, 1] mono waveform at 16 kHz via ffmpeg.

    wav_path: optional pre-extracted 16 kHz mono WAV — read directly, no decode.
    """
    if wav_path and os.path.exists(wav_path):
        try:
            with wave.open(wav_path) as w:
                frames = w.readframes(w.getnframes())
        except (wave.Error, OSError):
            return None
        if not frames:
            return None
        return np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0

    # delete=False + manual unlink: on Windows the open NamedTemporaryFile
    # handle blocks ffmpeg from writing to the same path.
    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    tmp.close()
    try:
        cmd = [
            "ffmpeg", "-y", "-i", video_path,
            "-vn", "-ar", "16000", "-ac", "1",
            "-f", "wav", tmp.name, "-loglevel", "error",
        ]
        result = proc_run(cmd, timeout=600, check=False)
        if result.returncode != 0:
            return None
        with wave.open(tmp.name) as w:
            frames = w.readframes(w.getnframes())
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass
    if not frames:
        return None
    return np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0


def extract_audio_events(video_path: str, wav_path: Optional[str] = None) -> list[dict]:
    """
    Run YAMNet over a video's audio and return per-frame reaction probabilities.

    Returns a list of {time, laughter, cheering, screaming, speech} dicts, one per
    YAMNet frame (~0.48 s hop). Empty list if the model/runtime is unavailable or the
    audio can't be read, so callers degrade gracefully.
    """
    session = _get_session()
    if session is None:
        return []

    waveform = _read_waveform_16k_mono(video_path, wav_path=wav_path)
    if waveform is None or waveform.size == 0:
        return []

    input_name = session.get_inputs()[0].name

    # Windowed so one multi-hour session.run() can't block the worker unboundedly.
    window = 16000 * 300
    parts: list[np.ndarray] = []
    times: list[float] = []
    for offset in range(0, waveform.size, window):
        chunk = waveform[offset:offset + window]
        if chunk.size < 16000:
            continue
        chunk_scores = session.run(None, {input_name: chunk})[0]  # [frames, 521]
        if chunk_scores.shape[0] == 0:
            continue
        hop = (chunk.size / 16000.0) / chunk_scores.shape[0]
        base = offset / 16000.0
        times.extend(base + f * hop for f in range(chunk_scores.shape[0]))
        parts.append(chunk_scores)

    if not parts:
        return []
    scores = np.concatenate(parts, axis=0)
    n_frames = scores.shape[0]

    def channel_max(keys: list[int]) -> np.ndarray:
        return scores[:, keys].max(axis=1) if keys else np.zeros(n_frames)

    laughter = channel_max(_class_index["laughter"])
    cheering = channel_max(_class_index["cheering"])
    screaming = channel_max(_class_index["screaming"])
    speech = channel_max(_class_index["speech"])

    return [
        {
            "time": round(times[i], 2),
            "laughter": round(float(laughter[i]), 3),
            "cheering": round(float(cheering[i]), 3),
            "screaming": round(float(screaming[i]), 3),
            "speech": round(float(speech[i]), 3),
        }
        for i in range(n_frames)
    ]


def _reaction_level(event: dict) -> float:
    """Combined reaction strength for one frame: the loudest reaction channel."""
    return max(event["laughter"], event["cheering"], event["screaming"])


def compute_event_scores(
    events_data: list[dict],
    segments: list[dict],
) -> list[float]:
    """
    Per-segment reaction score (0-10) from the peak reaction within each segment.

    Unlike audio energy (RMS in dB, only meaningful relative to a video's own
    baseline), YAMNet probabilities are absolute-calibrated in [0, 1], so the score is
    a direct scaling of the peak rather than a z-score. A brief chuckle registers
    ~0.25, a hearty laugh higher; peaks stand out sharply against a ~0 baseline.
    """
    if not events_data:
        return [0.0] * len(segments)

    scores = []
    for seg in segments:
        seg_start = seg.get("start", 0)
        seg_end = seg.get("end", 0)
        levels = [
            _reaction_level(e)
            for e in events_data
            if seg_start <= e["time"] <= seg_end
        ]
        peak = max(levels) if levels else 0.0
        scores.append(round(min(10.0, peak * 12.0), 2))

    return scores


def get_event_profile(
    video_path: str,
    segments: list[dict],
    progress_callback: Optional[Callable] = None,
    reaction_threshold: float = 0.15,
    wav_path: Optional[str] = None,
) -> dict:
    """
    Full pipeline: detect audio events and score all segments.

    Returns {events_data, segment_scores, reaction_times} where reaction_times are the
    timestamps of frames whose reaction level clears reaction_threshold — the anchors a
    reaction-based detector expands backwards from.
    """
    if not is_available():
        return {"events_data": [], "segment_scores": [0.0] * len(segments), "reaction_times": []}

    if progress_callback:
        progress_callback(0, "Detecting laughter and reactions...")

    events_data = extract_audio_events(video_path, wav_path=wav_path)

    if progress_callback:
        progress_callback(70, "Scoring segments by reaction...")

    segment_scores = compute_event_scores(events_data, segments)

    reaction_times = [
        e["time"] for e in events_data if _reaction_level(e) >= reaction_threshold
    ]

    if progress_callback:
        progress_callback(100, "Reaction analysis complete")

    return {
        "events_data": events_data,
        "segment_scores": segment_scores,
        "reaction_times": reaction_times,
    }
