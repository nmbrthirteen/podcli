"""find_moments_from_text: parse AI output into clip dicts, with the AI CLI mocked."""

import json
import os
import sys
import types
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BACKEND_ROOT = os.path.join(ROOT, "backend")
if BACKEND_ROOT not in sys.path:
    sys.path.insert(0, BACKEND_ROOT)

from services import claude_suggest as cs

SEGMENTS = [
    {"start": 10.0, "end": 20.0, "text": "We talked about discipline and habits.", "speaker": "A"},
    {"start": 20.0, "end": 60.0, "text": "The moment I realized failure was the turning point.", "speaker": "B"},
]


def _fake_run(**kwargs):
    payload = {
        "clips": [
            {
                "title": "The turning point",
                "start_second": 20.0,
                "end_second": 58.0,
                "segments": [{"start": 20.0, "end": 58.0}],
                "content_type": "guest_story",
                "scores": {"standalone": 5, "hook": 5, "relevance": 4, "quotability": 4},
                "quote": "Failure was the turning point",
                "why": "Directly matches the pasted moment",
            }
        ]
    }
    return types.SimpleNamespace(returncode=0, stdout=json.dumps(payload), stderr="")


class FindMomentsTests(unittest.TestCase):
    def setUp(self):
        self._orig_candidates = cs._find_ai_cli_candidates
        self._orig_run = cs._run_ai_command
        cs._find_ai_cli_candidates = lambda: [("/usr/bin/claude", "claude")]
        cs._run_ai_command = lambda **kw: _fake_run(**kw)

    def tearDown(self):
        cs._find_ai_cli_candidates = self._orig_candidates
        cs._run_ai_command = self._orig_run

    def test_finds_and_shapes_moment(self):
        clips = cs.find_moments_from_text("the turning point", SEGMENTS, [])
        self.assertEqual(len(clips), 1)
        c = clips[0]
        self.assertEqual(c["start_second"], 20.0)
        self.assertEqual(c["end_second"], 58.0)
        self.assertEqual(c["title"], "The turning point")
        self.assertEqual(c["segments"], [{"start": 20.0, "end": 58.0}])
        self.assertTrue(c["suggested_caption_style"])
        self.assertGreater(c["score"], 0)

    def test_no_ai_cli_returns_empty(self):
        cs._find_ai_cli_candidates = lambda: []
        self.assertEqual(cs.find_moments_from_text("x", SEGMENTS, []), [])

    def test_progress_callback_invoked(self):
        seen = []
        cs.find_moments_from_text("x", SEGMENTS, [], progress_callback=lambda p, m: seen.append((p, m)))
        self.assertTrue(seen)


if __name__ == "__main__":
    unittest.main()
