"""Tests for backend.services.transcript_packer phrase boundaries.

The packed markdown is the only view the clip selector reads, so a phrase that
swallows a question plus the answer around it is the difference between a clip
that makes sense and one that opens on a reply to nothing.
"""

import os
import sys
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BACKEND_ROOT = os.path.join(ROOT, "backend")
if BACKEND_ROOT not in sys.path:
    sys.path.insert(0, BACKEND_ROOT)

from services.transcript_packer import pack_transcript


def _words(text: str, start: float = 0.0, speaker=None, step: float = 0.3):
    """One word per token, back to back so no silence gap ever splits."""
    out = []
    t = start
    for token in text.split(" "):
        out.append({"word": token, "start": round(t, 3), "end": round(t + step, 3), "speaker": speaker})
        t += step
    return out


def _transcript(words, speaker_segments=None, num_speakers=0):
    speakers = {"num_speakers": num_speakers, "speakers": {}}
    if num_speakers:
        speakers["speakers"] = {
            f"SPEAKER_0{i}": {"label": f"Speaker {i}", "total_time": 10.0}
            for i in range(num_speakers)
        }
    return {
        "words": words,
        "duration": words[-1]["end"] if words else 0.0,
        "language": "en",
        "speakers": speakers,
        "speaker_segments": speaker_segments or [],
    }


def _phrase_lines(md: str):
    return [ln for ln in md.split("\n") if ln.startswith("[") and "]" in ln and " S" in ln]


class NoSpeakerLabelsTests(unittest.TestCase):
    """With diarization off every line is S?, so the speaker split never fires."""

    def setUp(self):
        text = (
            "But I went to Peru and that is where we stayed. "
            "Why did you want to go there? "
            "Well I am a big nature person and I wanted to see it."
        )
        self.md = pack_transcript(_transcript(_words(text)), "test.mp4")
        self.lines = _phrase_lines(self.md)

    def test_question_becomes_its_own_line(self):
        questions = [ln for ln in self.lines if ln.rstrip().endswith("?")]
        self.assertEqual(len(questions), 1)
        self.assertIn("Why did you want to go there?", questions[0])
        # and it carries its own timestamps, so a clip can start on it
        self.assertNotIn("stayed", questions[0])
        self.assertNotIn("Well I am", questions[0])

    def test_full_stops_also_split(self):
        self.assertGreaterEqual(len(self.lines), 3)

    def test_header_warns_the_reader_not_to_guess_turns(self):
        self.assertIn("No speaker labels", self.md)
        self.assertIn("do not guess", self.md)

    def test_short_fragment_before_a_stop_does_not_split(self):
        # "No." is under the abbreviation floor, so it stays glued.
        md = pack_transcript(_transcript(_words("No. that never happened to us at all")), "t.mp4")
        self.assertEqual(len(_phrase_lines(md)), 1)


class WithSpeakerLabelsTests(unittest.TestCase):
    """Diarized transcripts keep speaker splits and gain the sentence split."""

    def setUp(self):
        words = _words(
            "I stayed deep in the Amazon jungle. It was great.", start=0.0, speaker="SPEAKER_00"
        )
        words += _words("Why did you go there?", start=20.0, speaker="SPEAKER_01")
        segments = [
            {"speaker": "SPEAKER_00", "start": 0.0, "end": 10.0},
            {"speaker": "SPEAKER_01", "start": 20.0, "end": 26.0},
        ]
        self.md = pack_transcript(_transcript(words, segments, num_speakers=2), "test.mp4")
        self.lines = _phrase_lines(self.md)

    def test_no_warning_when_speakers_are_known(self):
        self.assertNotIn("No speaker labels", self.md)

    def test_speakers_are_distinguished(self):
        speakers = {ln.split("] ")[1].split(" ")[0] for ln in self.lines}
        self.assertEqual(len(speakers), 2)
        self.assertNotIn("S?", speakers)

    def test_question_still_splits(self):
        self.assertTrue(any(ln.rstrip().endswith("?") for ln in self.lines))

    def test_full_stops_split_diarized_lines_too(self):
        # Diarization drifts a word or two at a turn, so a phrase that runs past
        # a sentence end leaves the selector nowhere clean to start a clip.
        first = [ln for ln in self.lines if "Amazon" in ln][0]
        self.assertNotIn("It was great.", first)
        self.assertTrue(any("It was great." in ln for ln in self.lines))


if __name__ == "__main__":
    unittest.main()
