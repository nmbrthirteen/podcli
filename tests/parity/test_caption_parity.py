"""Layer-1 caption parity: lock the deterministic, engine-independent surface.

Everything downstream of the transcript JSON contract (the `{words, segments}`
dict produced by transcribe_file / parse_speaker_transcript / JSON import /
cache) is pure code that does NOT change when we swap the transcription engine
or relocate the runtime. This test pins the most timestamp-sensitive part of
that surface — caption (ASS) generation — against committed goldens.

If a future change (whisper.cpp swap, hermetic runtime, a refactor) alters
caption output for a fixed transcript, this test fails. That is the whole point:
the engine may change, the contract's consumers may not — silently.

Regenerate goldens intentionally with:  UPDATE_GOLDENS=1 pytest tests/parity/test_caption_parity.py
Goldens use the .ass.expected extension because *.ass is gitignored.
"""

import json
import os
import sys
import tempfile

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
BACKEND_ROOT = os.path.join(ROOT, "backend")
if BACKEND_ROOT not in sys.path:
    sys.path.insert(0, BACKEND_ROOT)

import services.caption_renderer as caption_renderer  # noqa: E402
from services.caption_renderer import render_captions, _sanitize_words  # noqa: E402

HERE = os.path.dirname(__file__)
GOLDEN_DIR = os.path.join(HERE, "golden")
TRANSCRIPT = os.path.join(HERE, "transcript_synthetic.json")
STYLES = ["hormozi", "karaoke", "subtle", "branded"]


def _deterministic_text_widths(texts, font_name, font_size, bold, spacing=2):
    """A host-font-independent stand-in for _measure_text_widths.

    Real width measurement resolves the host's font via fc-match and freetype,
    so positions differ between macOS (Arial) and CI (DejaVu/Liberation) and the
    goldens can never match across machines. The parity harness exists to lock
    the engine-independent ASS pipeline (timing, styles, karaoke, position and
    pill geometry derived from widths) — not the host's typography — so we pin a
    deterministic width model and the goldens become reproducible everywhere.
    """
    widths = []
    for t in texts:
        n = len(t)
        if n == 0:
            widths.append(0)
            continue
        widths.append(round(n * font_size * 0.5) + spacing * max(0, n - 1))
    return widths


def _load_words():
    with open(TRANSCRIPT, encoding="utf-8") as f:
        return json.load(f)["words"]


def _render(style: str) -> str:
    words = _load_words()
    fd, out = tempfile.mkstemp(suffix=".ass")
    os.close(fd)
    try:
        render_captions(words, style, out, time_offset=0.0)
        with open(out, encoding="utf-8") as f:
            return f.read()
    finally:
        if os.path.exists(out):
            os.remove(out)


@pytest.fixture(autouse=True)
def _hermetic_render(monkeypatch):
    """Pin the two host-dependent inputs so goldens are reproducible everywhere.

    1. Font name: caption_styles.DETECTED_FONT is resolved at import via fc-list,
       so it's "Arial" on macOS and "Liberation Sans" on CI's Ubuntu — and that
       name is written into the ASS Style line.
    2. Text widths: see _deterministic_text_widths (affects the branded style's
       per-word positioning and pill geometry).
    """
    monkeypatch.setattr(caption_renderer, "_measure_text_widths", _deterministic_text_widths)

    real_get_style = caption_renderer.get_style

    def _pinned_get_style(name):
        style = dict(real_get_style(name))
        style["font_name"] = "Arial"
        return style

    monkeypatch.setattr(caption_renderer, "get_style", _pinned_get_style)


@pytest.mark.parametrize("style", STYLES)
def test_caption_output_matches_golden(style):
    produced = _render(style)
    golden_path = os.path.join(GOLDEN_DIR, f"{style}.ass.expected")

    if os.environ.get("UPDATE_GOLDENS") == "1":
        os.makedirs(GOLDEN_DIR, exist_ok=True)
        with open(golden_path, "w", encoding="utf-8") as f:
            f.write(produced)
        pytest.skip(f"golden updated: {style}")

    assert os.path.exists(golden_path), (
        f"missing golden for {style}. Generate with UPDATE_GOLDENS=1."
    )
    with open(golden_path, encoding="utf-8") as f:
        expected = f.read()
    assert produced == expected, (
        f"caption output for '{style}' diverged from golden. "
        f"If this is an intended change, regenerate with UPDATE_GOLDENS=1."
    )


def test_word_text_normalization():
    """The exact normalization the whisper.cpp boundary must reproduce.

    whisper.cpp emits leading-space token markers and can emit empty/whitespace
    tokens; corrections + caption spacing match on stripped word text. If the
    new engine's word text isn't normalized identically here, captions and
    apply_corrections() silently diverge. This is the single highest-risk
    integration detail in the engine swap.
    """
    words = _load_words()
    cleaned = _sanitize_words(words)
    texts = [w["word"] for w in cleaned]

    # leading space stripped
    assert texts[0] == "The"
    # whitespace-only token dropped entirely
    assert "" not in texts
    assert all(t.strip() == t and t for t in texts)
    # punctuation / apostrophe / number+symbol survive verbatim
    assert "billion." in texts
    assert "Wasn't" in texts
    assert "$10" in texts
    assert "expensive?" in texts
    # exactly one token (the whitespace-only one) was dropped
    assert len(cleaned) == len(words) - 1


def test_zero_duration_word_gets_floor():
    """A token with end <= start must be widened to the 50ms floor, never
    crash or render a zero/negative-length event (a real whisper.cpp quirk)."""
    words = _load_words()
    cleaned = _sanitize_words(words)
    for w in cleaned:
        assert w["end"] > w["start"]
        assert (w["end"] - w["start"]) >= 0.05 - 1e-9
