"""Tests for backend.services.engine_comparison: disagreement scoring and report output."""

import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BACKEND_ROOT = os.path.join(ROOT, "backend")
if BACKEND_ROOT not in sys.path:
    sys.path.insert(0, BACKEND_ROOT)

from services import engine_comparison as ec


class NormalizeWordTests(unittest.TestCase):
    def test_strips_punctuation(self):
        self.assertEqual(ec.normalize_word("hello,"), "hello")

    def test_casefolds(self):
        self.assertEqual(ec.normalize_word("HELLO"), "hello")

    def test_nfkc_normalizes(self):
        # Fullwidth "A" (U+FF21) NFKC-normalizes to ASCII "a" after casefold.
        self.assertEqual(ec.normalize_word("Ａ"), "a")

    def test_keeps_marks_and_numbers(self):
        self.assertEqual(ec.normalize_word("café2"), "café2")

    def test_empty_punctuation_only_becomes_empty_string(self):
        self.assertEqual(ec.normalize_word("..."), "")


class WordLevenshteinTests(unittest.TestCase):
    def test_identical_sequences(self):
        self.assertEqual(ec.word_levenshtein(["a", "b"], ["a", "b"]), 0)

    def test_one_substitution(self):
        self.assertEqual(ec.word_levenshtein(["a", "b", "c"], ["a", "x", "c"]), 1)

    def test_one_insertion(self):
        self.assertEqual(ec.word_levenshtein(["a", "c"], ["a", "b", "c"]), 1)

    def test_against_empty(self):
        self.assertEqual(ec.word_levenshtein([], ["a", "b"]), 2)
        self.assertEqual(ec.word_levenshtein(["a", "b"], []), 2)


class DisagreementRatioTests(unittest.TestCase):
    def test_identical_text_is_zero(self):
        self.assertEqual(ec.disagreement_ratio("hello world", "hello world"), 0.0)

    def test_punctuation_and_case_do_not_count_as_disagreement(self):
        self.assertEqual(ec.disagreement_ratio("Hello, World!", "hello world"), 0.0)

    def test_one_word_different_out_of_two(self):
        self.assertEqual(ec.disagreement_ratio("hello world", "hello word"), 0.5)

    def test_completely_different_short_texts(self):
        self.assertEqual(ec.disagreement_ratio("hello", "goodbye"), 1.0)

    def test_both_empty_is_zero_not_a_crash(self):
        self.assertEqual(ec.disagreement_ratio("", ""), 0.0)


class BuildWindowsTests(unittest.TestCase):
    def test_splits_into_fixed_windows_and_flags_both_empty(self):
        words_a = [{"word": "hello", "start": 1.0, "end": 1.5}]
        words_b = [{"word": "hallo", "start": 1.0, "end": 1.5}]
        windows = ec.build_windows(words_a, words_b, total_duration=45.0, window_seconds=20.0)
        self.assertEqual(len(windows), 3)
        self.assertEqual(windows[0]["start"], 0.0)
        self.assertEqual(windows[0]["end"], 20.0)
        self.assertFalse(windows[0]["both_empty"])
        self.assertTrue(windows[1]["both_empty"])  # 20-40s: no words from either
        self.assertEqual(windows[1]["disagreement"], 0.0)

    def test_word_assigned_to_window_holding_its_midpoint(self):
        # Word spans 19-21s, straddling the 20s window boundary; its
        # midpoint (20.0) belongs to the second window.
        words_a = [{"word": "straddle", "start": 19.0, "end": 21.0}]
        windows = ec.build_windows(words_a, [], total_duration=40.0, window_seconds=20.0)
        self.assertEqual(windows[0]["text_a"], "")
        self.assertEqual(windows[1]["text_a"], "straddle")


