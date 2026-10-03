"""Omnilingual ASR (sherpa-onnx CTC) adapter behind the transcribe_file contract.

Meta's Omnilingual ASR CTC model covers ~1600 languages, including ones
whisper.cpp handles poorly. It's character-level CTC: tokens.txt has no
SentencePiece "continuation vs. word-start" convention. Token id 4 is a
literal space character, emitted by the model itself as a word boundary.
Grouping tokens into words means splitting the token stream on that space
token, not on a leading-marker prefix like the whisper.cpp adapter does.

The model takes no language conditioning: there's no -l equivalent. It
decodes everything through the same multilingual weights.
"""

import hashlib
import json
import os
import shutil
import urllib.request
import uuid
import wave
from typing import Optional

# Mirrors the pins in cli/internal/provision/provision.go so the studio and
# MCP paths can fetch the model on first use, not only the launcher.
MODEL_REVISION = "5243e02858d1428b8fbeeb5f7f1cc6fccf4d9433"
MODEL_REPO = "csukuangfj2/sherpa-onnx-omnilingual-asr-1600-languages-1B-ctc-v2-int8-2026-02-05"
MODEL_FILES = {
    "model.int8.onnx": ("8af72da192fc2c8567c328d4f8059bdf47f182a0369077893c295ef39740c637", 1032239439),
    "tokens.txt": ("7d99997ef207ff14c2cfe825f2aa037528ea250113cc3c6392bfe49326884ba6", 90630),
    "LICENSE": ("a70a523bafbb595c2844104feb313d204904dac91c3d186c05f22a10a71c7a94", 581),
}

# Decode window: 20s cores tiled across the file, each padded with 1s of
# context on each side (clipped to file bounds) so CTC isn't starved of
# context at a window edge. A word's midpoint decides which core window
# claims it, so the context padding doesn't duplicate words across windows.
WINDOW_SECONDS = 20.0
CONTEXT_SECONDS = 1.0

# Mirrors transcript_packer.SILENCE_SPLIT_SEC: the pause length that splits
# a run of words into separate segments.
SEGMENT_PAUSE_SECONDS = 0.5

# whisper-cli's trailing-token stretch over silence doesn't apply to a CTC
# model with no language-model smoothing, but a token's timestamp from
# sherpa-onnx is its emission time, not its end; pad the last token of a
# word by the model's frame stride (10ms at this model's subsampling) so a
# one-token word doesn't collapse to zero length.
WORD_END_PAD_SECONDS = 0.02

_WORD_BOUNDARY = " "


def _sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def ensure_model(model_dir: str, progress_callback=None) -> None:
    """Download any missing or outdated pinned model file into model_dir.

    Files only land through a hash-checked rename, so an existing file of the
    pinned size is trusted without rehashing 1 GB on every run. A size
    mismatch means an older model (the 300M one) and gets replaced.
    """
    os.makedirs(model_dir, exist_ok=True)
    for name, (sha, size) in MODEL_FILES.items():
        dest = os.path.join(model_dir, name)
        if os.path.exists(dest) and os.path.getsize(dest) == size:
            continue
        if progress_callback:
            progress_callback(5, f"Downloading the omnilingual model, {size / 1e9:.1f} GB on first use ({name})")
        url = f"https://huggingface.co/{MODEL_REPO}/resolve/{MODEL_REVISION}/{name}"
        tmp = os.path.join(model_dir, f".{name}.{uuid.uuid4().hex}.download")
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "podcli"})
            with urllib.request.urlopen(request, timeout=60) as response, open(tmp, "wb") as target:
                shutil.copyfileobj(response, target, 4 * 1024 * 1024)
            if _sha256(tmp) != sha:
                raise RuntimeError(f"omnilingual {name} checksum mismatch; download again")
            os.replace(tmp, dest)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)


def _read_wav_mono16(wav_path: str):
    """Returns (samples: np.float32 in [-1, 1], sample_rate, duration_seconds)."""
    import numpy as np

    with wave.open(wav_path, "rb") as w:
        sr, width, n = w.getframerate(), w.getsampwidth(), w.getnframes()
        raw = w.readframes(n)
    if width != 2:
        raise ValueError(f"omnilingual adapter needs 16-bit PCM wav, got {width * 8}-bit: {wav_path}")
    samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    duration = len(samples) / sr if sr else 0.0
    return samples, sr, duration


