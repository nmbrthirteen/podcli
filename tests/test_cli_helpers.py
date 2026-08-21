"""Tests for backend.cli helpers: the shared WAV cache and Studio port resolution."""

import os
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BACKEND_ROOT = os.path.join(ROOT, "backend")
if BACKEND_ROOT not in sys.path:
    sys.path.insert(0, BACKEND_ROOT)

import cli as cli_mod
from services import audio_extract


class SharedWavTests(unittest.TestCase):
    def test_successful_extraction_is_reused(self):
        fd, wav_path = tempfile.mkstemp(suffix=".wav")
        os.close(fd)
        try:
            with mock.patch.object(
                audio_extract, "extract_wav_16k_mono", return_value=wav_path
            ) as extract:
                shared = cli_mod._SharedWav("/video.mp4")
                self.assertEqual(shared.get(), wav_path)
                self.assertEqual(shared.get(), wav_path)
            self.assertEqual(extract.call_count, 1)
        finally:
            if os.path.exists(wav_path):
                os.unlink(wav_path)

    def test_failed_extraction_is_attempted_once(self):
        with mock.patch.object(
            audio_extract, "extract_wav_16k_mono", side_effect=RuntimeError("decode failed")
        ) as extract:
            shared = cli_mod._SharedWav("/video.mp4")
            for _ in range(4):
                self.assertIsNone(shared.get())

        self.assertEqual(extract.call_count, 1)
        self.assertIsInstance(shared.error, RuntimeError)

    def test_empty_result_is_treated_as_failure(self):
        with mock.patch.object(
            audio_extract, "extract_wav_16k_mono", return_value=None
        ) as extract:
            shared = cli_mod._SharedWav("/video.mp4")
            self.assertIsNone(shared.get())
            self.assertIsNone(shared.get())

        self.assertEqual(extract.call_count, 1)


if __name__ == "__main__":
    unittest.main()


class SelectionSignatureTests(unittest.TestCase):
    """The suggestions cache is only allowed to replay picks made under the
    same rules."""

    def _sig(self, config, kb="kb-a"):
        with mock.patch("services.claude_suggest.kb_signature", return_value=kb):
            return cli_mod._selection_signature(config)

    def test_editing_the_knowledge_base_invalidates_an_ai_session(self):
        cfg = {"ai_select": True}
        self.assertNotEqual(self._sig(cfg, kb="kb-a"), self._sig(cfg, kb="kb-b"))

    def test_editing_the_knowledge_base_leaves_a_saliency_session_alone(self):
        cfg = {"ai_select": False}
        self.assertEqual(self._sig(cfg, kb="kb-a"), self._sig(cfg, kb="kb-b"))

    def test_changing_profile_invalidates(self):
        a = self._sig({"ai_select": True, "profile": "party"})
        b = self._sig({"ai_select": True})
        self.assertNotEqual(a, b)

    def test_turning_energy_off_invalidates(self):
        a = self._sig({"ai_select": True, "energy_boost": False})
        b = self._sig({"ai_select": True, "energy_boost": True})
        self.assertNotEqual(a, b)

    def test_an_unreadable_knowledge_base_does_not_raise(self):
        with mock.patch("services.claude_suggest.kb_signature", side_effect=OSError("boom")):
            self.assertIn("kb-unavailable", cli_mod._selection_signature({"ai_select": True}))

    def test_two_failed_lookups_never_compare_equal(self):
        # Otherwise a session saved during one failure replays during the next,
        # after the knowledge base has changed underneath it.
        with mock.patch("services.claude_suggest.kb_signature", side_effect=OSError("boom")):
            a = cli_mod._selection_signature({"ai_select": True})
            b = cli_mod._selection_signature({"ai_select": True})
        self.assertNotEqual(a, b)
