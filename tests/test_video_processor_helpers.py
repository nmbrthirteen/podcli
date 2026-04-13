"""Tests for pure helper functions inside video_processor.

Deliberately tests only the side-effect-free helpers (keyframe
simplification, speaker-side resolution, face track assignment) so
the suite can run without cv2, ffmpeg, or real video files.
"""

import os
import sys
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BACKEND_ROOT = os.path.join(ROOT, "backend")
if BACKEND_ROOT not in sys.path:
    sys.path.insert(0, BACKEND_ROOT)

from services import video_processor as vp


class SimplifyKeyframesTests(unittest.TestCase):
    def test_short_input_is_unchanged(self):
        self.assertEqual(vp._simplify_keyframes([]), [])
        self.assertEqual(vp._simplify_keyframes([(0, 100)]), [(0, 100)])
        self.assertEqual(
            vp._simplify_keyframes([(0, 100), (5, 200)]),
            [(0, 100), (5, 200)],
        )

    def test_collinear_points_dropped(self):
        # Perfect line: (0,0) → (5,500) → (10,1000) → middle should drop
        keyframes = [(0, 0), (5, 500), (10, 1000)]
        out = vp._simplify_keyframes(keyframes)
        self.assertEqual(out, [(0, 0), (10, 1000)])

    def test_non_collinear_points_preserved(self):
        # (0,0), (5,800), (10,1000) — middle is far from the interpolated value
        keyframes = [(0, 0), (5, 800), (10, 1000)]
        out = vp._simplify_keyframes(keyframes)
        self.assertEqual(out, keyframes)  # all preserved

    def test_tolerance_controls_sensitivity(self):
        # Middle point is 10 units off the line
        keyframes = [(0, 0), (5, 510), (10, 1000)]
        # With loose tolerance, it's dropped
        loose = vp._simplify_keyframes(keyframes, tolerance=50)
        self.assertEqual(loose, [(0, 0), (10, 1000)])
        # With tight tolerance, it's kept
        tight = vp._simplify_keyframes(keyframes, tolerance=1)
        self.assertEqual(tight, keyframes)

    def test_always_keeps_endpoints(self):
        keyframes = [(0, 100), (1, 100), (2, 100), (3, 100)]
        out = vp._simplify_keyframes(keyframes)
        # At minimum first and last survive
        self.assertEqual(out[0], (0, 100))
        self.assertEqual(out[-1], (3, 100))


class ResolveSpeakerSidesTests(unittest.TestCase):
    def test_no_face_map_returns_empty(self):
        # Without a face_map, we cannot infer sides — must return empty dict
        sides = vp._resolve_speaker_sides(
            segments=[(0.0, 5.0, "SPEAKER_00")],
            detections=[],
            width=1920,
            face_map=None,
        )
        self.assertEqual(sides, {})

    def test_face_map_assigns_sides_based_on_cluster_x(self):
        face_map = {
            "clusters": [
                {"center_x": 400},   # left of 960 midline
                {"center_x": 1500},  # right of midline
            ],
            "speaker_mappings": {
                "SPEAKER_00": 0,
                "SPEAKER_01": 1,
            },
        }
        sides = vp._resolve_speaker_sides(
            segments=[],
            detections=[],
            width=1920,
            face_map=face_map,
        )
        self.assertEqual(sides["SPEAKER_00"], "left")
        self.assertEqual(sides["SPEAKER_01"], "right")

    def test_ignores_unmapped_speakers(self):
        face_map = {
            "clusters": [{"center_x": 400}],
            "speaker_mappings": {
                "SPEAKER_00": 0,
                "SPEAKER_GHOST": None,   # unmapped
                "SPEAKER_OOB": 99,       # index out of range
            },
        }
        sides = vp._resolve_speaker_sides(
            segments=[], detections=[], width=1920, face_map=face_map,
        )
        self.assertEqual(sides, {"SPEAKER_00": "left"})


class AssignFaceTracksTests(unittest.TestCase):
    def test_empty_detections(self):
        self.assertEqual(vp._assign_face_tracks([], width=1920), [])

    def test_stable_face_gets_stable_track_id(self):
        # Same face position across frames → same track id
        detections = [
            (0.0, [{"cx": 500, "fw": 120}]),
            (0.2, [{"cx": 505, "fw": 120}]),
            (0.4, [{"cx": 510, "fw": 120}]),
        ]
        tracked = vp._assign_face_tracks(detections, width=1920)
        self.assertEqual(len(tracked), 3)
        # All three frames should share one track id
        ids = set()
        for _t, faces in tracked:
            for f in faces:
                ids.add(f.get("track_id"))
        self.assertEqual(len(ids), 1)

    def test_distant_faces_get_different_track_ids(self):
        # Two clearly separated faces on opposite sides
        detections = [
            (0.0, [{"cx": 300, "fw": 120}, {"cx": 1500, "fw": 120}]),
            (0.2, [{"cx": 305, "fw": 120}, {"cx": 1495, "fw": 120}]),
        ]
        tracked = vp._assign_face_tracks(detections, width=1920)
        # Frame 0 should have 2 distinct track ids
        frame0_ids = {f["track_id"] for f in tracked[0][1]}
        self.assertEqual(len(frame0_ids), 2)
        # Frame 1's ids should match frame 0's (stable)
        frame1_ids = {f["track_id"] for f in tracked[1][1]}
        self.assertEqual(frame0_ids, frame1_ids)


if __name__ == "__main__":
    unittest.main()
