"""Tests for backend.main's AI suggestion handler."""

import os
import sys
import unittest
from unittest import mock

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BACKEND_ROOT = os.path.join(ROOT, "backend")
if BACKEND_ROOT not in sys.path:
    sys.path.insert(0, BACKEND_ROOT)

import main as backend_main
from services import claude_suggest

SEGMENTS = [{"start": 0.0, "end": 10.0, "text": "hello"}]
ENERGY_DATA = [{"time": float(t), "rms_db": -30.0} for t in range(31)] + [
    {"time": 25.0, "rms_db": 0.0}
]
EVENTS_DATA = [{
    "time": 25.0,
    "laughter": 1.0,
    "cheering": 0.0,
    "screaming": 0.0,
    "speech": 0.0,
}]
CLIPS = [
    {"title": "first", "start_second": 0, "end_second": 10, "score": 15},
    {"title": "second", "start_second": 10, "end_second": 20, "score": 14},
    {"title": "reaction", "start_second": 20, "end_second": 30, "score": 13},
]


class SuggestReactionTimesTests(unittest.TestCase):
    def _run(self, params, clips=None):
        captured = {}
        emitted = {}

        def fake_suggest(**kwargs):
            captured.update(kwargs)
            return clips or [{"title": "clip", "start_second": 0, "end_second": 30}]

        def fake_emit_result(task_id, status, data=None, error=None):
            emitted.update({"task_id": task_id, "status": status, "data": data, "error": error})

        with mock.patch.object(claude_suggest, "suggest_initial_with_claude", fake_suggest), \
             mock.patch.object(claude_suggest, "_find_ai_cli_candidates", return_value=["claude"]), \
             mock.patch.object(backend_main, "emit_result", fake_emit_result), \
             mock.patch.object(backend_main, "emit_progress"):
            backend_main.handle_suggest_clips("task-1", params)
        return captured, emitted

    def test_reaction_times_are_forwarded(self):
        captured, _ = self._run({
            "segments": SEGMENTS,
            "reaction_times": [12.5, 40.0],
        })
        self.assertEqual(captured["reaction_times"], [12.5, 40.0])

    def test_cached_signals_are_reused_without_decoding(self):
        with mock.patch(
            "services.signal_cache.load_signals",
            return_value={"energy_data": ENERGY_DATA, "events_data": EVENTS_DATA},
        ), \
             mock.patch("services.audio_analyzer.extract_audio_energy") as extract_energy, \
             mock.patch("services.audio_events.extract_audio_events") as extract_events, \
             mock.patch.object(backend_main.os.path, "exists", return_value=True):
            captured, emitted = self._run(
                {"segments": SEGMENTS, "video_path": "/video.mp4", "top_n": 2},
                clips=[dict(c) for c in CLIPS],
            )

        extract_energy.assert_not_called()
        extract_events.assert_not_called()
        self.assertEqual(captured["reaction_times"], [25.0])
        self.assertEqual(captured["top_n"], 4)
        self.assertEqual(
            [clip["title"] for clip in emitted["data"]["clips"]],
            ["first", "reaction"],
        )
        self.assertGreater(emitted["data"]["clips"][1]["signal_boost"], 2)

    def test_uncached_signals_are_extracted_once_from_a_shared_wav(self):
        with mock.patch("services.signal_cache.load_signals", return_value={}), \
             mock.patch("services.signal_cache.save_signals") as save_signals, \
             mock.patch(
                 "services.audio_extract.extract_wav_16k_mono", return_value="/tmp/shared.wav"
             ) as extract_wav, \
             mock.patch(
                 "services.audio_analyzer.extract_audio_energy", return_value=ENERGY_DATA
             ) as extract_energy, \
             mock.patch(
                 "services.audio_events.extract_audio_events", return_value=EVENTS_DATA
             ) as extract_events, \
             mock.patch("services.audio_events.is_available", return_value=True), \
             mock.patch.object(backend_main.os.path, "exists", return_value=True), \
             mock.patch.object(backend_main.os, "unlink"):
            captured, _ = self._run(
                {"segments": SEGMENTS, "video_path": "/video.mp4", "top_n": 2},
                clips=[dict(c) for c in CLIPS],
            )

        extract_wav.assert_called_once_with("/video.mp4")
        extract_energy.assert_called_once_with("/video.mp4", wav_path="/tmp/shared.wav")
        extract_events.assert_called_once_with("/video.mp4", wav_path="/tmp/shared.wav")
        save_signals.assert_called_once_with(
            "/video.mp4", energy_data=ENERGY_DATA, events_data=EVENTS_DATA
        )
        self.assertEqual(captured["reaction_times"], [25.0])

    def test_missing_video_and_anchors_pass_none(self):
        captured, _ = self._run({"segments": SEGMENTS})
        self.assertIsNone(captured["reaction_times"])


if __name__ == "__main__":
    unittest.main()
