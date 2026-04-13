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
