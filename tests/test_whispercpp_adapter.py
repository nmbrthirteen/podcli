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

from services.transcription_whispercpp import (
    _dtw_preset_for_model,
    _tokens_to_words,
    transcribe_file,
)


class WhisperCppAdapterTests(unittest.TestCase):
    def test_sentencepiece_marker_is_removed(self):
        words = _tokens_to_words([
            {"text": "▁hello", "offsets": {"from": 0, "to": 100}},
            {"text": "▁world", "offsets": {"from": 100, "to": 200}},
        ])
        self.assertEqual([w["word"] for w in words], ["hello", "world"])


class LanguageHandlingTests(unittest.TestCase):
    """whisper-cli defaults to English decoding when -l is omitted entirely, so
    an unset language must become "-l auto" rather than no flag at all; the
    reported language must come from whisper-cli's detected result, not the
    (possibly "auto") param we passed in."""

    def _fake_run(self, result_language, params_language):
        def run(cmd, **kwargs):
            of_index = cmd.index("-of")
            out_base = cmd[of_index + 1]
            payload = {
                "params": {"language": params_language},
                "result": {"language": result_language},
                "transcription": [],
            }
            with open(out_base + ".json", "w", encoding="utf-8") as f:
                json.dump(payload, f)
            return mock.Mock(returncode=0)

        return mock.Mock(side_effect=run)

    def _run_transcribe(self, language, result_language, params_language):
        with tempfile.NamedTemporaryFile(suffix=".mp3") as media, \
             tempfile.NamedTemporaryFile(suffix=".bin") as model, \
             tempfile.NamedTemporaryFile(suffix=".wav") as wav:
            fake_run = self._fake_run(result_language, params_language)
            with mock.patch("services.transcription_whispercpp.subprocess.run", fake_run):
                result = transcribe_file(
                    media.name,
                    model.name,
                    language=language,
                    wav_path=wav.name,
                )
            cmd = fake_run.call_args[0][0]
            return result, cmd

    def test_unset_language_passes_auto(self):
        _, cmd = self._run_transcribe(None, "es", "auto")
        self.assertIn("-l", cmd)
        self.assertEqual(cmd[cmd.index("-l") + 1], "auto")

    def test_explicit_language_is_passed_through(self):
        _, cmd = self._run_transcribe("fr", "fr", "fr")
        self.assertEqual(cmd[cmd.index("-l") + 1], "fr")

    def test_label_prefers_detected_result_language_over_param(self):
        # Requesting auto-detect but whisper-cli actually detects Spanish: the
        # output must be labeled "es", never the "auto" we passed as -l.
        result, _ = self._run_transcribe(None, "es", "auto")
        self.assertEqual(result["language"], "es")


class DtwPresetTests(unittest.TestCase):
    def test_preset_tracks_the_model_file(self):
        cases = {
            "ggml-tiny.en.bin": "tiny.en",
            "ggml-base.bin": "base",
            "ggml-small.bin": "small",
            "ggml-large-v3-turbo.bin": "large.v3-turbo",
            "ggml-base.en-q5_1.bin": "base.en",
            # K-quantisation names carry underscores between the qualifiers, and
            # failing to strip them dropped -dtw for a model that supports it.
            "ggml-large-v3-q4_k_m.gguf": "large.v3",
            "ggml-small.en-q8_0.bin": "small.en",
        }
        for name, preset in cases.items():
            with self.subTest(name=name):
                self.assertEqual(_dtw_preset_for_model(os.path.join("/models", name)), preset)

    def test_unknown_model_gets_no_preset(self):
        self.assertIsNone(_dtw_preset_for_model("/models/ggml-distil-large-v2.bin"))


if __name__ == "__main__":
    unittest.main()
