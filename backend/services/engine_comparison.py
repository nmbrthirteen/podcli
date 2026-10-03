"""Compare two transcription engines over the same window of a file.

Transcribes a sample range twice, once per engine, and reports where the two
transcripts disagree, windowed every 20s by default. This measures
disagreement between the two outputs, not accuracy against a ground truth.
Neither engine is assumed correct.
"""

from __future__ import annotations

import html
import json
import os
import shutil
import unicodedata
from typing import Any, Optional

# Unicode categories kept when normalizing a word for comparison: letters (L*),
# marks (M*, e.g. combining diacritics), numbers (N*). Punctuation and symbols
# are dropped so "hello," and "hello" agree.
_KEEP_CATEGORY_PREFIXES = ("L", "M", "N")


def normalize_word(word: str) -> str:
    """NFKC-normalize, casefold, and strip everything but letters/marks/numbers."""
    normalized = unicodedata.normalize("NFKC", word or "").casefold()
    return "".join(ch for ch in normalized if unicodedata.category(ch)[0] in _KEEP_CATEGORY_PREFIXES)


def normalize_words(words: list[str]) -> list[str]:
    return [w for w in (normalize_word(w) for w in words) if w]


def word_levenshtein(a: list[str], b: list[str]) -> int:
    """Levenshtein distance over word sequences (insert/delete/substitute, cost 1 each)."""
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, wa in enumerate(a, 1):
        curr = [i] + [0] * len(b)
        for j, wb in enumerate(b, 1):
            cost = 0 if wa == wb else 1
            curr[j] = min(
                prev[j] + 1,       # delete from a
                curr[j - 1] + 1,   # insert into a
                prev[j - 1] + cost,  # substitute
            )
        prev = curr
    return prev[-1]


def disagreement_ratio(text_a: str, text_b: str) -> float:
    """Levenshtein over normalized words, divided by the longer word count.
    0.0 = identical (after normalization), 1.0 = completely different.
    Two empty texts disagree 0.0 (nothing to disagree about), so callers should
    flag that case separately rather than reading it as agreement."""
    words_a = normalize_words(text_a.split())
    words_b = normalize_words(text_b.split())
    denom = max(len(words_a), len(words_b))
    if denom == 0:
        return 0.0
    return word_levenshtein(words_a, words_b) / denom


def _words_in_window(words: list[dict], window_start: float, window_end: float) -> str:
    """Join words whose midpoint falls in [window_start, window_end). Matches
    the midpoint-ownership rule used elsewhere so a word isn't double-counted
    in two adjacent windows."""
    picked = []
    for w in words:
        try:
            start, end = float(w.get("start", 0.0)), float(w.get("end", 0.0))
        except (TypeError, ValueError):
            continue
        midpoint = (start + end) / 2.0
        if window_start <= midpoint < window_end:
            text = str(w.get("word", "")).strip()
            if text:
                picked.append(text)
    return " ".join(picked)


