"""Shared 16 kHz mono WAV extraction.

The transcription, energy, and reaction analyzers all consume the same
16 kHz mono PCM audio. Extracting it once in the orchestration layer and
passing the wav path down avoids decoding a long source video 3-5 times
per run.
"""

import os
import tempfile
from typing import Optional

from utils.proc import run as proc_run


def extract_wav_16k_mono(
    media_path: str,
    wav_path: Optional[str] = None,
    timeout: int = 1800,
    start_seconds: Optional[float] = None,
    duration_seconds: Optional[float] = None,
) -> str:
    """Extract audio as 16 kHz mono 16-bit PCM WAV. Returns the wav path.

    When wav_path is None a temp file is created; the caller owns cleanup.
    start_seconds/duration_seconds trim the output to a window of the source,
    used for sample-mode transcription (test a language on a short clip
    instead of the full episode).
    """
    owns_wav = wav_path is None
    if owns_wav:
        fd, wav_path = tempfile.mkstemp(prefix="podcli_audio_", suffix=".wav")
        os.close(fd)
    cmd = ["ffmpeg", "-y", "-loglevel", "error"]
    if start_seconds:
        # Before -i: fast (keyframe-seek) trim. Precision to the frame
        # doesn't matter for a language-check sample.
        cmd += ["-ss", str(start_seconds)]
    cmd += ["-i", media_path]
    if duration_seconds:
        cmd += ["-t", str(duration_seconds)]
    cmd += [
        "-vn",
        "-acodec", "pcm_s16le",
        "-ar", "16000",
        "-ac", "1",
        wav_path,
    ]
    try:
        result = proc_run(cmd, timeout=timeout, check=False)
        if result.returncode != 0:
            raise RuntimeError(f"Audio extraction failed: {(result.stderr or '')[-300:]}")
    except Exception:
        if owns_wav:
            try:
                os.unlink(wav_path)
            except OSError:
                pass
        raise
    return wav_path
