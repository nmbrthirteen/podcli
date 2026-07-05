"""Tests for backend.services.saliency pure signal functions."""

import os
import sys
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BACKEND_ROOT = os.path.join(ROOT, "backend")
if BACKEND_ROOT not in sys.path:
    sys.path.insert(0, BACKEND_ROOT)

import numpy as np

from services import saliency as sal
from services.profiles import get_profile


class PickPeaksTests(unittest.TestCase):
    def test_finds_local_maxima_above_height(self):
        curve = np.array([0, 1, 5, 1, 0, 0, 4, 0, 0, 9, 0.0])
        self.assertEqual(sal.pick_peaks(curve, 2.0, 2), [2, 6, 9])

    def test_height_filters_small_peaks(self):
        curve = np.array([0, 3, 0, 0, 9, 0.0])
        self.assertEqual(sal.pick_peaks(curve, 5.0, 1), [4])

    def test_min_gap_suppresses_nearby_lower_peak(self):
        # two peaks 2 bins apart; min_gap 3 keeps only the taller
        curve = np.array([0, 8, 0, 6, 0.0])
        self.assertEqual(sal.pick_peaks(curve, 1.0, 3), [1])

    def test_empty_curve(self):
        self.assertEqual(sal.pick_peaks(np.array([]), 1.0, 2), [])


class DilateTests(unittest.TestCase):
    def test_spike_spreads_to_neighbors(self):
        curve = np.zeros(9)
        curve[4] = 1.0
        out = sal._dilate(curve, 2)
        self.assertTrue(np.all(out[2:7] == 1.0))
        self.assertEqual(out[1], 0.0)
        self.assertEqual(out[7], 0.0)

    def test_zero_radius_is_identity(self):
        curve = np.array([0.0, 1.0, 0.0])
        self.assertTrue(np.array_equal(sal._dilate(curve, 0), curve))


class RobustZTests(unittest.TestCase):
    def test_constant_array_is_zero(self):
        out = sal._robust_z(np.array([5.0, 5.0, 5.0]))
        self.assertTrue(np.allclose(out, 0.0))

    def test_outlier_gets_high_z(self):
        out = sal._robust_z(np.array([1.0, 1.0, 1.0, 1.0, 10.0]))
        self.assertEqual(int(np.argmax(out)), 4)
        self.assertGreater(out[4], 1.0)


class FuseChannelsTests(unittest.TestCase):
    def test_renormalizes_over_present_channels(self):
        # party weights audio_event=0.4, energy=0.2; both present -> peak follows audio_event
        channels = {
            "energy": np.array([3.0, 2.0, 1.0]),
            "audio_event": np.array([0.0, 0.0, 1.0]),
        }
        fused = sal.fuse_channels(channels, get_profile("party"))
        self.assertEqual(int(np.argmax(fused)), 2)

    def test_zero_weight_channel_ignored(self):
        # podcast gives motion weight 0, so a motion-only signal must not drive the curve
        channels = {"motion": np.array([0.0, 9.0, 0.0])}
        fused = sal.fuse_channels(channels, get_profile("podcast"))
        self.assertTrue(np.allclose(fused, 0.0))


class WindowForPeakTests(unittest.TestCase):
    def setUp(self):
        self.energy_flat = np.zeros(200)
        self.party = get_profile("party")

    def test_reaction_expands_backwards_from_onset(self):
        start, end, is_reaction = sal._window_for_peak(
            100.0, 0.5, self.party, 200.0, self.energy_flat, min_dur=8, max_dur=40
        )
        self.assertTrue(is_reaction)
        # lookback 8s before, payoff 2s after
        self.assertLess(start, 100.0)
        self.assertGreaterEqual(100.0 - start, self.party.reaction_lookback_sec - 0.1)
        self.assertLessEqual(end, 100.0 + self.party.reaction_payoff_sec + 0.1)

    def test_non_reaction_is_symmetric(self):
        start, end, is_reaction = sal._window_for_peak(
            100.0, 0.0, self.party, 200.0, self.energy_flat, min_dur=8, max_dur=40
        )
        self.assertFalse(is_reaction)
        self.assertAlmostEqual((start + end) / 2, 100.0, delta=1.0)

    def test_respects_min_duration(self):
        start, end, _ = sal._window_for_peak(
            100.0, 0.5, self.party, 200.0, self.energy_flat, min_dur=12, max_dur=40
        )
        self.assertGreaterEqual(round(end - start, 1), 12.0)

    def test_clamps_to_video_bounds(self):
        start, end, _ = sal._window_for_peak(
            2.0, 0.5, self.party, 200.0, self.energy_flat, min_dur=8, max_dur=40
        )
        self.assertGreaterEqual(start, 0.0)


class PooledTests(unittest.TestCase):
    def test_pools_across_files_reaction_first_with_source(self):
        fake = {
            "a.mp4": [
                {"start_second": 10, "end_second": 18, "score": 30.0, "reasons": ["energy_peak"]},
            ],
            "b.mp4": [
                {"start_second": 5, "end_second": 15, "score": 12.0, "reasons": ["reaction"]},
            ],
        }
        orig = sal.detect_highlights
        sal.detect_highlights = lambda path, **kw: [dict(c) for c in fake[path]]
        try:
            pooled = sal.detect_highlights_pooled(["a.mp4", "b.mp4"], top_n=5)
        finally:
            sal.detect_highlights = orig
        self.assertEqual(len(pooled), 2)
        # reaction outranks the higher-scored energy peak across files
        self.assertEqual(pooled[0]["reasons"], ["reaction"])
        self.assertEqual(pooled[0]["source_file"], "b.mp4")
        self.assertTrue(all("source_file" in c for c in pooled))

    def test_top_n_caps_pool(self):
        fake = [{"start_second": i, "end_second": i + 8, "score": float(i), "reasons": ["energy_peak"]} for i in range(10)]
        orig = sal.detect_highlights
        sal.detect_highlights = lambda path, **kw: [dict(c) for c in fake]
        try:
            pooled = sal.detect_highlights_pooled(["a.mp4"], top_n=3)
        finally:
            sal.detect_highlights = orig
        self.assertEqual(len(pooled), 3)


if __name__ == "__main__":
    unittest.main()
