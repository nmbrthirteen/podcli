"""SRT/VTT sidecar generation from a rendered clip's word list.

Every rendered clip's captions are burned from a word list already retimed
to the clip's own playback clock (0 at the first frame of the exported
file). These sidecars are the same words and the same clock, written out as
plain subtitle files instead of pixels, useful for platforms that take an
uploaded subtitle track, for accessibility, and for anyone editing the clip
further downstream.
"""

from typing import Optional


def _timestamp(seconds: float, vtt: bool) -> str:
    """HH:MM:SS,mmm (SRT) or HH:MM:SS.mmm (VTT).

    Rounds to whole milliseconds once, up front, then derives h/m/s/ms by
    integer division, so a value like 1.9996 carries into 2.000 instead of
    rounding seconds and milliseconds separately and emitting "01,1000".
    """
    total_ms = round(max(0.0, seconds) * 1000)
    h, rem = divmod(total_ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    sep = "." if vtt else ","
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{ms:03d}"


def _group_words(words: list[dict], words_per_line: int) -> list[dict]:
    groups = []
    chunk: list[dict] = []
    for w in words:
        text = (w.get("word") or "").strip()
        if not text:
            continue
        chunk.append(w)
        if len(chunk) >= words_per_line:
            groups.append(chunk)
            chunk = []
    if chunk:
        groups.append(chunk)
    return [
        {
            "text": " ".join(w["word"].strip() for w in g),
            "start": g[0]["start"],
            "end": g[-1]["end"],
        }
        for g in groups
    ]


def words_to_srt(words: list[dict], words_per_line: int = 8) -> Optional[str]:
    """SRT text from retimed words, or None if there's nothing to write."""
    groups = _group_words(words, words_per_line)
    if not groups:
        return None
    blocks = []
    for i, g in enumerate(groups, 1):
        blocks.append(
            f"{i}\n{_timestamp(g['start'], False)} --> {_timestamp(g['end'], False)}\n{g['text']}\n"
        )
    return "\n".join(blocks)


def words_to_vtt(words: list[dict], words_per_line: int = 8) -> Optional[str]:
    """WEBVTT text from retimed words, or None if there's nothing to write."""
    groups = _group_words(words, words_per_line)
    if not groups:
        return None
    blocks = ["WEBVTT\n"]
    for i, g in enumerate(groups, 1):
        blocks.append(
            f"{i}\n{_timestamp(g['start'], True)} --> {_timestamp(g['end'], True)}\n{g['text']}\n"
        )
    return "\n".join(blocks)


def write_sidecars(words: list[dict], output_base: str, words_per_line: int = 8) -> dict:
    """Write {output_base}.srt and {output_base}.vtt. Returns the paths
    actually written (a key is omitted rather than pointing at an empty file
    when there are no words to caption).
    """
    paths: dict[str, str] = {}
    srt = words_to_srt(words, words_per_line)
    if srt is not None:
        srt_path = f"{output_base}.srt"
        with open(srt_path, "w", encoding="utf-8") as f:
            f.write(srt)
        paths["srt_path"] = srt_path

    vtt = words_to_vtt(words, words_per_line)
    if vtt is not None:
        vtt_path = f"{output_base}.vtt"
        with open(vtt_path, "w", encoding="utf-8") as f:
            f.write(vtt)
        paths["vtt_path"] = vtt_path

    return paths
