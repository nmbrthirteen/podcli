"""Tests for backend.services.audio_analyzer.compute_energy_scores."""

import os
import sys
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BACKEND_ROOT = os.path.join(ROOT, "backend")
if BACKEND_ROOT not in sys.path:
    sys.path.insert(0, BACKEND_ROOT)

from services import audio_analyzer as aa


class ComputeEnergyScoresTests(unittest.TestCase):
    def test_empty_energy_data_returns_zeros(self):
        segments = [{"start": 0, "end": 10}, {"start": 10, "end": 20}]
        self.assertEqual(aa.compute_energy_scores([], segments), [0.0, 0.0])

    def test_all_silence_returns_zeros(self):
        # All values below -60 dB threshold → treated as silence
        energy = [{"time": t, "rms_db": -80} for t in range(0, 10)]
        self.assertEqual(
            aa.compute_energy_scores(energy, [{"start": 0, "end": 5}]), [0.0]
        )

    def test_empty_segments_returns_empty_list(self):
        self.assertEqual(aa.compute_energy_scores([{"time": 0, "rms_db": -20}], []), [])

    def test_higher_segment_energy_gets_higher_score(self):
        # Quiet baseline + one loud spike segment
        energy = []
        for t in range(0, 30):
            energy.append({"time": float(t), "rms_db": -30.0})
        # Loud spike in range 10-15
        for t in range(10, 16):
            energy.append({"time": float(t), "rms_db": -10.0})

        segments = [
            {"start": 0, "end": 5},     # quiet
            {"start": 10, "end": 15},   # loud
            {"start": 20, "end": 25},   # quiet
        ]
        scores = aa.compute_energy_scores(energy, segments)
        self.assertEqual(len(scores), 3)
        self.assertGreater(scores[1], scores[0])
        self.assertGreater(scores[1], scores[2])

    def test_scores_bounded_to_0_10(self):
        # Create data that might push scores out of range
        energy = [{"time": float(t), "rms_db": -30.0} for t in range(0, 50)]
        # One huge spike
        energy.append({"time": 25.0, "rms_db": 0.0})
        segments = [{"start": 24, "end": 26}]
        scores = aa.compute_energy_scores(energy, segments)
        self.assertGreaterEqual(scores[0], 0)
        self.assertLessEqual(scores[0], 10)

    def test_segment_outside_energy_window_gets_zero(self):
        energy = [{"time": float(t), "rms_db": -20.0} for t in range(0, 10)]
        segments = [{"start": 100, "end": 110}]  # completely outside data
        self.assertEqual(aa.compute_energy_scores(energy, segments), [0.0])

    def test_ignores_silence_samples_within_segment(self):
        # A segment with loud + silent samples should only score off the loud ones
        energy = [
            {"time": 0.0, "rms_db": -20.0},
            {"time": 1.0, "rms_db": -80.0},  # silence — should be ignored
            {"time": 2.0, "rms_db": -20.0},
            {"time": 3.0, "rms_db": -40.0},  # baseline context
            {"time": 4.0, "rms_db": -40.0},
        ]
        scores = aa.compute_energy_scores(energy, [{"start": 0, "end": 2}])
        # Only 2 samples contribute — -20 is above the -40 baseline → score > 0
        self.assertGreater(scores[0], 0)

    def test_zero_variance_energy_is_handled(self):
        # All-same energy level would cause std=0 — function must avoid div-by-zero
        energy = [{"time": float(t), "rms_db": -25.0} for t in range(0, 20)]
        segments = [{"start": 5, "end": 10}]
        # Should not raise
        scores = aa.compute_energy_scores(energy, segments)
        self.assertEqual(len(scores), 1)


if __name__ == "__main__":
    unittest.main()
