"""Tests for backend.services.transcription_omnilingual: the sherpa-onnx
Omnilingual ASR CTC adapter. Mocks the recognizer; never loads the real
model (large download) or decodes real audio."""

import math
import os
import sys
import unittest
from unittest import mock

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BACKEND_ROOT = os.path.join(ROOT, "backend")
if BACKEND_ROOT not in sys.path:
    sys.path.insert(0, BACKEND_ROOT)

import services.transcription_omnilingual as omni


class TokensToWordsTests(unittest.TestCase):
    def test_splits_on_boundary_space_token(self):
        tokens = ["h", "i", " ", "t", "h", "e", "r", "e"]
        timestamps = [0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7]
        words = omni._tokens_to_words(tokens, timestamps, decode_end=10.0)
        self.assertEqual([w["word"] for w in words], ["hi", "there"])
        self.assertEqual(words[0]["start"], 0.0)
        self.assertAlmostEqual(words[0]["end"], 0.1 + omni.WORD_END_PAD_SECONDS)
        self.assertEqual(words[1]["start"], 0.3)

    def test_leading_and_trailing_boundary_tokens_produce_no_empty_words(self):
        tokens = [" ", "h", "i", " "]
        timestamps = [0.0, 0.1, 0.2, 0.3]
        words = omni._tokens_to_words(tokens, timestamps, decode_end=10.0)
        self.assertEqual([w["word"] for w in words], ["hi"])

    def test_word_end_capped_at_decode_end(self):
        tokens = ["x"]
        timestamps = [9.995]
        words = omni._tokens_to_words(tokens, timestamps, decode_end=10.0)
        self.assertLessEqual(words[0]["end"], 10.0)

    def test_single_token_word_has_positive_duration(self):
        tokens = ["a"]
        timestamps = [1.0]
        words = omni._tokens_to_words(tokens, timestamps, decode_end=10.0)
        self.assertGreater(words[0]["end"], words[0]["start"])


class GroupIntoSegmentsTests(unittest.TestCase):
    def test_splits_on_pause(self):
        words = [
            {"word": "hello", "start": 0.0, "end": 0.5},
            {"word": "world", "start": 0.6, "end": 1.0},
            {"word": "later", "start": 2.0, "end": 2.5},  # 1.0s gap >= 0.5
        ]
        segments = omni._group_into_segments(words)
        self.assertEqual(len(segments), 2)
        self.assertEqual(segments[0]["text"], "hello world")
        self.assertEqual(segments[1]["text"], "later")

    def test_no_words_produces_no_segments(self):
        self.assertEqual(omni._group_into_segments([]), [])


class ValidateTokensAndTimestampsTests(unittest.TestCase):
    def test_mismatched_lengths_raise(self):
        with self.assertRaises(ValueError):
            omni._validate_tokens_and_timestamps(["a", "b"], [0.0], "w")

    def test_nan_timestamp_raises(self):
        with self.assertRaises(ValueError):
            omni._validate_tokens_and_timestamps(["a"], [math.nan], "w")

    def test_negative_timestamp_raises(self):
        with self.assertRaises(ValueError):
            omni._validate_tokens_and_timestamps(["a"], [-0.1], "w")

    def test_out_of_order_timestamps_raise(self):
        with self.assertRaises(ValueError):
            omni._validate_tokens_and_timestamps(["a", "b"], [1.0, 0.5], "w")

    def test_valid_input_does_not_raise(self):
        omni._validate_tokens_and_timestamps(["a", "b"], [0.0, 0.1], "w")


class FakeStream:
    def __init__(self, tokens, timestamps):
        self._tokens = tokens
        self._timestamps = timestamps
        self.result = mock.Mock(tokens=tokens, timestamps=timestamps)

    def accept_waveform(self, sr, chunk):
        pass


class FakeRecognizer:
    """Returns a fixed token stream per call, regardless of audio content.
    The tests only check the windowing/assignment logic in transcribe_file,
    not real decoding."""

    def __init__(self, per_call_tokens):
        self._calls = iter(per_call_tokens)

    def create_stream(self):
        tokens, timestamps = next(self._calls)
        return FakeStream(tokens, timestamps)

    def decode_stream(self, stream):
        pass


