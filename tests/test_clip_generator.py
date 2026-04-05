import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BACKEND_ROOT = os.path.join(ROOT, "backend")
if BACKEND_ROOT not in sys.path:
    sys.path.insert(0, BACKEND_ROOT)

from services import clip_generator as cg


class ClipGeneratorTests(unittest.TestCase):
    def setUp(self):
        self._orig_remotion_available = cg._remotion_available
        cg._remotion_available = None

    def tearDown(self):
        cg._remotion_available = self._orig_remotion_available

    def _fake_exists(self, real_exists):
        def _exists(path):
            if path.endswith(os.path.join("remotion", "render.mjs")):
                return True
            if path.endswith(os.path.join("remotion-bundle", "index.html")):
                return True
            return real_exists(path)
        return _exists

    def test_remotion_runtime_failure_does_not_disable_future_clips(self):
        real_exists = os.path.exists
        fail_result = subprocess.CompletedProcess(
            args=["node"],
            returncode=1,
            stdout="Error: transient render failure",
            stderr="",
        )

        with tempfile.TemporaryDirectory() as td:
            video_path = os.path.join(td, "video.mp4")
            output_path = os.path.join(td, "captioned.mp4")
            with open(video_path, "wb"):
                pass

            with mock.patch.object(cg.os.path, "exists", side_effect=self._fake_exists(real_exists)), \
                 mock.patch.object(cg.shutil, "which", return_value="/usr/bin/node"), \
                 mock.patch("subprocess.run", return_value=fail_result) as mock_run:
                first = cg._render_with_remotion(
                    video_path=video_path,
                    words=[{"word": "hello", "start": 0.0, "end": 0.5}],
                    caption_style="branded",
                    output_path=output_path,
                )
                second = cg._render_with_remotion(
                    video_path=video_path,
                    words=[{"word": "world", "start": 0.5, "end": 1.0}],
                    caption_style="branded",
                    output_path=output_path,
                )

        self.assertFalse(first)
        self.assertFalse(second)
        self.assertIsNone(cg._remotion_available)
        self.assertGreaterEqual(mock_run.call_count, 4)

    def test_remotion_timeout_does_not_disable_future_clips(self):
        real_exists = os.path.exists

        with tempfile.TemporaryDirectory() as td:
            video_path = os.path.join(td, "video.mp4")
            output_path = os.path.join(td, "captioned.mp4")
            with open(video_path, "wb"):
                pass

            timeout_exc = subprocess.TimeoutExpired(cmd=["node"], timeout=600)
            with mock.patch.object(cg.os.path, "exists", side_effect=self._fake_exists(real_exists)), \
                 mock.patch.object(cg.shutil, "which", return_value="/usr/bin/node"), \
                 mock.patch("subprocess.run", side_effect=timeout_exc) as mock_run:
                first = cg._render_with_remotion(
                    video_path=video_path,
                    words=[{"word": "hello", "start": 0.0, "end": 0.5}],
                    caption_style="branded",
                    output_path=output_path,
                )
                second = cg._render_with_remotion(
                    video_path=video_path,
                    words=[{"word": "world", "start": 0.5, "end": 1.0}],
                    caption_style="branded",
                    output_path=output_path,
                )

        self.assertFalse(first)
        self.assertFalse(second)
        self.assertIsNone(cg._remotion_available)
        self.assertGreaterEqual(mock_run.call_count, 4)

    def test_trim_weak_opening_cuts_initial_dead_air(self):
        words = [
            {"word": "We", "start": 3.2, "end": 3.4},
            {"word": "built", "start": 3.4, "end": 3.8},
        ]
        trimmed = cg._trim_weak_opening(words, start_second=0.0, end_second=12.0)
        self.assertEqual(trimmed, 3.0)

    def test_trim_weak_opening_skips_setup_words(self):
        words = [
            {"word": "So", "start": 10.0, "end": 10.2},
            {"word": "yeah", "start": 10.2, "end": 10.35},
            {"word": "we", "start": 10.5, "end": 10.62},
            {"word": "launched", "start": 10.62, "end": 10.95},
        ]
        trimmed = cg._trim_weak_opening(words, start_second=10.0, end_second=24.0)
        self.assertGreaterEqual(trimmed, 10.35)

    def test_trim_weak_opening_preserves_question_hook(self):
        words = [
            {"word": "Why?", "start": 5.0, "end": 5.2},
            {"word": "would", "start": 5.2, "end": 5.35},
            {"word": "anyone", "start": 5.35, "end": 5.6},
        ]
        trimmed = cg._trim_weak_opening(words, start_second=5.0, end_second=20.0)
        self.assertEqual(trimmed, 5.0)

    def test_select_problematic_scene_cuts_flags_tail_and_clusters(self):
        cuts = [5.0, 10.0, 10.7, 18.4]
        flagged = cg._select_problematic_scene_cuts(cuts, duration=20.0)
        self.assertIn(10.0, flagged)
        self.assertIn(10.7, flagged)
        self.assertIn(18.4, flagged)
        self.assertNotIn(5.0, flagged)

    def test_select_problematic_scene_cuts_returns_empty_when_clean(self):
        cuts = [3.0, 9.0, 15.0]
        flagged = cg._select_problematic_scene_cuts(cuts, duration=30.0)
        self.assertEqual(flagged, [])

    def test_auto_fix_transition_jumps_is_bounded_by_max_passes(self):
        with mock.patch.object(cg, "_get_media_duration", return_value=40.0), \
             mock.patch.object(cg, "_detect_scene_cuts", return_value=[10.0, 10.6, 38.8]) as detect_mock, \
             mock.patch.object(cg, "_apply_local_transition_smoothing", return_value=True) as smooth_mock, \
             mock.patch.object(cg.os, "replace") as replace_mock:
            fixed = cg._auto_fix_transition_jumps("/tmp/fake.mp4", max_passes=2)

        self.assertTrue(fixed)
        self.assertEqual(smooth_mock.call_count, 2)
        self.assertEqual(detect_mock.call_count, 2)
        self.assertEqual(replace_mock.call_count, 2)


if __name__ == "__main__":
    unittest.main()
