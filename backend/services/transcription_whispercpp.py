"""whisper.cpp transcription adapter — emits the same contract dict as
services.transcription.transcribe_file (segments + word-level timestamps), so it
is a drop-in engine behind that seam.

whisper.cpp emits subword *tokens* with a literal leading-space convention
(" and", " just", continuation/punctuation tokens have no leading space). We
merge tokens into words on that boundary and apply the exact same word-text
normalization the rest of the pipeline expects (strip) — this is the single
highest-risk integration detail: apply_corrections() and caption spacing match
on stripped word text, so the new engine's words must normalize identically.

Requires a whisper-cli binary and a ggml model. In production these come from
the hermetic provisioner; here they are parameters/env so the parity harness can
point at a local install.
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
