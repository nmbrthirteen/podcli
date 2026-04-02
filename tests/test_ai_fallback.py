import json
import os
import subprocess
import sys
import unittest
from unittest import mock


ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BACKEND_ROOT = os.path.join(ROOT, "backend")
if BACKEND_ROOT not in sys.path:
    sys.path.insert(0, BACKEND_ROOT)

from services import claude_suggest as cs
from services import content_generator as cg
from services import thumbnail_ai as tai


class AIFallbackTests(unittest.TestCase):
    def test_suggest_with_claude_retries_with_codex_after_runtime_failure(self):
        segments = [
            {"start": 0.0, "end": 12.0, "speaker": "SPEAKER_00", "text": "Warmup context."},
            {"start": 12.0, "end": 40.0, "speaker": "SPEAKER_00", "text": "The best grid connection is no grid connection."},
        ]
        progress = []
        codex_payload = json.dumps({
            "clips": [
                {
                    "title": "The best grid connection is no grid connection",
                    "start_second": 12.0,
                    "end_second": 38.0,
                    "segments": [{"start": 12.0, "end": 38.0}],
                    "duration": 26,
                    "content_type": "hot_take",
                    "scores": {"standalone": 5, "hook": 5, "relevance": 4, "quotability": 4},
                    "quote": "The best grid connection is no grid connection",
                    "why": "Strong contrarian line with clean standalone context.",
                }
            ]
        })

        with mock.patch.object(
            cs,
            "_find_ai_cli_candidates",
            return_value=[("/tmp/claude", "claude"), ("/tmp/codex", "codex")],
        ), mock.patch.object(
            cs,
            "_run_ai_command",
            side_effect=[
                subprocess.CompletedProcess(args=["claude"], returncode=1, stdout="", stderr="claude down"),
                subprocess.CompletedProcess(args=["codex"], returncode=0, stdout=codex_payload, stderr=""),
            ],
        ):
            clips = cs.suggest_with_claude(
                segments=segments,
                top_n=1,
                progress_callback=lambda _pct, msg: progress.append(msg),
            )

        self.assertIsNotNone(clips)
        self.assertEqual(len(clips), 1)
        self.assertEqual(clips[0]["_ai_engine"], "codex")
        self.assertIn("Retrying with Codex...", progress)
        self.assertIn("Codex suggested 1 clips", progress)

    def test_generate_clip_content_retries_with_codex(self):
        clip = {
            "title": "Power demand is exploding",
            "start_second": 10.0,
            "end_second": 36.0,
            "content_type": "market_landscape",
        }
        transcript_segments = [
            {"start": 9.0, "speaker": "SPEAKER_00", "text": "Power demand is exploding."},
            {"start": 18.0, "speaker": "SPEAKER_00", "text": "Data centers will need a lot more energy."},
        ]
        progress = []
        codex_text = """TITLES (8 options, 40-60 chars, keyword-first, follow title spec):
1. Power Demand Is Exploding Fast
2. Data Centers Need Much More Energy
TOP PICK: 1 — strongest hook

DESCRIPTION:
Power demand is exploding.
Guest explains why data centers need more energy.

TAGS:
power demand, data centers, energy, ai infrastructure

HASHTAGS:
#power #energy #datacenters #ai #infrastructure"""

        with mock.patch.object(
            cg,
            "_find_ai_cli_candidates",
            return_value=[("/tmp/claude", "claude"), ("/tmp/codex", "codex")],
        ), mock.patch.object(
            cg,
            "_run_ai_command",
            side_effect=[
                subprocess.CompletedProcess(args=["claude"], returncode=1, stdout="", stderr="claude down"),
                subprocess.CompletedProcess(args=["codex"], returncode=0, stdout=codex_text, stderr=""),
            ],
        ):
            result = cg.generate_clip_content(
                clip=clip,
                transcript_segments=transcript_segments,
                progress_callback=lambda _pct, msg: progress.append(msg),
            )

        self.assertIsNotNone(result)
        self.assertEqual(result["engine"], "codex")
        self.assertTrue(result["titles"])
        self.assertIn("Retrying content generation with Codex...", progress)

    def test_thumbnail_layout_retries_with_codex(self):
        codex_layout = """
Some wrapper text
{
  "line1": "POWER DEMAND",
  "line2": "IS EXPLODING",
  "box_y": "78%",
  "photo_object_position": "center 18%",
  "line1_font_size": "96px",
  "line2_font_size": "90px"
}
"""

        with mock.patch.object(
            cs,
            "_find_ai_cli_candidates",
            return_value=[("/tmp/claude", "claude"), ("/tmp/codex", "codex")],
        ), mock.patch.object(
            cs,
            "_run_ai_command",
            side_effect=[
                subprocess.CompletedProcess(args=["claude"], returncode=1, stdout="", stderr="claude down"),
                subprocess.CompletedProcess(args=["codex"], returncode=0, stdout=codex_layout, stderr=""),
            ],
        ):
            layout = tai.ask_claude_for_layout(
                title="Power demand is exploding",
                frame_path="/tmp/frame.png",
                frame_info={"face_x_pct": 50, "face_y_pct": 40, "face_w_pct": 20, "face_h_pct": 25},
                config={"enabled": True},
            )

        self.assertEqual(layout["line1"], "POWER DEMAND")
        self.assertEqual(layout["line2"], "IS EXPLODING")
        self.assertEqual(layout["box_y"], "78%")


if __name__ == "__main__":
    unittest.main()
