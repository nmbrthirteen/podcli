"""Energy-snap for whisper.cpp word timings: trailing words stranded in true
silence are pulled back into the voiced span, while words over speech are left
alone. (whisper.cpp sometimes stretches the final phrase across trailing
silence; this keeps captions in sync.)"""

import math
import os
import struct
import sys
import tempfile
import unittest
import wave

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BACKEND_ROOT = os.path.join(ROOT, "backend")
if BACKEND_ROOT not in sys.path:
    sys.path.insert(0, BACKEND_ROOT)

from services.transcription_whispercpp import _snap_words_to_voiced, _voiced_intervals


def _make_wav(path, voiced_s=1.0, silence_s=1.0, sr=16000):
    w = wave.open(path, "wb")
    w.setnchannels(1)
    w.setsampwidth(2)
    w.setframerate(sr)
    buf = bytearray()
    for i in range(int(sr * voiced_s)):
        buf += struct.pack("<h", int(8000 * math.sin(2 * math.pi * 200 * i / sr)))
    buf += b"\x00\x00" * int(sr * silence_s)
    w.writeframes(bytes(buf))
    w.close()


class EnergySnapTests(unittest.TestCase):
    def setUp(self):
        self.wav = tempfile.mktemp(suffix=".wav")
        _make_wav(self.wav)

    def tearDown(self):
        if os.path.exists(self.wav):
            os.remove(self.wav)

    def test_voiced_interval_detected(self):
        iv = _voiced_intervals(self.wav)
        self.assertTrue(iv)
        self.assertAlmostEqual(iv[0][0], 0.0, delta=0.05)
        self.assertAlmostEqual(iv[-1][1], 1.0, delta=0.1)

    def test_trailing_word_pulled_out_of_silence(self):
        words = [
            {"word": "hello", "start": 0.1, "end": 0.5},
            {"word": "world", "start": 1.55, "end": 1.95},
        ]
        out = _snap_words_to_voiced(words, self.wav)
        self.assertEqual(out[0]["start"], 0.1)  # word over speech untouched
        self.assertLessEqual(out[1]["start"], 1.2)  # stranded word clamped back
        # a word clamped to the upper bound must keep positive duration
        for w in out:
            self.assertGreater(w["end"], w["start"])

    def test_all_silence_leaves_words_unchanged(self):
        silent = tempfile.mktemp(suffix=".wav")
        _make_wav(silent, voiced_s=0.0, silence_s=1.0)
        words = [{"word": "x", "start": 0.1, "end": 0.5}]
        try:
            self.assertEqual(_snap_words_to_voiced(words, silent), words)
        finally:
            os.remove(silent)


if __name__ == "__main__":
    unittest.main()
