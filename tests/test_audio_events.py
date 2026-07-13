"""Tests for backend.services.audio_events scoring and profile scaffolding."""

import os
import sys
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BACKEND_ROOT = os.path.join(ROOT, "backend")
if BACKEND_ROOT not in sys.path:
    sys.path.insert(0, BACKEND_ROOT)

from services import audio_events as ae
from services import profiles


def _event(t, laughter=0.0, cheering=0.0, screaming=0.0, speech=0.0):
    return {
        "time": t,
        "laughter": laughter,
        "cheering": cheering,
        "screaming": screaming,
        "speech": speech,
    }


class ComputeEventScoresTests(unittest.TestCase):
    def test_empty_events_returns_zeros(self):
        segments = [{"start": 0, "end": 10}, {"start": 10, "end": 20}]
        self.assertEqual(ae.compute_event_scores([], segments), [0.0, 0.0])

    def test_empty_segments_returns_empty(self):
        self.assertEqual(ae.compute_event_scores([_event(1, laughter=0.5)], []), [])

    def test_segment_with_laugh_scores_higher_than_silent(self):
        events = [_event(2, laughter=0.5, speech=0.1), _event(30, speech=1.0)]
        scores = ae.compute_event_scores(
            events, [{"start": 0, "end": 10}, {"start": 20, "end": 40}]
        )
        self.assertGreater(scores[0], scores[1])
        self.assertEqual(scores[1], 0.0)

    def test_score_uses_loudest_reaction_channel(self):
        # cheering should count even with zero laughter
        events = [_event(1, cheering=0.5)]
        scores = ae.compute_event_scores(events, [{"start": 0, "end": 5}])
        self.assertGreater(scores[0], 0.0)

    def test_score_is_clamped_to_ten(self):
        events = [_event(1, laughter=1.0)]
        scores = ae.compute_event_scores(events, [{"start": 0, "end": 5}])
        self.assertLessEqual(scores[0], 10.0)

    def test_speech_alone_is_not_a_reaction(self):
        events = [_event(1, speech=1.0)]
        scores = ae.compute_event_scores(events, [{"start": 0, "end": 5}])
        self.assertEqual(scores[0], 0.0)


class ProfileTests(unittest.TestCase):
    def test_default_is_podcast_and_llm_sourced(self):
        p = profiles.get_profile(None)
        self.assertEqual(p.name, "podcast")
        self.assertEqual(p.candidate_source, "llm")

    def test_unknown_profile_falls_back_to_default(self):
        self.assertEqual(profiles.get_profile("nonsense").name, profiles.DEFAULT_PROFILE)

    def test_party_is_saliency_sourced_and_ignores_transcript(self):
        p = profiles.get_profile("party")
        self.assertEqual(p.candidate_source, "saliency")
        self.assertEqual(p.channel_weights["transcript_semantic"], 0.0)
        self.assertGreater(p.channel_weights["audio_event"], 0.0)

    def test_podcast_weights_are_transcript_dominant(self):
        w = profiles.get_profile("podcast").channel_weights
        self.assertEqual(max(w, key=w.get), "transcript_semantic")



class WaveformFromSharedWavTests(unittest.TestCase):
    def test_reads_pre_extracted_wav_directly(self):
        import struct
        import tempfile
        import wave as wave_mod

        fd, wav_path = tempfile.mkstemp(suffix=".wav")
        os.close(fd)
        try:
            with wave_mod.open(wav_path, "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(16000)
                w.writeframes(struct.pack("<4h", 0, 16384, -16384, 0))

            waveform = ae._read_waveform_16k_mono("/nonexistent.mp4", wav_path=wav_path)
            self.assertIsNotNone(waveform)
            self.assertEqual(len(waveform), 4)
            self.assertAlmostEqual(float(waveform[1]), 0.5, places=3)
        finally:
            os.unlink(wav_path)

    def test_missing_wav_falls_back_to_extraction_path(self):
        from unittest import mock

        with mock.patch.object(ae, "proc_run", return_value=mock.Mock(returncode=1, stderr="")):
            waveform = ae._read_waveform_16k_mono("/nonexistent.mp4", wav_path="/no/such/file.wav")
        self.assertIsNone(waveform)

    def test_corrupt_wav_from_ffmpeg_returns_none(self):
        from unittest import mock

        def fake_run(cmd, **kwargs):
            # ffmpeg "succeeds" but leaves a WAV that wave.open cannot parse.
            with open(cmd[cmd.index("wav") + 1], "wb") as f:
                f.write(b"not a wav")
            return mock.Mock(returncode=0, stderr="")

        with mock.patch.object(ae, "proc_run", side_effect=fake_run):
            self.assertIsNone(ae._read_waveform_16k_mono("/nonexistent.mp4"))


if __name__ == "__main__":
    unittest.main()
