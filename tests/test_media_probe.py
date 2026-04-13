"""Tests for backend.services.media_probe — extracted ffmpeg helpers."""

import os
import sys
import unittest
from unittest import mock

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BACKEND_ROOT = os.path.join(ROOT, "backend")
if BACKEND_ROOT not in sys.path:
    sys.path.insert(0, BACKEND_ROOT)

from services import media_probe


class ParseDurationSecondsTests(unittest.TestCase):
    def test_valid_numeric_string(self):
        self.assertEqual(media_probe.parse_duration_seconds("12.5"), 12.5)

    def test_valid_float(self):
        self.assertEqual(media_probe.parse_duration_seconds(30.0), 30.0)

    def test_rejects_nan(self):
        self.assertIsNone(media_probe.parse_duration_seconds("nan"))
        self.assertIsNone(media_probe.parse_duration_seconds(float("nan")))

    def test_rejects_infinity(self):
        self.assertIsNone(media_probe.parse_duration_seconds(float("inf")))
        self.assertIsNone(media_probe.parse_duration_seconds("inf"))

    def test_rejects_zero_and_negative(self):
        self.assertIsNone(media_probe.parse_duration_seconds(0))
        self.assertIsNone(media_probe.parse_duration_seconds(-5))

    def test_rejects_non_parsable(self):
        self.assertIsNone(media_probe.parse_duration_seconds("N/A"))
        self.assertIsNone(media_probe.parse_duration_seconds(None))
        self.assertIsNone(media_probe.parse_duration_seconds(""))


class GetMediaDurationSecondsTests(unittest.TestCase):
    def test_uses_format_duration_when_valid(self):
        fake_info = {"format": {"duration": "42.5"}, "streams": []}
        with mock.patch.object(media_probe, "get_video_info", return_value=fake_info):
            self.assertEqual(
                media_probe.get_media_duration_seconds("/fake.mp4"), 42.5
            )

    def test_prefers_max_of_format_and_streams(self):
        fake_info = {
            "format": {"duration": "30.0"},
            "streams": [
                {"duration": "29.5"},
                {"duration": "31.0"},  # slightly longer stream
            ],
        }
        with mock.patch.object(media_probe, "get_video_info", return_value=fake_info):
            self.assertEqual(
                media_probe.get_media_duration_seconds("/fake.mp4"), 31.0
            )

    def test_falls_back_to_default_when_all_invalid(self):
        fake_info = {
            "format": {"duration": "N/A"},
            "streams": [{"duration": "N/A"}],
        }
        with mock.patch.object(media_probe, "get_video_info", return_value=fake_info):
            self.assertEqual(
                media_probe.get_media_duration_seconds("/fake.mp4", default=99.0),
                99.0,
            )

    def test_falls_back_when_probe_raises(self):
        with mock.patch.object(
            media_probe, "get_video_info", side_effect=RuntimeError("ffprobe failed")
        ):
            self.assertEqual(
                media_probe.get_media_duration_seconds("/fake.mp4", default=7.0),
                7.0,
            )


class HasAudioStreamTests(unittest.TestCase):
    def test_detects_audio(self):
        info = {"streams": [{"codec_type": "video"}, {"codec_type": "audio"}]}
        with mock.patch.object(media_probe, "get_video_info", return_value=info):
            self.assertTrue(media_probe.has_audio_stream("/fake.mp4"))

    def test_detects_no_audio(self):
        info = {"streams": [{"codec_type": "video"}]}
        with mock.patch.object(media_probe, "get_video_info", return_value=info):
            self.assertFalse(media_probe.has_audio_stream("/fake.mp4"))

    def test_assumes_yes_on_probe_failure(self):
        # Defensive default — pipeline should still try audio filters
        with mock.patch.object(
            media_probe, "get_video_info", side_effect=RuntimeError("boom")
        ):
            self.assertTrue(media_probe.has_audio_stream("/fake.mp4"))


class GetDimensionsTests(unittest.TestCase):
    def test_returns_first_video_stream_dimensions(self):
        info = {
            "streams": [
                {"codec_type": "audio"},
                {"codec_type": "video", "width": 1920, "height": 1080},
            ]
        }
        with mock.patch.object(media_probe, "get_video_info", return_value=info):
            self.assertEqual(
                media_probe.get_dimensions("/fake.mp4"), (1920, 1080)
            )

    def test_raises_when_no_video_stream(self):
        info = {"streams": [{"codec_type": "audio"}]}
        with mock.patch.object(media_probe, "get_video_info", return_value=info):
            with self.assertRaises(ValueError):
                media_probe.get_dimensions("/fake.mp4")


class VideoProcessorReExportsTests(unittest.TestCase):
    """Regression guard: video_processor must continue to expose the
    helpers under their original names so older imports and test mocks
    don't break."""

    def test_video_processor_reexports_helpers(self):
        from services import video_processor as vp
        self.assertIs(vp.get_video_info, media_probe.get_video_info)
        self.assertIs(vp.get_dimensions, media_probe.get_dimensions)
        self.assertIs(vp._has_audio_stream, media_probe.has_audio_stream)
        self.assertIs(
            vp._parse_duration_seconds, media_probe.parse_duration_seconds
        )
        self.assertIs(
            vp._get_media_duration_seconds, media_probe.get_media_duration_seconds
        )
        self.assertIs(
            vp._run_ffmpeg_with_fallback, media_probe.run_ffmpeg_with_fallback
        )

    def test_video_processor_still_exposes_cpu_flags_and_timeout(self):
        from services import video_processor as vp
        self.assertEqual(vp.CPU_FLAGS, media_probe.CPU_FLAGS)
        self.assertEqual(vp._FFMPEG_TIMEOUT, media_probe.FFMPEG_TIMEOUT)


if __name__ == "__main__":
    unittest.main()
