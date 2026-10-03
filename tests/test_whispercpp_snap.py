"""Energy-snap for whisper.cpp word timings: trailing words stranded in true
silence are pulled back into the voiced span, while words over speech are left
alone. (whisper.cpp sometimes stretches the final phrase across trailing
silence; this keeps captions in sync.)"""

import math
import os
import random
import struct
import sys
import tempfile
import unittest
import wave

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BACKEND_ROOT = os.path.join(ROOT, "backend")
if BACKEND_ROOT not in sys.path:
    sys.path.insert(0, BACKEND_ROOT)

import numpy as np

from services.transcription_whispercpp import _frame_rms, _snap_words_to_voiced, _voiced_intervals


def _voiced_intervals_quadratic_reference(wav_path, bridge=0.3, thresh_ratio=0.07):
    """The pre-fix implementation: gathers every window into an (nf, frame)
    matrix before taking the RMS. Kept here only as a correctness oracle for
    the O(n) rewrite in transcription_whispercpp._voiced_intervals. It must
    never run on real audio, only the short synthetic clips in this test."""
    import wave as wave_mod

    try:
        w = wave_mod.open(wav_path, "rb")
        sr, width, n = w.getframerate(), w.getsampwidth(), w.getnframes()
        raw = w.readframes(n)
        w.close()
    except Exception:
        return []
    if width != 2 or sr <= 0 or not raw:
        return []
    samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32)
    hop = max(1, int(sr * 0.010))
    frame = max(hop, int(sr * 0.025))
    if len(samples) < frame:
        return []
    nf = 1 + (len(samples) - frame) // hop
    idx = np.arange(nf)[:, None] * hop + np.arange(frame)[None, :]
    rms = np.sqrt((samples[idx] ** 2).mean(axis=1))
    peak = float(rms.max())
    if peak <= 0:
        return []
    voiced = rms > thresh_ratio * peak
    intervals, start = [], None
    for i, v in enumerate(voiced):
        if v and start is None:
            start = i * hop / sr
        elif not v and start is not None:
            intervals.append([start, i * hop / sr])
            start = None
    if start is not None:
        intervals.append([start, nf * hop / sr])
    merged = []
    for iv in intervals:
        if merged and iv[0] - merged[-1][1] <= bridge:
            merged[-1][1] = iv[1]
        else:
            merged.append(iv)
    return merged


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


class VoicedIntervalsMatchesOldImplementationTests(unittest.TestCase):
    """The O(n) running-sum rewrite has to produce the same voiced intervals
    as the O(nf * frame) index-matrix version it replaces, on audio shaped
    like what it actually has to handle: several voiced/silent transitions,
    not just one clean on/off."""

    def _make_mixed_wav(self, path, sr=16000, seed=0):
        rng = random.Random(seed)
        w = wave.open(path, "wb")
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        buf = bytearray()
        # Alternating voiced tones and silence of varying length, plus a
        # little noise floor during "silence" so it isn't a degenerate
        # all-zero case.
        for seg_i in range(6):
            dur = 0.3 + 0.1 * seg_i
            n = int(sr * dur)
            if seg_i % 2 == 0:
                for i in range(n):
                    val = int(7000 * math.sin(2 * math.pi * (180 + seg_i * 20) * i / sr))
                    buf += struct.pack("<h", val)
            else:
                for _ in range(n):
                    buf += struct.pack("<h", rng.randint(-50, 50))
        w.writeframes(bytes(buf))
        w.close()

    def test_matches_quadratic_reference_on_mixed_audio(self):
        path = tempfile.mktemp(suffix=".wav")
        self._make_mixed_wav(path)
        try:
            fast = _voiced_intervals(path)
            reference = _voiced_intervals_quadratic_reference(path)
        finally:
            os.remove(path)

        self.assertEqual(len(fast), len(reference))
        for (fs, fe), (rs, re) in zip(fast, reference):
            self.assertAlmostEqual(fs, rs, places=4)
            self.assertAlmostEqual(fe, re, places=4)

    def test_matches_quadratic_reference_across_seeds(self):
        for seed in range(3):
            with self.subTest(seed=seed):
                path = tempfile.mktemp(suffix=".wav")
                self._make_mixed_wav(path, seed=seed)
                try:
                    fast = _voiced_intervals(path)
                    reference = _voiced_intervals_quadratic_reference(path)
                finally:
                    os.remove(path)
                self.assertEqual(fast, reference)


class FrameRmsLongFilePrecisionTests(unittest.TestCase):
    """On a multi-hour file the running sum of squares climbs into the range
    where float32 can no longer resolve one frame's contribution. A late
    frame's RMS comes out wrong, and can cross the voiced/silent threshold
    the wrong way. float64 has to hold exactly through the same file length."""

    def test_late_frame_rms_matches_direct_computation_on_a_long_constant_signal(self):
        sr = 16000
        hop = int(sr * 0.010)
        frame = int(sr * 0.025)
        amplitude = 10000.0
        # ~20.8 minutes at 16kHz: long enough for float32's cumulative sum
        # of squares to lose more precision than a single frame is worth.
        n_samples = 20_000_000
        samples = np.full(n_samples, amplitude, dtype=np.float32)
        nf = 1 + (n_samples - frame) // hop

        rms = _frame_rms(samples, hop, frame, nf)

        # A constant-amplitude signal has an exact, trivial per-frame RMS:
        # amplitude itself, independent of position. So this is a direct
        # computation, not another running sum that could share the bug.
        late_frame = nf - 1
        self.assertAlmostEqual(float(rms[late_frame]), amplitude, delta=1e-6)
        mid_frame = nf // 2
        self.assertAlmostEqual(float(rms[mid_frame]), amplitude, delta=1e-6)

    def test_float32_cumsum_would_have_failed_the_same_assertion(self):
        # Pins the regression: confirms the bug this test guards against is
        # real and large, not a tolerance picked to pass trivially.
        sr = 16000
        hop = int(sr * 0.010)
        frame = int(sr * 0.025)
        amplitude = 10000.0
        n_samples = 20_000_000
        samples = np.full(n_samples, amplitude, dtype=np.float32)
        nf = 1 + (n_samples - frame) // hop

        sq = samples * samples
        csum32 = np.concatenate(([0.0], np.cumsum(sq, dtype=np.float32)))
        starts = np.arange(nf) * hop
        window_sums = csum32[starts + frame] - csum32[starts]
        rms32 = np.sqrt(np.maximum(window_sums, 0.0) / frame)

        self.assertGreater(abs(float(rms32[nf // 2]) - amplitude), 100.0)


if __name__ == "__main__":
    unittest.main()
