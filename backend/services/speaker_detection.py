"""
Speaker diarization service.

Detects who is speaking when in a podcast using pyannote.audio.
Merges speaker labels with Whisper transcription segments for
per-word and per-segment speaker attribution.

Supports 2-person and 3+ person podcasts automatically.
"""

import os
import subprocess
import tempfile
from typing import Optional, Callable

# Fix torchaudio compatibility — speechbrain calls torchaudio.list_audio_backends()
# which was removed in torchaudio >= 2.10. Monkey-patch before any import.
try:
    import torchaudio
    if not hasattr(torchaudio, "list_audio_backends"):
        torchaudio.list_audio_backends = lambda: ["ffmpeg"]
except ImportError:
    pass

# Suppress noisy PyTorch warnings (std() degrees of freedom, etc.)
import warnings
warnings.filterwarnings("ignore", message="std\\(\\): degrees of freedom")
warnings.filterwarnings("ignore", category=UserWarning, module="pyannote")


def extract_audio_wav(video_path: str, output_path: str) -> str:
    """Extract audio as 16kHz mono WAV (required by pyannote)."""
    cmd = [
        "ffmpeg", "-y",
        "-i", video_path,
        "-vn",                  # No video
        "-acodec", "pcm_s16le", # 16-bit PCM
        "-ar", "16000",         # 16kHz
        "-ac", "1",             # Mono
        output_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"Audio extraction failed: {result.stderr[-300:]}")
    return output_path


def run_diarization(
    audio_path: str,
    num_speakers: Optional[int] = None,
    min_speakers: int = 2,
    max_speakers: int = 5,
    hf_token: Optional[str] = None,
    progress_callback: Optional[Callable[[int, str], None]] = None,
) -> list[dict]:
    """
    Run speaker diarization on an audio file.

    Returns list of speaker segments:
        [{"speaker": "SPEAKER_00", "start": 0.5, "end": 3.2}, ...]

    Args:
        audio_path: Path to WAV audio file
        num_speakers: Exact number of speakers (if known)
        min_speakers: Minimum speakers to detect
        max_speakers: Maximum speakers to detect
        hf_token: HuggingFace token for pyannote model access
    """
    token = hf_token or os.environ.get("HF_TOKEN", "")

    try:
        from pyannote.audio import Pipeline
    except ImportError:
        msg = "pyannote.audio not installed — run: pip install pyannote.audio"
        if progress_callback:
            progress_callback(0, msg)
        raise ImportError(msg)

    if not token:
        msg = (
            "HF_TOKEN not set — speaker detection requires a HuggingFace token.\n"
            "\n"
            "  Setup (one-time):\n"
            "  1. Create a token at https://huggingface.co/settings/tokens\n"
            "  2. Accept model terms at ALL THREE:\n"
            "     → https://huggingface.co/pyannote/speaker-diarization-3.1\n"
            "     → https://huggingface.co/pyannote/segmentation-3.0\n"
            "     → https://huggingface.co/pyannote/speaker-diarization-community-1\n"
            "  3. Add to your .env file: HF_TOKEN=hf_your_token_here"
        )
        if progress_callback:
            progress_callback(0, msg)
        raise PermissionError(msg)

    if progress_callback:
        progress_callback(10, "Loading speaker diarization model...")

    try:
        # pyannote >= 3.1 uses 'token', older versions use 'use_auth_token'
        try:
            pipeline = Pipeline.from_pretrained(
                "pyannote/speaker-diarization-3.1",
                token=token,
            )
        except TypeError:
            pipeline = Pipeline.from_pretrained(
                "pyannote/speaker-diarization-3.1",
                use_auth_token=token,
            )
    except Exception as e:
        err_str = str(e).lower()
        if "token" in err_str or "auth" in err_str or "403" in err_str or "401" in err_str:
            msg = (
                f"HuggingFace auth failed: {e}\n"
                "\n"
                "  Fix: Accept model terms at ALL THREE (while logged into HuggingFace):\n"
                "  → https://huggingface.co/pyannote/speaker-diarization-3.1\n"
                "  → https://huggingface.co/pyannote/segmentation-3.0\n"
                "  → https://huggingface.co/pyannote/speaker-diarization-community-1\n"
                "  Then verify your HF_TOKEN is valid."
            )
            if progress_callback:
                progress_callback(0, msg)
            raise PermissionError(msg) from e
        raise

    # Move pipeline to GPU if available (Apple Silicon MPS or CUDA)
    import torch
    device = "cpu"
    if torch.cuda.is_available():
        device = "cuda"
        pipeline.to(torch.device("cuda"))
    elif hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
        try:
            pipeline.to(torch.device("mps"))
            device = "mps"
        except Exception:
            pass  # Some pyannote components don't support MPS, fall back to CPU

    # Estimate duration for progress message
    import wave
    try:
        with wave.open(audio_path, "rb") as wf:
            audio_dur = wf.getnframes() / wf.getframerate()
        if device != "cpu":
            est_min = max(1, int(audio_dur / 600))  # GPU: ~2x faster
            time_msg = f"Running speaker diarization on {device.upper()} (~{est_min}-{est_min * 2} min)..."
        else:
            est_min = max(1, int(audio_dur / 300))
            time_msg = f"Running speaker diarization on CPU (~{est_min}-{est_min * 2} min for {int(audio_dur / 60)} min audio)..."
    except Exception:
        time_msg = f"Running speaker diarization on {device.upper()}..."

    if progress_callback:
        progress_callback(30, time_msg)

    # Run diarization — this is a blocking call, no progress updates from pyannote
    diarization_params = {}
    if num_speakers:
        diarization_params["num_speakers"] = num_speakers
    else:
        diarization_params["min_speakers"] = min_speakers
        diarization_params["max_speakers"] = max_speakers

    diarization = pipeline(audio_path, **diarization_params)

    if progress_callback:
        progress_callback(90, "Processing speaker segments...")

    # pyannote >= 4.0 returns DiarizeOutput; extract the Annotation from it
    if hasattr(diarization, "speaker_diarization"):
        diarization = diarization.speaker_diarization

    # Convert to simple list format
    speaker_segments = []
    for turn, _, speaker in diarization.itertracks(yield_label=True):
        speaker_segments.append({
            "speaker": speaker,
            "start": round(turn.start, 3),
            "end": round(turn.end, 3),
        })

    if progress_callback:
        progress_callback(100, f"Detected {len(set(s['speaker'] for s in speaker_segments))} speakers")

    return speaker_segments


