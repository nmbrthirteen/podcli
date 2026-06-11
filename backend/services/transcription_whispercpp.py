"""whisper.cpp adapter behind the transcribe_file contract.

Tokens carry a leading-space convention (" and", continuations have none); we
merge on that boundary and strip word text. The strip must match the rest of the
pipeline exactly — apply_corrections() and caption spacing key on stripped text.
"""

import json
import os
import re
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
    )


def _tokens_to_words(tokens: list[dict]) -> list[dict]:
    """Merge whisper.cpp subword tokens into words with start/end seconds."""
    words: list[dict] = []
    cur_text = ""
    cur_start: Optional[float] = None
    cur_end: Optional[float] = None

    def flush():
        nonlocal cur_text, cur_start, cur_end
        text = cur_text.strip()
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
    _extract_wav(file_path, wav, ffmpeg)

    cmd = [whisper_cli, "-m", model_path, "-f", wav, "-ojf",
           "-of", out_base, "-t", str(threads)]
    if dtw_model:
        cmd += ["-dtw", dtw_model]
    if vad and vad_model and os.path.exists(vad_model):
        # VAD removes the trailing-words-into-silence failure mode but currently
        # adds a small systematic early bias (silence-removal remapping). Off by
        # default; opt in via PODCLI_WHISPERCPP_VAD.
        cmd += ["--vad", "--vad-model", vad_model]
    if language:
        cmd += ["-l", language]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    with open(out_base + ".json", encoding="utf-8") as f:
        data = json.load(f)

    transcription = data.get("transcription", [])
    segments, words = [], []
    for i, seg in enumerate(transcription):
        off = seg.get("offsets") or {}
        seg_start = round((off.get("from") or 0) / 1000.0, 3)
        seg_end = round((off.get("to") or 0) / 1000.0, 3)
        segments.append({
            "id": i,
            "start": seg_start,
            "end": seg_end,
            "text": (seg.get("text") or "").strip(),
            "speaker": None,
        })
        words.extend(_tokens_to_words(seg.get("tokens", [])))

    duration = segments[-1]["end"] if segments else 0.0
    return {
        "transcript": " ".join(s["text"] for s in segments).strip(),
        "segments": segments,
        "words": words,
        "duration": duration,
        "language": (data.get("params") or {}).get("language") or language or "en",
    }


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
