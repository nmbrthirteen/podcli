"""Tests for backend.services.audio_normalize.

Tests the pure helpers and the two-pass orchestration by mocking
proc_run so no actual ffmpeg process is spawned.
"""

import os
import sys
import unittest
from unittest import mock

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BACKEND_ROOT = os.path.join(ROOT, "backend")
if BACKEND_ROOT not in sys.path:
    sys.path.insert(0, BACKEND_ROOT)

from services import audio_normalize as an


class ParseLoudnormStatsTests(unittest.TestCase):
    def test_empty_stderr_returns_none(self):
        self.assertIsNone(an._parse_loudnorm_stats(""))
        self.assertIsNone(an._parse_loudnorm_stats(None))  # type: ignore[arg-type]

    def test_missing_json_block_returns_none(self):
        self.assertIsNone(
            an._parse_loudnorm_stats("no json here at all, just text")
        )

    def test_parses_valid_json_block(self):
        stderr = (
            "some ffmpeg noise\n"
            '{"input_i": "-20.5", "input_tp": "-2.0", '
            '"input_lra": "5.5", "input_thresh": "-30.0", '
            '"target_offset": "-0.5"}\n'
            "more noise\n"
        )
        data = an._parse_loudnorm_stats(stderr)
        self.assertIsNotNone(data)
        self.assertEqual(data["input_i"], "-20.5")
        self.assertEqual(data["input_tp"], "-2.0")

    def test_rejects_negative_infinity_input(self):
        # -inf means silence / too-short clip — two-pass isn't meaningful
        stderr = '{"input_i": "-inf", "input_tp": "-inf"}'
        self.assertIsNone(an._parse_loudnorm_stats(stderr))

    def test_rejects_positive_infinity_input(self):
        stderr = '{"input_i": "inf", "input_tp": "0"}'
        self.assertIsNone(an._parse_loudnorm_stats(stderr))

    def test_rejects_empty_input_i(self):
        stderr = '{"input_i": "", "input_tp": "0"}'
        self.assertIsNone(an._parse_loudnorm_stats(stderr))

    def test_rejects_malformed_json(self):
        stderr = '{"input_i": "-20", '  # truncated
        self.assertIsNone(an._parse_loudnorm_stats(stderr))


class NormalizeAudioTests(unittest.TestCase):
    def _ok(self, stderr: str = "") -> mock.Mock:
        return mock.Mock(returncode=0, stdout="", stderr=stderr)

    def test_two_pass_when_loudnorm_stats_available(self):
        measure_stderr = (
            '{"input_i": "-20.5", "input_tp": "-2.0", '
            '"input_lra": "5.5", "input_thresh": "-30.0", '
            '"target_offset": "-0.5"}'
        )
        with mock.patch.object(
            an, "proc_run",
            side_effect=[self._ok(stderr=measure_stderr), self._ok()],
        ) as mocked:
            result = an.normalize_audio("/in.mp4", "/out.mp4")
            self.assertEqual(result, "/out.mp4")
            self.assertEqual(mocked.call_count, 2)
            # Second call (apply) should include the measured values
            apply_cmd = mocked.call_args_list[1].args[0]
            joined = " ".join(apply_cmd)
            self.assertIn("measured_I=-20.5", joined)
            self.assertIn("linear=true", joined)

    def test_single_pass_fallback_on_missing_stats(self):
        with mock.patch.object(
            an, "proc_run",
            side_effect=[self._ok(stderr="no stats here"), self._ok()],
        ) as mocked:
            result = an.normalize_audio("/in.mp4", "/out.mp4")
            self.assertEqual(result, "/out.mp4")
            apply_cmd = mocked.call_args_list[1].args[0]
            joined = " ".join(apply_cmd)
            # No measured_* values — single-pass filter
            self.assertNotIn("measured_I", joined)
            self.assertIn("loudnorm=I=-14.0", joined)

    def test_single_pass_fallback_on_inf_measurement(self):
        measure_stderr = '{"input_i": "-inf"}'
        with mock.patch.object(
            an, "proc_run",
            side_effect=[self._ok(stderr=measure_stderr), self._ok()],
        ) as mocked:
            result = an.normalize_audio("/in.mp4", "/out.mp4")
            apply_cmd = mocked.call_args_list[1].args[0]
            joined = " ".join(apply_cmd)
            self.assertNotIn("measured_I", joined)

    def test_custom_target_lufs(self):
        with mock.patch.object(
            an, "proc_run",
            side_effect=[self._ok(stderr=""), self._ok()],
        ) as mocked:
            an.normalize_audio("/in.mp4", "/out.mp4", target_lufs=-16.0)
            apply_cmd = mocked.call_args_list[1].args[0]
            joined = " ".join(apply_cmd)
            self.assertIn("loudnorm=I=-16.0", joined)

    def test_raises_on_apply_failure(self):
        fail = mock.Mock(returncode=1, stdout="", stderr="broken")
        with mock.patch.object(
            an, "proc_run",
            side_effect=[self._ok(stderr=""), fail],
        ):
            with self.assertRaises(RuntimeError):
                an.normalize_audio("/in.mp4", "/out.mp4")


class ReExportTests(unittest.TestCase):
    def test_video_processor_still_exposes_normalize_audio(self):
        from services import video_processor as vp
        self.assertIs(vp.normalize_audio, an.normalize_audio)


if __name__ == "__main__":
    unittest.main()
