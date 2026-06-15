import os
import sys
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BACKEND_ROOT = os.path.join(ROOT, "backend")
if BACKEND_ROOT not in sys.path:
    sys.path.insert(0, BACKEND_ROOT)

from services.transcription_whispercpp import _tokens_to_words


class WhisperCppAdapterTests(unittest.TestCase):
    def test_sentencepiece_marker_is_removed(self):
        words = _tokens_to_words([
            {"text": "▁hello", "offsets": {"from": 0, "to": 100}},
            {"text": "▁world", "offsets": {"from": 100, "to": 200}},
        ])
        self.assertEqual([w["word"] for w in words], ["hello", "world"])


if __name__ == "__main__":
    unittest.main()
