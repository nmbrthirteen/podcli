"""Tests for backend.services.corrections — word/segment replacement."""

import json
import os
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BACKEND_ROOT = os.path.join(ROOT, "backend")
if BACKEND_ROOT not in sys.path:
    sys.path.insert(0, BACKEND_ROOT)

from services import corrections


class CorrectionsTests(unittest.TestCase):
    def setUp(self):
        # Redirect the corrections path to a fresh temp file per test
        self.tmpdir = tempfile.mkdtemp(prefix="podcli-corr-test-")
        self.corrections_file = os.path.join(self.tmpdir, "corrections.json")
        self._patcher = mock.patch.object(
            corrections, "_CORRECTIONS_PATH", self.corrections_file
        )
        self._patcher.start()

    def tearDown(self):
        self._patcher.stop()
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _set(self, mapping: dict):
        with open(self.corrections_file, "w") as f:
            json.dump(mapping, f)

    def test_get_corrections_empty_when_no_file(self):
        self.assertEqual(corrections.get_corrections(), {})

    def test_save_and_get_round_trip(self):
        corrections.save_corrections({"Boxel": "Voxel"})
        self.assertEqual(corrections.get_corrections(), {"Boxel": "Voxel"})

    def test_load_tolerates_corrupt_json(self):
        with open(self.corrections_file, "w") as f:
            f.write("{not valid")
        self.assertEqual(corrections.get_corrections(), {})

    def test_load_ignores_non_dict_payload(self):
        with open(self.corrections_file, "w") as f:
            json.dump(["not", "a", "dict"], f)
        self.assertEqual(corrections.get_corrections(), {})

    def test_apply_corrections_no_op_when_empty(self):
        words = [{"word": "hello"}]
        segments = [{"text": "hello world"}]
        w, s = corrections.apply_corrections(words, segments)
        self.assertEqual(w[0]["word"], "hello")
        self.assertEqual(s[0]["text"], "hello world")

    def test_apply_corrections_replaces_word(self):
        self._set({"Boxel": "Voxel"})
        words = [{"word": "Boxel"}, {"word": "world"}]
        segments = [{"text": "A Boxel appears"}]
        w, s = corrections.apply_corrections(words, segments)
        self.assertEqual(w[0]["word"], "Voxel")
        self.assertEqual(s[0]["text"], "A Voxel appears")

    def test_apply_corrections_case_insensitive_match(self):
        self._set({"grub": "GRU"})
        words = [{"word": "Grub"}]
        segments = [{"text": "the grub arrived"}]
        w, s = corrections.apply_corrections(words, segments)
        # Regex is case-insensitive and applies replacement as-is
        self.assertEqual(w[0]["word"], "GRU")
        self.assertEqual(s[0]["text"], "the GRU arrived")

    def test_apply_corrections_preserves_punctuation(self):
        self._set({"Boxel": "Voxel"})
        words = [{"word": "Boxel,"}]
        _, _ = corrections.apply_corrections(words, [])
        self.assertEqual(words[0]["word"], "Voxel,")

    def test_apply_corrections_longest_match_wins(self):
        # "open AI" (multi-word) should beat "AI" in segment replacement
        self._set({"open AI": "OpenAI", "AI": "A.I."})
        segments = [{"text": "I love open AI but also AI"}]
        _, s = corrections.apply_corrections([], segments)
        self.assertEqual(s[0]["text"], "I love OpenAI but also A.I.")

    def test_apply_corrections_word_boundary(self):
        # "AI" should not match inside "maintain"
        self._set({"AI": "A.I."})
        segments = [{"text": "we maintain AI systems"}]
        _, s = corrections.apply_corrections([], segments)
        self.assertEqual(s[0]["text"], "we maintain A.I. systems")

    def test_apply_corrections_merges_multiword_word_run(self):
        # "open AI" split across two words must merge into one caption word,
        # not just fix the segment text and leave "open"/"AI" as-is.
        self._set({"open AI": "OpenAI"})
        words = [
            {"word": "open", "start": 1.0, "end": 1.4, "speaker": "SPEAKER_00"},
            {"word": "AI", "start": 1.4, "end": 1.8, "speaker": "SPEAKER_00"},
        ]
        segments = [{"text": "I love open AI"}]
        w, s = corrections.apply_corrections(words, segments)
        self.assertEqual([x["word"] for x in w], ["OpenAI"])
        self.assertAlmostEqual(w[0]["start"], 1.0)
        self.assertAlmostEqual(w[0]["end"], 1.8)
        self.assertEqual(w[0]["speaker"], "SPEAKER_00")
        self.assertEqual(s[0]["text"], "I love OpenAI")

    def test_apply_corrections_multiword_merge_is_case_insensitive(self):
        self._set({"open AI": "OpenAI"})
        words = [
            {"word": "Open", "start": 0.0, "end": 0.5},
            {"word": "ai", "start": 0.5, "end": 1.0},
        ]
        w, _ = corrections.apply_corrections(words, [])
        self.assertEqual([x["word"] for x in w], ["OpenAI"])

    def test_apply_corrections_multiword_merge_ignores_punctuation(self):
        self._set({"open AI": "OpenAI"})
        words = [
            {"word": "open", "start": 0.0, "end": 0.5},
            {"word": "AI.", "start": 0.5, "end": 1.0},
        ]
        w, _ = corrections.apply_corrections(words, [])
        # Punctuation is ignored for matching, but the last word's trailing
        # punctuation still belongs on the merged word: "open AI." should
        # read as "OpenAI.", not drop the sentence end.
        self.assertEqual([x["word"] for x in w], ["OpenAI."])

    def test_apply_corrections_multiword_merge_carries_last_words_trailing_punctuation(self):
        self._set({"open AI": "OpenAI"})
        words = [
            {"word": "open", "start": 1.0, "end": 1.4, "speaker": "SPEAKER_00", "confidence": 0.9},
            {"word": "AI.", "start": 1.4, "end": 1.8, "speaker": "SPEAKER_01", "confidence": 0.4},
        ]
        w, _ = corrections.apply_corrections(words, [])
        self.assertEqual([x["word"] for x in w], ["OpenAI."])
        # First word's other fields carry onto the merged word...
        self.assertEqual(w[0]["speaker"], "SPEAKER_00")
        # ...except confidence, which takes the minimum across the run
        # rather than silently reporting the first word's (possibly higher)
        # confidence for a merge that includes a less-confident word.
        self.assertAlmostEqual(w[0]["confidence"], 0.4)

    def test_apply_corrections_multiword_merge_multi_word_replacement_keeps_trailing_punctuation(self):
        self._set({"open ai": "Open AI"})
        words = [
            {"word": "open", "start": 0.0, "end": 1.0},
            {"word": "ai.", "start": 1.0, "end": 2.0},
        ]
        w, _ = corrections.apply_corrections(words, [])
        # The trailing punctuation lands on the final replacement word only.
        self.assertEqual([x["word"] for x in w], ["Open", "AI."])

    def test_apply_corrections_multiword_replacement_splits_time_across_words(self):
        # A multi-word replacement distributes the merged span evenly across
        # its own word count, rather than collapsing into a single word.
        self._set({"open ai": "Open AI"})
        words = [
            {"word": "open", "start": 0.0, "end": 1.0},
            {"word": "ai", "start": 1.0, "end": 2.0},
        ]
        w, _ = corrections.apply_corrections(words, [])
        self.assertEqual([x["word"] for x in w], ["Open", "AI"])
        self.assertAlmostEqual(w[0]["start"], 0.0)
        self.assertAlmostEqual(w[0]["end"], 1.0)
        self.assertAlmostEqual(w[1]["start"], 1.0)
        self.assertAlmostEqual(w[1]["end"], 2.0)

    def test_apply_corrections_multiword_longest_match_wins_in_words(self):
        self._set({"open AI": "OpenAI", "AI": "A.I."})
        words = [
            {"word": "open", "start": 0.0, "end": 0.5},
            {"word": "AI", "start": 0.5, "end": 1.0},
        ]
        w, _ = corrections.apply_corrections(words, [])
        self.assertEqual([x["word"] for x in w], ["OpenAI"])

    def test_apply_corrections_leaves_non_matching_words_alone(self):
        self._set({"open AI": "OpenAI"})
        words = [
            {"word": "I", "start": 0.0, "end": 0.2},
            {"word": "love", "start": 0.2, "end": 0.5},
            {"word": "open", "start": 0.5, "end": 0.8},
            {"word": "AI", "start": 0.8, "end": 1.0},
        ]
        w, _ = corrections.apply_corrections(words, [])
        self.assertEqual([x["word"] for x in w], ["I", "love", "OpenAI"])

    def test_apply_corrections_returns_same_list_objects(self):
        self._set({"Foo": "Bar"})
        words = [{"word": "Foo"}]
        segments = [{"text": "Foo"}]
        w, s = corrections.apply_corrections(words, segments)
        # Same list identity — mutated in place
        self.assertIs(w, words)
        self.assertIs(s, segments)


if __name__ == "__main__":
    unittest.main()
