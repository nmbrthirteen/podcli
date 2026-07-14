"""Tests for backend.services.caption_renderer chunking and event volume."""

import os
import sys
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BACKEND_ROOT = os.path.join(ROOT, "backend")
if BACKEND_ROOT not in sys.path:
    sys.path.insert(0, BACKEND_ROOT)

from services import caption_renderer as cr
from config.caption_styles import get_style


def _word(text, start, end, speaker=None):
    return {"word": text, "start": start, "end": end, "speaker": speaker}


def _flowing_words(texts, start=0.0, dur=0.3, speaker=None):
    words = []
    t = start
    for text in texts:
        words.append(_word(text, t, t + dur, speaker))
        t += dur
    return words


class ChunkWordsTests(unittest.TestCase):
    def test_splits_on_word_count(self):
        words = _flowing_words(["a", "b", "c", "d", "e", "f", "g", "h", "i", "j"])
        chunks = cr._chunk_words(words, 4)
        self.assertEqual([len(c) for c in chunks], [4, 4, 2])

    def test_breaks_after_terminal_punctuation(self):
        words = _flowing_words(["That", "was", "wild.", "Then", "we", "left"])
        chunks = cr._chunk_words(words, 6)
        self.assertEqual([len(c) for c in chunks], [3, 3])
        self.assertEqual(chunks[0][-1]["word"], "wild.")

    def test_breaks_on_question_and_exclamation(self):
        words = _flowing_words(["Really?", "Yes!", "Okay"])
        chunks = cr._chunk_words(words, 6)
        self.assertEqual([len(c) for c in chunks], [1, 1, 1])

    def test_breaks_on_long_gap(self):
        words = [
            _word("before", 0.0, 0.3),
            _word("pause", 0.3, 0.6),
            _word("after", 1.6, 1.9),  # 1.0s gap
        ]
        chunks = cr._chunk_words(words, 6)
        self.assertEqual([len(c) for c in chunks], [2, 1])

    def test_short_gap_does_not_break(self):
        words = [
            _word("quick", 0.0, 0.3),
            _word("pause", 0.9, 1.2),  # 0.6s gap
        ]
        chunks = cr._chunk_words(words, 6)
        self.assertEqual([len(c) for c in chunks], [2])

    def test_breaks_on_speaker_change(self):
        words = (
            _flowing_words(["so", "anyway"], start=0.0, speaker="SPEAKER_00")
            + _flowing_words(["totally", "agree"], start=0.6, speaker="SPEAKER_01")
        )
        chunks = cr._chunk_words(words, 6)
        self.assertEqual([len(c) for c in chunks], [2, 2])
        self.assertEqual(chunks[1][0]["word"], "totally")

    def test_missing_speaker_labels_do_not_break(self):
        words = _flowing_words(["one", "two", "three"])
        self.assertEqual(len(cr._chunk_words(words, 6)), 1)

    def test_empty_words(self):
        self.assertEqual(cr._chunk_words([], 4), [])


class BrandedEventVolumeTests(unittest.TestCase):
    def test_events_are_linear_in_word_count(self):
        style = get_style("branded")
        chunk_size = style.get("words_per_chunk", 6)
        words = _flowing_words(["word"] * chunk_size)
        content = cr._render_branded(words, style, 0.0)
        dialogue_count = content.count("Dialogue:")
        # One text event per word for the chunk span + one pill per word.
        self.assertEqual(dialogue_count, 2 * chunk_size)

    def test_text_events_span_the_whole_chunk(self):
        style = get_style("branded")
        words = _flowing_words(["alpha", "beta", "gamma"])
        content = cr._render_branded(words, style, 0.0)
        layer1 = [l for l in content.splitlines() if l.startswith("Dialogue: 1,")]
        starts = {l.split(",")[1] for l in layer1}
        ends = {l.split(",")[2] for l in layer1}
        self.assertEqual(len(starts), 1)
        self.assertEqual(len(ends), 1)


if __name__ == "__main__":
    unittest.main()
