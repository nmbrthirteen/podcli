"""Tests for backend.services.encoder flag helpers."""

import os
import sys
import unittest
from unittest import mock

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BACKEND_ROOT = os.path.join(ROOT, "backend")
if BACKEND_ROOT not in sys.path:
    sys.path.insert(0, BACKEND_ROOT)

from services import encoder


class GetEncoderFlagsTests(unittest.TestCase):
    def test_returns_expected_flags_per_encoder(self):
        # videotoolbox (macOS)
        flags = encoder._get_encoder_flags("h264_videotoolbox")
        self.assertIn("h264_videotoolbox", flags)
        self.assertIn("-b:v", flags)

        # nvenc (NVIDIA)
        flags = encoder._get_encoder_flags("h264_nvenc")
        self.assertIn("h264_nvenc", flags)
        self.assertIn("-cq", flags)

        # amf (AMD)
        flags = encoder._get_encoder_flags("h264_amf")
        self.assertIn("h264_amf", flags)

        # vaapi (Linux Intel/AMD)
        flags = encoder._get_encoder_flags("h264_vaapi")
        self.assertIn("h264_vaapi", flags)

        # qsv (Intel QuickSync)
        flags = encoder._get_encoder_flags("h264_qsv")
        self.assertIn("h264_qsv", flags)

        # libx264 software fallback
        flags = encoder._get_encoder_flags("libx264")
        self.assertIn("libx264", flags)
        self.assertIn("-crf", flags)

    def test_unknown_encoder_falls_back_to_libx264(self):
        flags = encoder._get_encoder_flags("mystery_encoder_xyz")
        # Should return the libx264 flag set, not an empty list or error
        self.assertIn("libx264", flags)
        self.assertIn("-crf", flags)

    def test_all_flag_lists_start_with_c_v(self):
        # Every encoder profile must specify -c:v as its first pair
        for name in ["h264_videotoolbox", "h264_nvenc", "h264_amf",
                     "h264_vaapi", "h264_qsv", "libx264"]:
            flags = encoder._get_encoder_flags(name)
            self.assertEqual(flags[0], "-c:v")
            self.assertTrue(flags[1].startswith("h264") or flags[1] == "libx264")


class GetVideoEncodeFlagsTests(unittest.TestCase):
    def test_uses_detect_encoders_best_flags(self):
        fake_flags = ["-c:v", "h264_videotoolbox", "-b:v", "6M"]
        with mock.patch.object(
            encoder, "detect_encoders", return_value={"best_flags": fake_flags}
        ):
            self.assertEqual(encoder.get_video_encode_flags(), fake_flags)

    def test_falls_back_when_detect_encoders_raises(self):
        with mock.patch.object(encoder, "detect_encoders", side_effect=RuntimeError("boom")):
            flags = encoder.get_video_encode_flags()
            # Absolute fallback — must always return something usable
            self.assertIn("libx264", flags)
            self.assertIn("-crf", flags)


class GetEncoderInfoTests(unittest.TestCase):
    def setUp(self):
        # Isolate the on-disk cache so each test exercises detect_encoders directly.
        import tempfile
        self._tmpdir = tempfile.TemporaryDirectory()
        self._cache_path = os.path.join(self._tmpdir.name, "encoder.json")
        self._cache_patch = mock.patch.object(
            encoder, "_encoder_cache_path", return_value=self._cache_path
        )
        self._cache_patch.start()

    def tearDown(self):
        self._cache_patch.stop()
        self._tmpdir.cleanup()

    def test_returns_detect_encoders_output_on_success(self):
        fake_info = {"available": ["libx264", "h264_nvenc"], "best": "h264_nvenc"}
        with mock.patch.object(encoder, "detect_encoders", return_value=fake_info):
            self.assertEqual(encoder.get_encoder_info(), fake_info)

    def test_returns_libx264_fallback_on_failure(self):
        with mock.patch.object(encoder, "detect_encoders", side_effect=OSError("no ffmpeg")):
            info = encoder.get_encoder_info()
            self.assertEqual(info["best"], "libx264")
            self.assertIn("libx264", info["available"])
            self.assertIn("libx264", info["best_flags"])

    def test_caches_result_between_calls(self):
        fake_info = {"available": ["libx264", "h264_nvenc"], "best": "h264_nvenc"}
        with mock.patch.object(encoder, "detect_encoders", return_value=fake_info) as detect:
            encoder.get_encoder_info()
            encoder.get_encoder_info()
            self.assertEqual(detect.call_count, 1)


if __name__ == "__main__":
    unittest.main()
