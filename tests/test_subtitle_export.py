"""Tests for backend.services.subtitle_export: SRT/VTT sidecar generation."""

import os
import sys
import tempfile
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BACKEND_ROOT = os.path.join(ROOT, "backend")
if BACKEND_ROOT not in sys.path:
    sys.path.insert(0, BACKEND_ROOT)

from services import subtitle_export as se


def _word(text, start, end):
    return {"word": text, "start": start, "end": end}


class TimestampTests(unittest.TestCase):
    def test_srt_timestamp_format(self):
        self.assertEqual(se._timestamp(65.25, vtt=False), "00:01:05,250")

    def test_vtt_timestamp_format(self):
        self.assertEqual(se._timestamp(65.25, vtt=True), "00:01:05.250")

    def test_rounds_milliseconds_without_overflow(self):
        # 1.9996 * 1000 = 1999.6 -> rounds to 2000ms, carried into seconds,
        # never the invalid "01,1000".
        self.assertEqual(se._timestamp(1.9996, vtt=False), "00:00:02,000")

    def test_negative_seconds_clamped_to_zero(self):
        self.assertEqual(se._timestamp(-0.5, vtt=False), "00:00:00,000")


class GroupWordsTests(unittest.TestCase):
    def test_groups_by_words_per_line(self):
        words = [_word(f"w{i}", i, i + 0.5) for i in range(10)]
        groups = se._group_words(words, words_per_line=4)
        self.assertEqual([len(g["text"].split()) for g in groups], [4, 4, 2])

    def test_group_spans_first_start_to_last_end(self):
        words = [_word("a", 1.0, 1.5), _word("b", 1.5, 2.5), _word("c", 2.5, 3.0)]
        groups = se._group_words(words, words_per_line=3)
        self.assertEqual(groups[0]["start"], 1.0)
        self.assertEqual(groups[0]["end"], 3.0)

    def test_skips_blank_words(self):
        words = [_word("a", 0, 1), _word("", 1, 1), _word("b", 1, 2)]
        groups = se._group_words(words, words_per_line=8)
        self.assertEqual(groups[0]["text"], "a b")

    def test_empty_input_yields_no_groups(self):
        self.assertEqual(se._group_words([], words_per_line=8), [])


class WordsToSrtVttTests(unittest.TestCase):
    def test_words_to_srt_basic(self):
        words = [_word("Hello", 0.0, 0.5), _word("world", 0.5, 1.0)]
        srt = se.words_to_srt(words, words_per_line=8)
        self.assertEqual(
            srt,
            "1\n00:00:00,000 --> 00:00:01,000\nHello world\n",
        )

    def test_words_to_vtt_basic(self):
        words = [_word("Hello", 0.0, 0.5), _word("world", 0.5, 1.0)]
        vtt = se.words_to_vtt(words, words_per_line=8)
        self.assertEqual(
            vtt,
            "WEBVTT\n\n1\n00:00:00.000 --> 00:00:01.000\nHello world\n",
        )

    def test_no_words_returns_none(self):
        self.assertIsNone(se.words_to_srt([]))
        self.assertIsNone(se.words_to_vtt([]))

    def test_multiple_lines_are_numbered_in_order(self):
        words = [_word(f"w{i}", i, i + 0.5) for i in range(5)]
        srt = se.words_to_srt(words, words_per_line=2)
        self.assertIn("1\n", srt)
        self.assertIn("2\n", srt)
        self.assertIn("3\n", srt)


class WriteSidecarsTests(unittest.TestCase):
    def test_writes_both_files_and_returns_their_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = os.path.join(tmp, "clip")
            words = [_word("Hello", 0.0, 0.5), _word("world", 0.5, 1.0)]
            paths = se.write_sidecars(words, base)

            self.assertEqual(paths["srt_path"], f"{base}.srt")
            self.assertEqual(paths["vtt_path"], f"{base}.vtt")
            self.assertTrue(os.path.exists(paths["srt_path"]))
            self.assertTrue(os.path.exists(paths["vtt_path"]))
            with open(paths["srt_path"], encoding="utf-8") as f:
                self.assertIn("Hello world", f.read())

    def test_writes_nothing_when_there_are_no_words(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = os.path.join(tmp, "clip")
            paths = se.write_sidecars([], base)
            self.assertEqual(paths, {})
            self.assertFalse(os.path.exists(f"{base}.srt"))
            self.assertFalse(os.path.exists(f"{base}.vtt"))


if __name__ == "__main__":
    unittest.main()