def _load_recognizer(model_path: str, tokens_path: str, threads: int):
    import sherpa_onnx

    if not hasattr(sherpa_onnx.OfflineRecognizer, "from_omnilingual_asr_ctc"):
        raise RuntimeError(
            "This sherpa-onnx version has no OfflineRecognizer.from_omnilingual_asr_ctc; "
            "upgrade sherpa-onnx (pip install -U sherpa-onnx) to use --engine omnilingual."
        )
    return sherpa_onnx.OfflineRecognizer.from_omnilingual_asr_ctc(
        model=model_path, tokens=tokens_path, num_threads=threads, provider="cpu",
    )


def _validate_tokens_and_timestamps(tokens: list[str], timestamps: list[float], window_label: str) -> None:
    if len(tokens) != len(timestamps):
        raise ValueError(
            f"omnilingual decode produced {len(tokens)} tokens but {len(timestamps)} timestamps ({window_label})"
        )
    prev = -1.0
    for i, t in enumerate(timestamps):
        if t != t or t < 0:  # t != t is the NaN check
            raise ValueError(f"omnilingual decode produced an invalid timestamp {t!r} at token {i} ({window_label})")
        if t < prev:
            raise ValueError(f"omnilingual decode produced out-of-order timestamps at token {i} ({window_label})")
        prev = t


def _tokens_to_words(tokens: list[str], timestamps: list[float], decode_end: float) -> list[dict]:
    """Split a flat token stream on the word-boundary space token, into
    {word, start, end} with absolute-within-decode-window times."""
    words: list[dict] = []
    cur_tokens: list[str] = []
    cur_times: list[float] = []

    def flush():
        if not cur_tokens:
            return
        text = "".join(cur_tokens).strip()
        if not text:
            return
        start = cur_times[0]
        end = min(cur_times[-1] + WORD_END_PAD_SECONDS, decode_end)
        if end <= start:
            end = start + WORD_END_PAD_SECONDS
        words.append({"word": text, "start": start, "end": end})

    for tok, ts in zip(tokens, timestamps):
        if tok == _WORD_BOUNDARY:
            flush()
            cur_tokens, cur_times = [], []
            continue
        cur_tokens.append(tok)
        cur_times.append(ts)
    flush()
    return words


def _drop_seam_duplicates(prev_words: list[dict], candidates: list[dict], boundary: float) -> list[dict]:
    """Drop a candidate word that the previous window already claimed near
    their shared boundary. Each window owns a disjoint [core_start,
    core_end) by its own midpoint estimate (see transcribe_file), but the
    1s of context padding on each side means the same audio gets decoded
    twice, by two separate recognizer calls, and the acoustic model can give
    the same word a slightly different timestamp each time, so it can pass
    both windows' own membership test and get counted twice. Only words
    near the boundary are ever compared, so a legitimate stutter/repeat
    elsewhere in the transcript is never touched.
    """
    kept = []
    for w in candidates:
        near_boundary = abs(w["start"] - boundary) <= CONTEXT_SECONDS or abs(w["end"] - boundary) <= CONTEXT_SECONDS
        is_duplicate = near_boundary and any(
            pw["word"] == w["word"] and abs(pw["start"] - w["start"]) <= CONTEXT_SECONDS
            for pw in prev_words
            if abs(pw["end"] - boundary) <= CONTEXT_SECONDS or abs(pw["start"] - boundary) <= CONTEXT_SECONDS
        )
        if not is_duplicate:
            kept.append(w)
    return kept


def _group_into_segments(words: list[dict]) -> list[dict]:
    """Group words into segments on a pause >= SEGMENT_PAUSE_SECONDS, the
    same boundary transcript_packer uses to split phrases for the packed
    markdown view."""
    segments: list[dict] = []
    current: Optional[dict] = None
    for w in words:
        if current is not None and w["start"] - current["end"] >= SEGMENT_PAUSE_SECONDS:
            segments.append(current)
            current = None
        if current is None:
            current = {"text": w["word"], "start": w["start"], "end": w["end"]}
        else:
            current["text"] += " " + w["word"]
            current["end"] = w["end"]
    if current is not None:
        segments.append(current)
    return [
        {"id": i, "start": round(s["start"], 3), "end": round(s["end"], 3), "text": s["text"], "speaker": None}
        for i, s in enumerate(segments)
    ]


