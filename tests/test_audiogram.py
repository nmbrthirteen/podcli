"""Tests for audio-only input detection.

An episode that was never filmed used to reach `get_dimensions` and raise "No
video stream found" — after transcription, so the expensive half of the run was
already paid for. These cover knowing, from the header, that there is no picture
in it, and being careful about when that is worth stopping a run over.
"""

import os
import sys
import unittest
from unittest import mock

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BACKEND_ROOT = os.path.join(ROOT, "backend")
if BACKEND_ROOT not in sys.path:
    sys.path.insert(0, BACKEND_ROOT)

from services import audiogram


class IsAudioOnlyTests(unittest.TestCase):
    def test_sound_and_no_picture(self):
        info = {"streams": [{"codec_type": "audio"}]}
        with mock.patch.object(audiogram, "get_video_info", return_value=info):
            self.assertTrue(audiogram.is_audio_only("x.mp3"))

    def test_sound_behind_cover_art_still_counts(self):
        """An mp3's embedded artwork is a video stream to ffprobe. One still is
        not something to crop or follow a face around."""
        info = {"streams": [
            {"codec_type": "audio"},
            {"codec_type": "video", "avg_frame_rate": "0/0",
             "disposition": {"attached_pic": 1}},
        ]}
        with mock.patch.object(audiogram, "get_video_info", return_value=info):
            self.assertTrue(audiogram.is_audio_only("x.mp3"))

    def test_a_still_with_no_frame_rate_is_not_a_picture_either(self):
        info = {"streams": [
            {"codec_type": "audio"},
            {"codec_type": "video", "avg_frame_rate": "0/0", "disposition": {}},
        ]}
        with mock.patch.object(audiogram, "get_video_info", return_value=info):
            self.assertTrue(audiogram.is_audio_only("x.mka"))

    def test_a_real_video_is_not_audio_only(self):
        info = {"streams": [
            {"codec_type": "audio"},
            {"codec_type": "video", "avg_frame_rate": "30/1", "disposition": {}},
        ]}
        with mock.patch.object(audiogram, "get_video_info", return_value=info):
            self.assertFalse(audiogram.is_audio_only("x.mp4"))

    def test_a_silent_video_is_not_audio_only(self):
        info = {"streams": [
            {"codec_type": "video", "avg_frame_rate": "30/1", "disposition": {}},
        ]}
        with mock.patch.object(audiogram, "get_video_info", return_value=info):
            self.assertFalse(audiogram.is_audio_only("silent.mp4"))

    def test_a_file_it_cannot_read_is_not_called_audio(self):
        """A stub or a truncated download is not evidence of anything. Saying
        'this has no video track' about it would swap one unhelpful failure for
        another, so it goes down the path it always took."""
        with mock.patch.object(audiogram, "get_video_info", side_effect=OSError("nope")):
            self.assertFalse(audiogram.is_audio_only("stub.mp4"))

    def test_a_probe_that_found_no_streams_is_not_called_audio(self):
        with mock.patch.object(audiogram, "get_video_info", return_value={"streams": []}):
            self.assertFalse(audiogram.is_audio_only("stub.mp4"))


class MessageTests(unittest.TestCase):
    def test_it_names_the_file_and_says_what_is_wrong(self):
        message = audiogram.audio_only_message("/tmp/episode-12.mp3")
        self.assertIn("episode-12.mp3", message)
        self.assertIn("no video track", message)


if __name__ == "__main__":
    unittest.main()
