"""whisper.cpp adapter behind the transcribe_file contract.

Tokens carry a leading-space convention (" and", continuations have none); we
merge on that boundary and strip word text. The strip must match the rest of the
pipeline exactly — apply_corrections() and caption spacing key on stripped text.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from typing import Optional

_SPECIAL = re.compile(r"^\[.*\]$")  # [_BEG_], [_TT_...], etc.


def _extract_wav(media_path: str, wav_path: str, ffmpeg: str = "ffmpeg") -> None:
    subprocess.run(
        [ffmpeg, "-y", "-loglevel", "error", "-i", media_path,
         "-ar", "16000", "-ac", "1", wav_path],
        check=True,
        timeout=1800,
    )


def _tokens_to_words(tokens: list[dict]) -> list[dict]:
    """Merge whisper.cpp subword tokens into words with start/end seconds."""
    words: list[dict] = []
    cur_text = ""
    cur_start: Optional[float] = None
    cur_end: Optional[float] = None

    def flush():
        nonlocal cur_text, cur_start, cur_end
        text = cur_text.strip().lstrip("▁").strip()
        if text and cur_start is not None:
            words.append({
                "word": text,
                "start": round(cur_start / 1000.0, 3),
                "end": round(cur_end / 1000.0, 3),
                "speaker": None,
            })
        cur_text, cur_start, cur_end = "", None, None

    for tok in tokens:
        raw = tok.get("text", "")
        if _SPECIAL.match(raw.strip()):
            continue
        off = tok.get("offsets") or {}
        t0, t1 = off.get("from"), off.get("to")
        starts_word = raw.startswith(" ") or raw.startswith("▁")
        if starts_word and cur_text:
            flush()
        if cur_start is None and t0 is not None:
            cur_start = t0
        cur_text += raw
        if t1 is not None:
            cur_end = t1
    flush()
    return words


def _voiced_intervals(wav_path: str, bridge: float = 0.3, thresh_ratio: float = 0.07):
    import wave

    import numpy as np

    try:
        w = wave.open(wav_path, "rb")
        sr, width, n = w.getframerate(), w.getsampwidth(), w.getnframes()
        raw = w.readframes(n)
        w.close()
    except Exception:
        return []
    if width != 2 or sr <= 0 or not raw:
        return []
    samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32)
    hop = max(1, int(sr * 0.010))
    frame = max(hop, int(sr * 0.025))
    if len(samples) < frame:
        return []
    nf = 1 + (len(samples) - frame) // hop
    idx = np.arange(nf)[:, None] * hop + np.arange(frame)[None, :]
    rms = np.sqrt((samples[idx] ** 2).mean(axis=1))
    peak = float(rms.max())
    if peak <= 0:
        return []
    voiced = rms > thresh_ratio * peak
    intervals, start = [], None
    for i, v in enumerate(voiced):
        if v and start is None:
            start = i * hop / sr
        elif not v and start is not None:
            intervals.append([start, i * hop / sr])
            start = None
    if start is not None:
        intervals.append([start, nf * hop / sr])
    merged = []
    for iv in intervals:
        if merged and iv[0] - merged[-1][1] <= bridge:
            merged[-1][1] = iv[1]
        else:
            merged.append(iv)
    return merged


def _snap_words_to_voiced(words: list[dict], wav_path: str) -> list[dict]:
    """Pull word timings back into the voiced span. whisper.cpp sometimes
    stretches trailing words across trailing silence; clamping to [first voiced,
    last voiced] (with a small pad) and re-flowing keeps captions in sync without
    disturbing words that already overlap speech."""
    if not words:
        return words
    intervals = _voiced_intervals(wav_path)
    if not intervals:
        return words
    pad = 0.15
    lo = max(0.0, intervals[0][0] - pad)
    hi = intervals[-1][1] + pad
    out, prev_end = [], lo
    for w in words:
        s = min(max(float(w.get("start", 0.0)), lo), hi)
        e = min(max(float(w.get("end", 0.0)), lo), hi)
        if s < prev_end:
            s = prev_end
        if e <= s:
            # A word clamped to hi would otherwise collapse to zero length; a
            # tiny overhang past the voiced pad is harmless, a zero-length
            # caption event is not.
            e = s + 0.05
        prev_end = e
        out.append({**w, "start": round(s, 3), "end": round(e, 3)})
    return out


def transcribe_file(
    file_path: str,
    model_path: str,
    whisper_cli: str = "whisper-cli",
    ffmpeg: str = "ffmpeg",
    language: Optional[str] = "en",
    dtw_model: str = "base",
    threads: int = 4,
    vad: bool = False,
    vad_model: Optional[str] = None,
    **_ignored,
) -> dict:
    if not os.path.exists(file_path):
        raise FileNotFoundError(file_path)
    if not os.path.exists(model_path):
        raise FileNotFoundError(f"ggml model not found: {model_path}")

    tmpdir = tempfile.mkdtemp(prefix="wcpp_")
    wav = os.path.join(tmpdir, "audio.wav")
    out_base = os.path.join(tmpdir, "out")
    try:
        _extract_wav(file_path, wav, ffmpeg)

        cmd = [whisper_cli, "-m", model_path, "-f", wav, "-ojf",
               "-of", out_base, "-t", str(threads)]
        if dtw_model:
            cmd += ["-dtw", dtw_model]
        if vad and vad_model and os.path.exists(vad_model):
            # VAD removes the trailing-words-into-silence failure mode but adds a
            # systematic early bias (silence-removal remapping). Off by default;
            # the energy-snap below addresses the same defect without the bias.
            cmd += ["--vad", "--vad-model", vad_model]
        if language:
            cmd += ["-l", language]
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, timeout=7200)

        with open(out_base + ".json", encoding="utf-8") as f:
            data = json.load(f)

        transcription = data.get("transcription", [])
        segments, words = [], []
        for i, seg in enumerate(transcription):
            off = seg.get("offsets") or {}
            segments.append({
                "id": i,
                "start": round((off.get("from") or 0) / 1000.0, 3),
                "end": round((off.get("to") or 0) / 1000.0, 3),
                "text": (seg.get("text") or "").strip(),
                "speaker": None,
            })
            words.extend(_tokens_to_words(seg.get("tokens", [])))

        if os.environ.get("PODCLI_WHISPERCPP_NO_SNAP", "").strip().lower() not in ("1", "true", "yes", "on"):
            words = _snap_words_to_voiced(words, wav)

        return {
            "transcript": " ".join(s["text"] for s in segments).strip(),
            "segments": segments,
            "words": words,
            "duration": segments[-1]["end"] if segments else 0.0,
            "language": (data.get("params") or {}).get("language") or language or "en",
        }
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    # Quick CLI: transcribe_whispercpp <media> <model> [out.json]
    media, model = sys.argv[1], sys.argv[2]
    out = sys.argv[3] if len(sys.argv) > 3 else None
    result = transcribe_file(media, model)
    payload = json.dumps(result, indent=2, ensure_ascii=False)
    if out:
        with open(out, "w", encoding="utf-8") as f:
            f.write(payload)
        print(f"{len(result['words'])} words, {len(result['segments'])} segments -> {out}")
    else:
        print(payload)
