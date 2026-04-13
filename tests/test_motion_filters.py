"""Tests for backend.services.motion_filters — pure string builders."""

import os
import sys
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BACKEND_ROOT = os.path.join(ROOT, "backend")
if BACKEND_ROOT not in sys.path:
    sys.path.insert(0, BACKEND_ROOT)

from services import motion_filters as mf


class BuildCamExprTests(unittest.TestCase):
    def test_empty_returns_none(self):
        self.assertIsNone(mf.build_cam_expr([], duration=5.0, is_split=False))

    def test_single_keyframe_returns_constant(self):
        # One keyframe → constant expression
        self.assertEqual(
            mf.build_cam_expr([(0.0, 420)], duration=5.0, is_split=False),
            "420",
        )

    def test_t0_is_back_filled(self):
        # First keyframe at t=0.5 → a (0, x) entry is prepended
        expr = mf.build_cam_expr([(0.5, 420), (2.0, 420)], duration=5.0, is_split=False)
        self.assertIsNotNone(expr)
        self.assertIn("0.000", expr)

    def test_returns_none_when_too_many_parts(self):
        # Force more parts than max_parts → None (too complex)
        keyframes = [(i * 0.1, i * 100) for i in range(20)]
        self.assertIsNone(
            mf.build_cam_expr(keyframes, duration=2.0, is_split=False, max_parts=5)
        )

    def test_negligible_movement_produces_hold(self):
        # Jump < 2 → hold at x0
        expr = mf.build_cam_expr(
            [(0.0, 100), (1.0, 101)], duration=2.0, is_split=False
        )
        self.assertIsNotNone(expr)
        self.assertIn("100", expr)


class MotionWindowsFromKeyframesTests(unittest.TestCase):
    def test_no_keyframes_returns_empty(self):
        self.assertEqual(mf.motion_windows_from_keyframes([]), [])

    def test_small_jumps_are_filtered(self):
        # Jump = 10 < min_jump=60 → filtered out
        keyframes = [(0.0, 100), (0.5, 110)]
        self.assertEqual(mf.motion_windows_from_keyframes(keyframes), [])

    def test_large_jump_creates_window(self):
        keyframes = [(0.0, 100), (0.2, 200)]
        windows = mf.motion_windows_from_keyframes(keyframes, min_jump=50)
        self.assertEqual(windows, [(0.0, 0.2)])

    def test_slow_jumps_are_excluded(self):
        # A huge but slow transition > max_window_duration should not count
        keyframes = [(0.0, 100), (2.0, 300)]
        self.assertEqual(
            mf.motion_windows_from_keyframes(keyframes, max_window_duration=0.5), []
        )

    def test_instant_jumps_are_excluded(self):
        # dt <= 0.01 → excluded (not a real window)
        keyframes = [(0.0, 100), (0.005, 200)]
        self.assertEqual(mf.motion_windows_from_keyframes(keyframes), [])

    def test_too_many_windows_returns_empty(self):
        # If we exceed max_windows, fall back to an empty list
        # (too noisy to blur-annotate)
        keyframes = []
        t = 0.0
        for _ in range(20):
            keyframes.append((t, 0))
            keyframes.append((t + 0.1, 200))
            t += 0.3
        self.assertEqual(
            mf.motion_windows_from_keyframes(keyframes, max_windows=5), []
        )


class ExpandMotionWindowsTests(unittest.TestCase):
    def test_empty_returns_empty(self):
        self.assertEqual(mf.expand_motion_windows([]), [])

    def test_single_window_gets_padded(self):
        result = mf.expand_motion_windows(
            [(1.0, 2.0)], pad_before=0.1, pad_after=0.2
        )
        self.assertEqual(result, [(0.9, 2.2)])

    def test_t0_padding_clamped_to_zero(self):
        result = mf.expand_motion_windows(
            [(0.05, 1.0)], pad_before=0.5, pad_after=0.1
        )
        # Start clamped to 0.0
        self.assertEqual(result[0][0], 0.0)

    def test_overlapping_windows_merge(self):
        # Two windows with overlapping padded ranges should merge
        result = mf.expand_motion_windows(
            [(1.0, 2.0), (2.05, 3.0)], pad_before=0.05, pad_after=0.1
        )
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0][0], 0.95)
        self.assertGreaterEqual(result[0][1], 3.1)

    def test_non_overlapping_windows_stay_separate(self):
        result = mf.expand_motion_windows(
            [(1.0, 2.0), (5.0, 6.0)], pad_before=0.05, pad_after=0.1
        )
        self.assertEqual(len(result), 2)


class BuildMotionBlurFilterTests(unittest.TestCase):
    def test_no_motion_returns_empty_string(self):
        self.assertEqual(mf.build_motion_blur_filter([(0.0, 100), (1.0, 100)]), "")

    def test_motion_produces_gblur_chain(self):
        keyframes = [(0.0, 100), (0.2, 200)]
        result = mf.build_motion_blur_filter(keyframes, min_jump=50)
        self.assertIn("gblur", result)
        self.assertIn("sigma=", result)
        # Should have both outer and core blur layers
        self.assertEqual(result.count("gblur"), 2)

    def test_enable_expressions_use_between(self):
        keyframes = [(0.0, 100), (0.2, 200)]
        result = mf.build_motion_blur_filter(keyframes, min_jump=50)
        self.assertIn("between(t", result)


class BuildMotionZoomFilterTests(unittest.TestCase):
    def test_no_motion_returns_empty_string(self):
        self.assertEqual(
            mf.build_motion_zoom_filter(
                [(0.0, 100), (1.0, 100)], target_w=1080, target_h=1920
            ),
            "",
        )

    def test_motion_produces_scale_plus_crop(self):
        keyframes = [(0.0, 100), (0.2, 200)]
        result = mf.build_motion_zoom_filter(
            keyframes, target_w=1080, target_h=1920, min_jump=50
        )
        self.assertIn("scale=", result)
        self.assertIn("crop=1080:1920", result)
        self.assertIn("1+0.0180", result)


class SimplifyKeyframesTests(unittest.TestCase):
    """Parity check against the video_processor_helpers tests — ensures
    the extraction kept the same behavior."""

    def test_collinear_middle_dropped(self):
        self.assertEqual(
            mf.simplify_keyframes([(0, 0), (5, 500), (10, 1000)]),
            [(0, 0), (10, 1000)],
        )

    def test_tolerance_loose(self):
        self.assertEqual(
            mf.simplify_keyframes([(0, 0), (5, 510), (10, 1000)], tolerance=50),
            [(0, 0), (10, 1000)],
        )


class ReExportTests(unittest.TestCase):
    def test_video_processor_reexports_motion_helpers(self):
        from services import video_processor as vp
        self.assertIs(vp._build_cam_expr, mf.build_cam_expr)
        self.assertIs(vp._motion_windows_from_keyframes, mf.motion_windows_from_keyframes)
        self.assertIs(vp._expand_motion_windows, mf.expand_motion_windows)
        self.assertIs(vp._build_motion_blur_filter, mf.build_motion_blur_filter)
        self.assertIs(vp._build_motion_zoom_filter, mf.build_motion_zoom_filter)
        self.assertIs(vp._simplify_keyframes, mf.simplify_keyframes)


if __name__ == "__main__":
    unittest.main()