def transcribe_file(
    file_path: str,
    model_path: str,
    tokens_path: str,
    threads: int = 4,
    wav_path: Optional[str] = None,
    run_dir: Optional[str] = None,
    progress_callback=None,
    **_ignored,
) -> dict:
    """run_dir, if given, makes this resumable: each core window's words are
    written to run_dir/window-<i>.json as soon as they're decoded, and a
    rerun with the same run_dir skips any window that already has one. The
    final transcript is only assembled (and the receipts cleared) once every
    window has a receipt. A crash mid-file loses at most the window that
    was decoding, not the whole run."""
    if not os.path.exists(file_path):
        raise FileNotFoundError(file_path)
    if not os.path.exists(model_path):
        raise FileNotFoundError(f"omnilingual model not found: {model_path}")
    if not os.path.exists(tokens_path):
        raise FileNotFoundError(f"omnilingual tokens not found: {tokens_path}")

    owns_wav = False
    if not (wav_path and os.path.exists(wav_path)):
        from services.audio_extract import extract_wav_16k_mono

        wav_path = extract_wav_16k_mono(file_path)
        owns_wav = True

    from services import transcribe_runs

    try:
        samples, sr, duration = _read_wav_mono16(wav_path)
        recognizer = None  # lazily loaded: a fully-resumed run never needs it

        all_words: list[dict] = []
        n_windows = max(1, int(duration // WINDOW_SECONDS) + (1 if duration % WINDOW_SECONDS else 0))
        for i in range(n_windows):
            core_start = i * WINDOW_SECONDS
            core_end = min(core_start + WINDOW_SECONDS, duration)

            receipt_name = f"window-{i}.json"
            cached = transcribe_runs.read_receipt(run_dir, receipt_name) if run_dir else None
            if cached is not None:
                all_words.extend(cached.get("words") or [])
                if progress_callback:
                    progress_callback(int(100 * (i + 1) / n_windows), f"Window {i + 1}/{n_windows} (resumed)")
                continue

            decode_start = max(0.0, core_start - CONTEXT_SECONDS)
            decode_end = min(duration, core_end + CONTEXT_SECONDS)

            lo, hi = int(decode_start * sr), int(decode_end * sr)
            chunk = samples[lo:hi]
            if chunk.size == 0:
                continue

            if recognizer is None:
                recognizer = _load_recognizer(model_path, tokens_path, threads)

            stream = recognizer.create_stream()
            stream.accept_waveform(sr, chunk)
            recognizer.decode_stream(stream)
            result = stream.result

            tokens = list(result.tokens)
            timestamps = [decode_start + float(t) for t in result.timestamps]
            _validate_tokens_and_timestamps(tokens, timestamps, window_label=f"window {i} [{decode_start:.1f}, {decode_end:.1f})")

            words = _tokens_to_words(tokens, timestamps, decode_end)
            window_words = [
                w for w in words
                if core_start <= (w["start"] + w["end"]) / 2.0 < core_end
                or (i == n_windows - 1 and (w["start"] + w["end"]) / 2.0 == core_end)
            ]
            if i > 0:
                # Ownership-by-midpoint alone isn't enough at a seam: the
                # context overlap means the same word gets decoded twice,
                # once in each neighbor's own context, and the acoustic
                # model doesn't always timestamp it identically both times.
                # A word whose own-window estimate lands it just past the
                # boundary can duplicate one the previous window already
                # claimed under its own (slightly different) estimate.
                window_words = _drop_seam_duplicates(all_words, window_words, core_start)
            all_words.extend(window_words)
            if run_dir:
                transcribe_runs.write_receipt(run_dir, receipt_name, {"words": window_words})
            if progress_callback:
                progress_callback(int(100 * (i + 1) / n_windows), f"Window {i + 1}/{n_windows}")

        if run_dir:
            transcribe_runs.clear_run(run_dir)

        all_words.sort(key=lambda w: w["start"])
        words_out = [
            {"word": w["word"], "start": round(w["start"], 3), "end": round(w["end"], 3), "speaker": None}
            for w in all_words
        ]
        segments = _group_into_segments(all_words)

        return {
            "transcript": " ".join(w["word"] for w in words_out).strip(),
            "segments": segments,
            "words": words_out,
            "duration": round(duration, 3),
            # The model takes no language conditioning: it decodes every
            # language through the same weights. "und" reflects that nothing
            # was detected or requested, not that detection failed.
            "language": "und",
        }
    finally:
        if owns_wav and wav_path and os.path.exists(wav_path):
            try:
                os.unlink(wav_path)
            except OSError:
                pass


if __name__ == "__main__":
    import sys

    media, model, tokens = sys.argv[1], sys.argv[2], sys.argv[3]
    out = sys.argv[4] if len(sys.argv) > 4 else None
    result = transcribe_file(media, model, tokens)
    payload = json.dumps(result, indent=2, ensure_ascii=False)
    if out:
        with open(out, "w", encoding="utf-8") as f:
            f.write(payload)
        print(f"{len(result['words'])} words, {len(result['segments'])} segments -> {out}")
    else:
        print(payload)