class CompareEnginesTests(unittest.TestCase):
    def _fake_transcribe(self, words_by_engine):
        def fn(file_path, model_size=None, engine=None, language=None,
               enable_diarization=None, start_seconds=None, duration_seconds=None):
            words = words_by_engine[engine]
            duration = max((w["end"] for w in words), default=0.0)
            return {"words": words, "duration": duration, "engine": engine}
        return fn

    def test_compares_two_engines_and_scores_overall_disagreement(self):
        fake = self._fake_transcribe({
            "whispercpp": [{"word": "hello", "start": 0.0, "end": 0.5}],
            "omnilingual": [{"word": "hallo", "start": 0.0, "end": 0.5}],
        })
        report = ec.compare_engines(
            "/fake/video.mp4", "whispercpp", "omnilingual",
            duration_seconds=20.0, transcribe_fn=fake,
        )
        self.assertEqual(report["engine_a"], "whispercpp")
        self.assertEqual(report["engine_b"], "omnilingual")
        self.assertEqual(len(report["windows"]), 1)
        self.assertEqual(report["overall_disagreement"], 1.0)
        self.assertEqual(report["note"], "disagreement between the two engines' output, not accuracy against a transcript")

    def test_identical_output_scores_zero_disagreement(self):
        fake = self._fake_transcribe({
            "whispercpp": [{"word": "hello", "start": 0.0, "end": 0.5}],
            "omnilingual": [{"word": "hello", "start": 0.0, "end": 0.5}],
        })
        report = ec.compare_engines(
            "/fake/video.mp4", "whispercpp", "omnilingual",
            duration_seconds=20.0, transcribe_fn=fake,
        )
        self.assertEqual(report["overall_disagreement"], 0.0)

    def test_writes_json_and_html_when_output_dir_given(self):
        fake = self._fake_transcribe({
            "whispercpp": [{"word": "hello", "start": 0.0, "end": 0.5}],
            "omnilingual": [{"word": "hallo", "start": 0.0, "end": 0.5}],
        })
        with tempfile.TemporaryDirectory() as tmp:
            report = ec.compare_engines(
                "/fake/video.mp4", "whispercpp", "omnilingual",
                duration_seconds=20.0, transcribe_fn=fake, output_dir=tmp,
            )
            self.assertTrue(os.path.exists(report["json_path"]))
            self.assertTrue(os.path.exists(report["html_path"]))
            with open(report["json_path"], encoding="utf-8") as f:
                on_disk = json.load(f)
            self.assertEqual(on_disk["overall_disagreement"], report["overall_disagreement"])


class RenderHtmlEscapingTests(unittest.TestCase):
    def _report(self, text_a, text_b):
        return {
            "engine_a": "whispercpp",
            "engine_b": "omnilingual",
            "file_path": "/fake/video.mp4",
            "overall_disagreement": 0.5,
            "windows": [
                {"start": 0.0, "end": 20.0, "text_a": text_a, "text_b": text_b,
                 "both_empty": False, "disagreement": 0.5},
            ],
        }

    def test_script_closing_tag_in_transcript_text_is_neutralized(self):
        html_out = ec.render_html(self._report("</script><script>alert(1)</script>", "normal"), None)
        self.assertNotIn("</script><script>alert(1)</script>", html_out)
        # The data must still round-trip through JS correctly. Check the
        # escaped marker is present instead of a raw closing tag.
        self.assertIn("\\u003c/script", html_out)

    def test_script_closing_tag_with_mixed_case_is_also_neutralized(self):
        # A case-sensitive "</script" replacement lets "</SCRIPT>" (or any
        # other casing) through, since HTML tag matching is case-insensitive
        # but a literal string replace is not.
        html_out = ec.render_html(
            self._report("</SCRIPT><img onerror=alert(1)>", "normal"), None
        )
        self.assertNotIn("</SCRIPT><img onerror=alert(1)>", html_out)
        self.assertIn("\\u003c/SCRIPT>\\u003cimg onerror=alert(1)>", html_out)

    def test_html_tags_in_engine_names_are_escaped(self):
        report = self._report("hello", "world")
        report["engine_a"] = "<img src=x onerror=alert(1)>"
        html_out = ec.render_html(report, None)
        self.assertNotIn("<img src=x onerror=alert(1)>", html_out)
        self.assertIn("&lt;img", html_out)

    def test_no_audio_renders_placeholder_not_a_broken_tag(self):
        html_out = ec.render_html(self._report("a", "b"), None)
        self.assertIn("No sample audio", html_out)
        self.assertNotIn("<audio", html_out)

    def test_audio_path_is_wired_into_audio_tag(self):
        html_out = ec.render_html(self._report("a", "b"), "sample.wav")
        self.assertIn('src="sample.wav"', html_out)


class JsonForScriptTagTests(unittest.TestCase):
    def test_escapes_line_separator_and_paragraph_separator(self):
        # U+2028/U+2029 are valid JSON string characters but some JS engines
        # treat them as line terminators even inside a string literal, which
        # can truncate the embedded JSON mid-statement.
        out = ec._json_for_script_tag({"text": "line one line two line three"})
        self.assertNotIn(" ", out)
        self.assertNotIn(" ", out)
        self.assertIn("\\u2028", out)
        self.assertIn("\\u2029", out)


if __name__ == "__main__":
    unittest.main()