def build_windows(
    words_a: list[dict],
    words_b: list[dict],
    total_duration: float,
    window_seconds: float = 20.0,
) -> list[dict]:
    windows = []
    n_windows = max(1, int(total_duration // window_seconds) + (1 if total_duration % window_seconds else 0))
    for i in range(n_windows):
        start = i * window_seconds
        end = min(start + window_seconds, total_duration)
        text_a = _words_in_window(words_a, start, end)
        text_b = _words_in_window(words_b, start, end)
        both_empty = not text_a and not text_b
        windows.append({
            "start": round(start, 3),
            "end": round(end, 3),
            "text_a": text_a,
            "text_b": text_b,
            "both_empty": both_empty,
            "disagreement": 0.0 if both_empty else round(disagreement_ratio(text_a, text_b), 4),
        })
    return windows


def compare_engines(
    file_path: str,
    engine_a: str,
    engine_b: str,
    *,
    start_seconds: float = 0.0,
    duration_seconds: Optional[float] = None,
    window_seconds: float = 20.0,
    model_size: str = "base",
    language: Optional[str] = None,
    output_dir: Optional[str] = None,
    transcribe_fn=None,
) -> dict:
    """Transcribe the same [start_seconds, start_seconds + duration_seconds)
    window with engine_a and engine_b, and report per-window disagreement.

    transcribe_fn defaults to services.transcription.transcribe_file; tests
    inject a fake to avoid running real engines.
    """
    if transcribe_fn is None:
        from services.transcription import transcribe_file as transcribe_fn

    result_a = transcribe_fn(
        file_path, model_size=model_size, engine=engine_a, language=language,
        enable_diarization=False, start_seconds=start_seconds, duration_seconds=duration_seconds,
    )
    result_b = transcribe_fn(
        file_path, model_size=model_size, engine=engine_b, language=language,
        enable_diarization=False, start_seconds=start_seconds, duration_seconds=duration_seconds,
    )

    duration = max(
        float(result_a.get("duration", 0.0) or 0.0),
        float(result_b.get("duration", 0.0) or 0.0),
        duration_seconds or 0.0,
    )
    windows = build_windows(
        result_a.get("words") or [], result_b.get("words") or [], duration, window_seconds
    )
    scored = [w["disagreement"] for w in windows if not w["both_empty"]]
    overall_disagreement = round(sum(scored) / len(scored), 4) if scored else 0.0
    flagged_empty = sum(1 for w in windows if w["both_empty"])

    report = {
        "file_path": file_path,
        "engine_a": engine_a,
        "engine_b": engine_b,
        "start_seconds": start_seconds,
        "duration_seconds": duration,
        "window_seconds": window_seconds,
        "windows": windows,
        "overall_disagreement": overall_disagreement,
        "both_empty_window_count": flagged_empty,
        "note": "disagreement between the two engines' output, not accuracy against a transcript",
    }

    if output_dir:
        os.makedirs(output_dir, exist_ok=True)
        json_path = os.path.join(output_dir, "comparison.json")
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, ensure_ascii=False)
        report["json_path"] = json_path

        # A fresh extraction of the same window, purely for the report's
        # <audio> player, independent of whatever transcribe_fn did
        # internally (its own sample wav is already cleaned up by the time
        # we get a result back).
        audio_rel = None
        try:
            from services.audio_extract import extract_wav_16k_mono

            sample_wav = extract_wav_16k_mono(
                file_path, start_seconds=start_seconds, duration_seconds=duration_seconds
            )
            try:
                audio_rel = "sample.wav"
                shutil.copyfile(sample_wav, os.path.join(output_dir, audio_rel))
            finally:
                try:
                    os.unlink(sample_wav)
                except OSError:
                    pass
        except Exception:
            audio_rel = None

        html_path = os.path.join(output_dir, "comparison.html")
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(render_html(report, audio_rel))
        report["html_path"] = html_path

    return report


def _json_for_script_tag(data: Any) -> str:
    """json.dumps, safe to embed inside a <script> tag.

    Replacing only a literal "</script" (case-sensitively) left "</SCRIPT>"
    or "</ScRiPt>" in ASR text able to close the tag early and inject HTML.
    Escaping every "<" closes that regardless of case, and also neutralizes
    "<!--". U+2028/U+2029 are escaped too: valid in a JSON string, but
    treated as line terminators by some JS engines even inside a string
    literal, which can truncate the script.
    """
    encoded = json.dumps(data, ensure_ascii=False)
    return (
        encoded.replace("<", "\\u003c")
        .replace(" ", "\\u2028")
        .replace(" ", "\\u2029")
    )


def render_html(report: dict, audio_rel: Optional[str]) -> str:
    engine_a = html.escape(str(report["engine_a"]))
    engine_b = html.escape(str(report["engine_b"]))
    file_label = html.escape(os.path.basename(str(report["file_path"])))
    overall = report["overall_disagreement"]
    windows_json = _json_for_script_tag(report["windows"])
    audio_html = (
        f'<audio id="player" controls preload="metadata" src="{html.escape(audio_rel)}"></audio>'
        if audio_rel
        else "<p class=\"no-audio\">No sample audio saved alongside this report.</p>"
    )
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Engine comparison: {engine_a} vs {engine_b} ({file_label})</title>
<style>
  body {{ font-family: -apple-system, system-ui, sans-serif; margin: 2rem; color: #1a1a1a; background: #fafafa; }}
  h1 {{ font-size: 1.25rem; }}
  .note {{ color: #666; font-size: 0.9rem; margin-bottom: 1.5rem; }}
  table {{ border-collapse: collapse; width: 100%; }}
  th, td {{ border: 1px solid #ddd; padding: 0.5rem 0.75rem; text-align: left; vertical-align: top; }}
  th {{ background: #f0f0f0; }}
  tr.both-empty {{ background: #fff8e1; }}
  .disagreement {{ font-variant-numeric: tabular-nums; }}
  .disagreement.high {{ color: #b00020; font-weight: 600; }}
  .disagreement.low {{ color: #2e7d32; }}
  button.seek {{ cursor: pointer; }}
  audio {{ width: 100%; margin-bottom: 1.5rem; }}
  .no-audio {{ color: #999; font-style: italic; }}
</style>
</head>
<body>
<h1>Engine comparison: {engine_a} vs {engine_b}</h1>
<p class="note">File: {file_label}. Overall disagreement: <span class="disagreement">{overall}</span>
(word-level edit distance between the two engines' output, normalized; not accuracy against either transcript).</p>
{audio_html}
<table>
  <thead><tr><th>Window</th><th>{engine_a}</th><th>{engine_b}</th><th>Disagreement</th></tr></thead>
  <tbody id="rows"></tbody>
</table>
<script>
const windows = {windows_json};
const rows = document.getElementById("rows");
const player = document.getElementById("player");
function esc(s) {{
  const d = document.createElement("div");
  d.textContent = s;
  return d.innerHTML;
}}
function fmtTime(s) {{
  const m = Math.floor(s / 60);
  const sec = Math.floor(s % 60).toString().padStart(2, "0");
  return m + ":" + sec;
}}
for (const w of windows) {{
  const tr = document.createElement("tr");
  if (w.both_empty) tr.className = "both-empty";
  const level = w.disagreement >= 0.5 ? "high" : w.disagreement <= 0.1 ? "low" : "";
  tr.innerHTML =
    '<td><button class="seek" data-t="' + w.start + '">' + fmtTime(w.start) + '</button>' +
    ' - ' + fmtTime(w.end) + (w.both_empty ? ' <em>(both empty)</em>' : '') + '</td>' +
    '<td>' + esc(w.text_a) + '</td>' +
    '<td>' + esc(w.text_b) + '</td>' +
    '<td class="disagreement ' + level + '">' + w.disagreement.toFixed(2) + '</td>';
  rows.appendChild(tr);
}}
rows.addEventListener("click", (e) => {{
  const btn = e.target.closest("button.seek");
  if (!btn || !player) return;
  player.currentTime = parseFloat(btn.dataset.t);
  player.play();
}});
</script>
</body>
</html>
"""