def assign_speakers_to_segments(
    segments: list[dict],
    speaker_segments: list[dict],
) -> list[dict]:
    """
    Assign speaker labels to transcription segments.

    For each transcription segment, find the speaker who talks the most
    during that segment's time range (majority vote).
    """
    if not speaker_segments:
        # No diarization data — return segments unchanged
        for seg in segments:
            seg["speaker"] = None
        return segments

    for seg in segments:
        seg_start = seg["start"]
        seg_end = seg["end"]

        # Find overlapping speaker segments
        speaker_overlap = {}
        for sp in speaker_segments:
            overlap_start = max(seg_start, sp["start"])
            overlap_end = min(seg_end, sp["end"])
            overlap = max(0, overlap_end - overlap_start)

            if overlap > 0:
                speaker_overlap[sp["speaker"]] = speaker_overlap.get(sp["speaker"], 0) + overlap

        if speaker_overlap:
            # Assign speaker with most overlap
            seg["speaker"] = max(speaker_overlap, key=speaker_overlap.get)
        else:
            seg["speaker"] = None

    return segments


def assign_speakers_to_words(
    words: list[dict],
    speaker_segments: list[dict],
) -> list[dict]:
    """
    Assign speaker labels to individual words.

    Uses the midpoint of each word's timestamp to determine
    which speaker segment it falls within.
    """
    if not speaker_segments:
        for w in words:
            w["speaker"] = None
        return words

    for w in words:
        midpoint = (w["start"] + w["end"]) / 2

        # Find which speaker segment contains this midpoint
        assigned = None
        for sp in speaker_segments:
            if sp["start"] <= midpoint <= sp["end"]:
                assigned = sp["speaker"]
                break

        w["speaker"] = assigned

    return words


def create_speaker_summary(speaker_segments: list[dict]) -> dict:
    """
    Create a summary of speaker activity.

    Returns:
        {
            "num_speakers": int,
            "speakers": {
                "SPEAKER_00": {"total_time": float, "segments": int, "label": "Speaker 1"},
                ...
            }
        }
    """
    if not speaker_segments:
        return {"num_speakers": 0, "speakers": {}}

    speaker_stats = {}
    for sp in speaker_segments:
        name = sp["speaker"]
        if name not in speaker_stats:
            speaker_stats[name] = {"total_time": 0, "segments": 0}
        speaker_stats[name]["total_time"] += sp["end"] - sp["start"]
        speaker_stats[name]["segments"] += 1

    # Sort by total speaking time (most talkative first)
    sorted_speakers = sorted(speaker_stats.items(), key=lambda x: -x[1]["total_time"])

    # Assign friendly labels
    result = {}
    for i, (name, stats) in enumerate(sorted_speakers):
        result[name] = {
            "total_time": round(stats["total_time"], 1),
            "segments": stats["segments"],
            "label": f"Speaker {i + 1}",
        }

    return {
        "num_speakers": len(result),
        "speakers": result,
    }