class TranscribeFileWindowingTests(unittest.TestCase):
    def setUp(self):
        self._orig_read_wav = omni._read_wav_mono16
        self._orig_load = omni._load_recognizer
        self._tmp_files = []

    def tearDown(self):
        import shutil

        omni._read_wav_mono16 = self._orig_read_wav
        omni._load_recognizer = self._orig_load
        for p in self._tmp_files:
            if os.path.isdir(p):
                shutil.rmtree(p, ignore_errors=True)
            elif os.path.exists(p):
                os.unlink(p)

    def _touch(self, name):
        import tempfile

        fd, path = tempfile.mkstemp(suffix=name)
        os.close(fd)
        self._tmp_files.append(path)
        return path

    def test_single_window_file_produces_expected_words(self):
        media = self._touch(".mp4")
        model = self._touch(".onnx")
        tokens_file = self._touch(".txt")
        wav = self._touch(".wav")

        import numpy as np

        omni._read_wav_mono16 = lambda path: (np.zeros(16000 * 5, dtype=np.float32), 16000, 5.0)
        fake = FakeRecognizer([(["h", "i"], [0.0, 0.1])])
        omni._load_recognizer = lambda *a, **k: fake

        result = omni.transcribe_file(media, model, tokens_file, wav_path=wav)
        self.assertEqual(result["words"][0]["word"], "hi")
        self.assertEqual(result["duration"], 5.0)
        self.assertEqual(result["language"], "und")

    def test_context_overlap_is_not_duplicated_across_windows(self):
        # A 25s file splits into two core windows (0-20, 20-25). Each decode
        # call includes 1s of context from the neighbor. A word whose
        # midpoint sits just past 20s must be claimed by window 2 only.
        media = self._touch(".mp4")
        model = self._touch(".onnx")
        tokens_file = self._touch(".txt")
        wav = self._touch(".wav")

        import numpy as np

        duration = 25.0
        omni._read_wav_mono16 = lambda path: (
            np.zeros(int(16000 * duration), dtype=np.float32), 16000, duration,
        )
        # Window 0 decodes [0, 21): sees a word at absolute t=20.5 (in its
        # context tail). Window 1 decodes [19, 25): sees the same word.
        fake = FakeRecognizer([
            (["a", "b"], [20.5 - 0.0, 20.5 - 0.0 + 0.01]),  # decode_start=0 -> absolute 20.5
            (["a", "b"], [20.5 - 19.0, 20.5 - 19.0 + 0.01]),  # decode_start=19 -> absolute 20.5
        ])
        omni._load_recognizer = lambda *a, **k: fake

        result = omni.transcribe_file(media, model, tokens_file, wav_path=wav)
        matching = [w for w in result["words"] if w["word"] == "ab"]
        self.assertEqual(len(matching), 1)

    def test_shifted_timestamps_for_the_same_word_at_a_seam_are_deduped(self):
        # Same scenario as the exact-timestamp case above, but the two
        # neighboring windows' own (overlapping) decodes timestamp the same
        # word a little differently, as a real acoustic model can. Each
        # window's own midpoint test alone would let both keep it.
        media = self._touch(".mp4")
        model = self._touch(".onnx")
        tokens_file = self._touch(".txt")
        wav = self._touch(".wav")

        import numpy as np

        duration = 25.0
        omni._read_wav_mono16 = lambda path: (
            np.zeros(int(16000 * duration), dtype=np.float32), 16000, duration,
        )
        # Window 0 decodes [0, 21): "ab" lands at absolute 19.6-19.9,
        # inside its own core [0, 20).
        # Window 1 decodes [19, 25): the same "ab" lands at absolute
        # 20.05-20.3 instead, shifted ~0.4s by the different decode
        # context, inside its own core [20, 25).
        fake = FakeRecognizer([
            (["a", "b"], [19.6, 19.9]),           # decode_start=0 -> absolute 19.6, 19.9
            (["a", "b"], [1.05, 1.3]),            # decode_start=19 -> absolute 20.05, 20.3
        ])
        omni._load_recognizer = lambda *a, **k: fake

        result = omni.transcribe_file(media, model, tokens_file, wav_path=wav)
        matching = [w for w in result["words"] if w["word"] == "ab"]
        self.assertEqual(len(matching), 1)
        # The surviving copy is window 0's: the earlier window wins ties,
        # since it's the one whose receipt (if any) was already written.
        self.assertAlmostEqual(matching[0]["start"], 19.6, places=3)

    def test_a_legitimate_repeated_word_away_from_any_seam_is_not_deduped(self):
        media = self._touch(".mp4")
        model = self._touch(".onnx")
        tokens_file = self._touch(".txt")
        wav = self._touch(".wav")

        import numpy as np

        duration = 25.0
        omni._read_wav_mono16 = lambda path: (
            np.zeros(int(16000 * duration), dtype=np.float32), 16000, duration,
        )
        # Both repeats of "no" sit well inside window 0's core, nowhere
        # near the 20s boundary, a real stutter/repeat, not a seam echo.
        fake = FakeRecognizer([
            (["n", "o", " ", "n", "o"], [2.0, 2.1, 2.2, 2.3, 2.4]),
            ([], []),
        ])
        omni._load_recognizer = lambda *a, **k: fake

        result = omni.transcribe_file(media, model, tokens_file, wav_path=wav)
        matching = [w for w in result["words"] if w["word"] == "no"]
        self.assertEqual(len(matching), 2)

    def test_resumes_from_receipts_without_touching_the_recognizer(self):
        # A rerun with the same run_dir must skip every window that already
        # has a receipt. Simulated here by pre-seeding window-0's receipt
        # and giving the fake recognizer only enough calls for window 1.
        media = self._touch(".mp4")
        model = self._touch(".onnx")
        tokens_file = self._touch(".txt")
        wav = self._touch(".wav")

        import tempfile
        import numpy as np

        from services import transcribe_runs

        run_dir = tempfile.mkdtemp()
        self._tmp_files.append(run_dir)
        transcribe_runs.write_receipt(
            run_dir, "window-0.json",
            {"words": [{"word": "resumed", "start": 1.0, "end": 1.3}]},
        )

        duration = 25.0
        omni._read_wav_mono16 = lambda path: (
            np.zeros(int(16000 * duration), dtype=np.float32), 16000, duration,
        )
        # Only one call available: if the resumed window tried to decode
        # again, create_stream() would raise StopIteration from the fake.
        # Window 1 decodes [19, 25) (1s context back into window 0's core);
        # these relative timestamps land at absolute 21.0-21.2, inside
        # window 1's own core [20, 25), so it's the one that claims them.
        fake = FakeRecognizer([(["n", "e", "w"], [2.0, 2.1, 2.2])])
        omni._load_recognizer = lambda *a, **k: fake

        result = omni.transcribe_file(media, model, tokens_file, wav_path=wav, run_dir=run_dir)

        self.assertIn({"word": "resumed", "start": 1.0, "end": 1.3, "speaker": None}, result["words"])
        self.assertTrue(any(w["word"] == "new" for w in result["words"]))
        # The run completed fully, so its receipts are cleared.
        self.assertFalse(os.path.exists(run_dir))

    def test_a_crash_mid_file_leaves_completed_windows_resumable(self):
        media = self._touch(".mp4")
        model = self._touch(".onnx")
        tokens_file = self._touch(".txt")
        wav = self._touch(".wav")

        import tempfile
        import numpy as np

        run_dir = tempfile.mkdtemp()
        self._tmp_files.append(run_dir)

        duration = 25.0
        omni._read_wav_mono16 = lambda path: (
            np.zeros(int(16000 * duration), dtype=np.float32), 16000, duration,
        )

        class CrashingRecognizer:
            def __init__(self):
                self.calls = 0

            def create_stream(self):
                self.calls += 1
                if self.calls == 2:
                    raise RuntimeError("simulated crash on window 1")
                return FakeStream(["a"], [0.0])

            def decode_stream(self, stream):
                pass

        omni._load_recognizer = lambda *a, **k: CrashingRecognizer()

        with self.assertRaises(RuntimeError):
            omni.transcribe_file(media, model, tokens_file, wav_path=wav, run_dir=run_dir)

        # Window 0's receipt survives the crash in window 1.
        self.assertTrue(os.path.exists(os.path.join(run_dir, "window-0.json")))
        self.assertFalse(os.path.exists(os.path.join(run_dir, "window-1.json")))

    def test_raises_a_clear_error_when_method_missing_on_old_sherpa_onnx(self):
        class _NoOmnilingualRecognizer:
            pass

        fake_sherpa = mock.Mock(OfflineRecognizer=_NoOmnilingualRecognizer)
        with mock.patch.dict(sys.modules, {"sherpa_onnx": fake_sherpa}):
            with self.assertRaises(RuntimeError):
                omni._load_recognizer("/fake/model", "/fake/tokens", threads=1)


if __name__ == "__main__":
    unittest.main()
