import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
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

    def test_kept_caption_overlay_path_matches_remotion_contract(self):
        with tempfile.TemporaryDirectory() as td:
            output_path = os.path.join(td, "captioned.mp4")
            expected = cg._kept_caption_overlay_path(output_path)
            self.assertTrue(expected.endswith("_captions.mov"))
            self.assertIn("captioned", expected)

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

        self.assertEqual(first, (False, None))
        self.assertEqual(second, (False, None))
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

        self.assertEqual(first, (False, None))
        self.assertEqual(second, (False, None))
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



class TransitionAutofixGatingTests(unittest.TestCase):
    def test_multi_segment_cut_can_jump(self):
        self.assertTrue(cg._reframe_can_jump(
            reframe=True, crop_strategy="face",
            keep_segments=[{"start": 0, "end": 5}, {"start": 8, "end": 12}],
        ))

    def test_non_reframe_format_cannot_jump(self):
        self.assertFalse(cg._reframe_can_jump(reframe=False, crop_strategy="face"))

    def test_center_crop_cannot_jump(self):
        self.assertFalse(cg._reframe_can_jump(reframe=True, crop_strategy="center"))

    def test_face_follow_can_jump_without_speaker_labels(self):
        # whisper.cpp (the default engine) skips diarization, so the tracker
        # still snaps between faces with every word unlabeled.
        self.assertTrue(cg._reframe_can_jump(reframe=True, crop_strategy="face"))

    def test_speaker_strategies_can_jump(self):
        self.assertTrue(cg._reframe_can_jump(reframe=True, crop_strategy="speaker"))
        self.assertTrue(cg._reframe_can_jump(reframe=True, crop_strategy="speaker-hardcut"))

    def test_manual_crop_jumps_only_with_multiple_keyframes(self):
        self.assertFalse(cg._reframe_can_jump(
            reframe=True, crop_strategy="manual", crop_keyframes=[{"t": 0, "x_pct": 50}],
        ))
        self.assertTrue(cg._reframe_can_jump(
            reframe=True, crop_strategy="manual",
            crop_keyframes=[{"t": 0, "x_pct": 20}, {"t": 3, "x_pct": 80}],
        ))

    def test_default_engine_face_crop_runs_autofix(self):
        env = {k: v for k, v in os.environ.items() if k != "PODCLI_TRANSITION_AUTOFIX_PASSES"}
        with mock.patch.dict(os.environ, env, clear=True):
            passes = cg._transition_autofix_passes(
                cg._reframe_can_jump(reframe=True, crop_strategy="face", crop_keyframes=None)
            )
        self.assertEqual(passes, 2)

    def test_default_passes_gated_by_jump_potential(self):
        env = {k: v for k, v in os.environ.items() if k != "PODCLI_TRANSITION_AUTOFIX_PASSES"}
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(cg._transition_autofix_passes(True), 2)
            self.assertEqual(cg._transition_autofix_passes(False), 0)

    def test_env_override_wins(self):
        with mock.patch.dict(os.environ, {"PODCLI_TRANSITION_AUTOFIX_PASSES": "1"}):
            self.assertEqual(cg._transition_autofix_passes(False), 1)
            self.assertEqual(cg._transition_autofix_passes(True), 1)
        with mock.patch.dict(os.environ, {"PODCLI_TRANSITION_AUTOFIX_PASSES": "0"}):
            self.assertEqual(cg._transition_autofix_passes(True), 0)

    def test_env_override_is_clamped_and_validated(self):
        with mock.patch.dict(os.environ, {"PODCLI_TRANSITION_AUTOFIX_PASSES": "9"}):
            self.assertEqual(cg._transition_autofix_passes(False), 2)
        with mock.patch.dict(os.environ, {"PODCLI_TRANSITION_AUTOFIX_PASSES": "junk"}):
            self.assertEqual(cg._transition_autofix_passes(True), 2)


class OutputPathReservationTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="podcli-output-test-")
        cg._reserved_output_paths.clear()
        self.addCleanup(cg._reserved_output_paths.clear)
        self.addCleanup(shutil.rmtree, self.tmpdir, ignore_errors=True)

    def test_duplicate_title_gets_a_suffix(self):
        first = cg._reserve_output_path(self.tmpdir, "same_title_short", ".mp4")
        second = cg._reserve_output_path(self.tmpdir, "same_title_short", ".mp4")
        third = cg._reserve_output_path(self.tmpdir, "same_title_short", ".mp4")
        self.assertEqual(os.path.basename(first), "same_title_short.mp4")
        self.assertEqual(os.path.basename(second), "same_title_short-2.mp4")
        self.assertEqual(os.path.basename(third), "same_title_short-3.mp4")

    def test_concurrent_renders_of_one_title_never_share_a_file(self):
        barrier = threading.Barrier(8)

        def render(i):
            barrier.wait()
            path = cg._reserve_output_path(self.tmpdir, "duplicate_short", ".mp4")
            with open(path, "w", encoding="utf-8") as f:
                f.write(f"clip-{i}")
            return path

        with ThreadPoolExecutor(max_workers=8) as pool:
            paths = list(pool.map(render, range(8)))

        self.assertEqual(len(set(paths)), 8)
        contents = []
        for p in paths:
            self.assertTrue(os.path.exists(p))
            with open(p, encoding="utf-8") as f:
                contents.append(f.read())
        self.assertEqual(len(set(contents)), 8)

    def test_sidecar_paths_inherit_the_unique_stem(self):
        cg._reserve_output_path(self.tmpdir, "same_title_short", ".mp4")
        second = cg._reserve_output_path(self.tmpdir, "same_title_short", ".mp4")
        base, _ = os.path.splitext(second)
        self.assertTrue(base.endswith("same_title_short-2"))


if __name__ == "__main__":
    unittest.main()
