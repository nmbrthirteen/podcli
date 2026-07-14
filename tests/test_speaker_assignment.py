"""Tests for backend.services.speaker_detection word/segment assignment."""

import os
import sys
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BACKEND_ROOT = os.path.join(ROOT, "backend")
if BACKEND_ROOT not in sys.path:
    sys.path.insert(0, BACKEND_ROOT)

from services.speaker_detection import assign_speakers_to_words


class AssignSpeakersToWordsTests(unittest.TestCase):
    def test_no_diarization_data_assigns_none(self):
        words = [{"word": "hi", "start": 0.0, "end": 0.4}]
        out = assign_speakers_to_words(words, [])
        self.assertIsNone(out[0]["speaker"])

    def test_word_inside_single_segment(self):
        words = [{"word": "hi", "start": 1.0, "end": 1.4}]
        segments = [{"speaker": "SPEAKER_00", "start": 0.0, "end": 5.0}]
        out = assign_speakers_to_words(words, segments)
        self.assertEqual(out[0]["speaker"], "SPEAKER_00")

    def test_boundary_word_gets_max_overlap_speaker_not_first_match(self):
        # Word straddles a turn boundary: A covers 0.1s, B covers 0.5s.
        words = [{"word": "yeah", "start": 10.0, "end": 10.6}]
        segments = [
            {"speaker": "SPEAKER_00", "start": 9.0, "end": 10.1},
            {"speaker": "SPEAKER_01", "start": 10.1, "end": 12.0},
        ]
        out = assign_speakers_to_words(words, segments)
        self.assertEqual(out[0]["speaker"], "SPEAKER_01")

    def test_split_segments_of_same_speaker_accumulate(self):
        words = [{"word": "so", "start": 10.0, "end": 11.0}]
        segments = [
            {"speaker": "SPEAKER_00", "start": 10.0, "end": 10.3},
            {"speaker": "SPEAKER_01", "start": 10.3, "end": 10.7},
            {"speaker": "SPEAKER_00", "start": 10.7, "end": 11.0},
        ]
        out = assign_speakers_to_words(words, segments)
        self.assertEqual(out[0]["speaker"], "SPEAKER_00")

    def test_zero_length_word_falls_back_to_midpoint(self):
        words = [{"word": "uh", "start": 10.5, "end": 10.5}]
        segments = [{"speaker": "SPEAKER_01", "start": 10.0, "end": 11.0}]
        out = assign_speakers_to_words(words, segments)
        self.assertEqual(out[0]["speaker"], "SPEAKER_01")

    def test_word_outside_all_segments_is_none(self):
        words = [{"word": "hm", "start": 50.0, "end": 50.4}]
        segments = [{"speaker": "SPEAKER_00", "start": 0.0, "end": 10.0}]
        out = assign_speakers_to_words(words, segments)
        self.assertIsNone(out[0]["speaker"])


if __name__ == "__main__":
    unittest.main()
