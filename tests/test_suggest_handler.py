"""Tests for backend.main's AI suggestion handler: reaction anchors reach the prompt."""

import os
import sys
import unittest
from unittest import mock

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BACKEND_ROOT = os.path.join(ROOT, "backend")
if BACKEND_ROOT not in sys.path:
    sys.path.insert(0, BACKEND_ROOT)

import main as backend_main

SEGMENTS = [{"start": 0.0, "end": 10.0, "text": "hello"}]


class SuggestReactionTimesTests(unittest.TestCase):
    def _run(self, params):
        captured = {}

        def fake_suggest(**kwargs):
            captured.update(kwargs)
            return [{"title": "clip", "start_second": 0, "end_second": 30}]

        with mock.patch("services.claude_suggest.suggest_initial_with_claude", fake_suggest), \
             mock.patch("services.claude_suggest._find_ai_cli_candidates", return_value=["claude"]), \
             mock.patch.object(backend_main, "emit_result"), \
             mock.patch.object(backend_main, "emit_progress"):
            backend_main.handle_suggest_clips("task-1", params)
        return captured

    def test_reaction_times_are_forwarded(self):
        captured = self._run({
            "segments": SEGMENTS,
            "reaction_times": [12.5, 40.0],
        })
        self.assertEqual(captured["reaction_times"], [12.5, 40.0])

    def test_reaction_times_are_derived_from_video_path(self):
        with mock.patch("services.audio_events.is_available", return_value=True), \
             mock.patch("services.audio_events.get_event_profile",
                        return_value={"reaction_times": [7.0]}) as profile, \
             mock.patch.object(backend_main.os.path, "exists", return_value=True):
            captured = self._run({"segments": SEGMENTS, "video_path": "/video.mp4"})

        self.assertEqual(captured["reaction_times"], [7.0])
        self.assertEqual(profile.call_args.args[0], "/video.mp4")

    def test_missing_video_and_anchors_pass_none(self):
        captured = self._run({"segments": SEGMENTS})
        self.assertIsNone(captured["reaction_times"])


if __name__ == "__main__":
    unittest.main()
