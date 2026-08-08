import os
import sys
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BACKEND_ROOT = os.path.join(ROOT, "backend")
if BACKEND_ROOT not in sys.path:
    sys.path.insert(0, BACKEND_ROOT)

from services.transcription_whispercpp import _dtw_preset_for_model, _tokens_to_words


class WhisperCppAdapterTests(unittest.TestCase):
    def test_sentencepiece_marker_is_removed(self):
        words = _tokens_to_words([
            {"text": "▁hello", "offsets": {"from": 0, "to": 100}},
            {"text": "▁world", "offsets": {"from": 100, "to": 200}},
        ])
        self.assertEqual([w["word"] for w in words], ["hello", "world"])


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
