"""Tests for video_processor._choose_segment_tracks — the per-turn
face-track selector that decides which visual track represents the
active speaker for a given segment.
"""

import os
import sys
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BACKEND_ROOT = os.path.join(ROOT, "backend")
if BACKEND_ROOT not in sys.path:
    sys.path.insert(0, BACKEND_ROOT)

from services import video_processor as vp


def _face(cx: int, fw: int = 180, track_id: int = 0) -> dict:
    """Helper to build a face detection dict."""
    return {"cx": cx, "fw": fw, "track_id": track_id}


def _build_split_detections(
    left_x: int,
    right_x: int,
    duration: float,
    fps: int = 10,
) -> list:
    """Synth a split-screen detection stream with two stable tracks."""
    detections = []
    steps = int(duration * fps) + 1
    for i in range(steps):
        t = i / fps
        detections.append((t, [_face(left_x, track_id=0), _face(right_x, track_id=1)]))
    return detections


class ChooseSegmentTracksTests(unittest.TestCase):
    def test_empty_segments_returns_empty(self):
        tracks, anchors, sides = vp._choose_segment_tracks(
            segments=[],
            tracked_detections=[],
            speaker_side={},
            speaker_anchor_x={},
            width=1920,
        )
        self.assertEqual(tracks, [])
        self.assertEqual(anchors, {})
        self.assertEqual(sides, {})

    def test_no_detections_yields_none_track(self):
        segments = [(0.0, 5.0, "SPEAKER_00")]
        tracks, _, _ = vp._choose_segment_tracks(
            segments=segments,
            tracked_detections=[],
            speaker_side={},
            speaker_anchor_x={},
            width=1920,
        )
        # One entry per segment, chosen_track_id is None
        self.assertEqual(len(tracks), 1)
        self.assertIsNone(tracks[0][3])

    def test_single_speaker_picks_available_track(self):
        # One speaker talking, one face visible throughout
        detections = [
            (0.0, [_face(960, track_id=0)]),
            (0.5, [_face(960, track_id=0)]),
            (1.0, [_face(960, track_id=0)]),
        ]
        segments = [(0.0, 1.0, "SPEAKER_00")]
        tracks, anchors, sides = vp._choose_segment_tracks(
            segments=segments,
            tracked_detections=detections,
            speaker_side={},
            speaker_anchor_x={},
            width=1920,
        )
        self.assertEqual(len(tracks), 1)
        self.assertEqual(tracks[0][3], 0)  # track id 0 chosen
        self.assertIn("SPEAKER_00", anchors)
        self.assertIn("SPEAKER_00", sides)

    def test_side_hint_pulls_camera_to_correct_track(self):
        # Two faces, roughly equal frame count, but a strong side hint
        # should steer to the track on that side.
        detections = _build_split_detections(left_x=400, right_x=1500, duration=5.0)
        segments = [(0.0, 5.0, "SPEAKER_00")]
        tracks, _, _ = vp._choose_segment_tracks(
            segments=segments,
            tracked_detections=detections,
            speaker_side={"SPEAKER_00": "right"},
            speaker_anchor_x={"SPEAKER_00": 1500.0},
            width=1920,
        )
        # Should pick the right-side track (id 1)
        self.assertEqual(tracks[0][3], 1)
        # The chosen x should be on the right half
        self.assertGreater(tracks[0][4], 960)

    def test_two_speakers_learn_opposite_sides(self):
        # Two speakers, two tracks. SPEAKER_00 hinted to left; without a
        # hint, SPEAKER_01 should be inferred to the right (opposite-side
        # inference for 2-speaker clips).
        detections = _build_split_detections(left_x=400, right_x=1500, duration=10.0)
        segments = [
            (0.0, 5.0, "SPEAKER_00"),
            (5.0, 10.0, "SPEAKER_01"),
        ]
        tracks, _, sides = vp._choose_segment_tracks(
            segments=segments,
            tracked_detections=detections,
            speaker_side={"SPEAKER_00": "left"},
            speaker_anchor_x={"SPEAKER_00": 400.0},
            width=1920,
        )
        self.assertEqual(len(tracks), 2)
        self.assertEqual(sides.get("SPEAKER_00"), "left")
        self.assertEqual(sides.get("SPEAKER_01"), "right")

    def test_learned_anchors_persist_across_segments(self):
        detections = _build_split_detections(left_x=400, right_x=1500, duration=10.0)
        segments = [
            (0.0, 5.0, "SPEAKER_00"),
            (5.0, 10.0, "SPEAKER_00"),  # Same speaker, second turn
        ]
        tracks, anchors, sides = vp._choose_segment_tracks(
            segments=segments,
            tracked_detections=detections,
            speaker_side={"SPEAKER_00": "left"},
            speaker_anchor_x={"SPEAKER_00": 400.0},
            width=1920,
        )
        # Both turns should pick the same track
        self.assertEqual(tracks[0][3], tracks[1][3])
        # Anchor should be near the left cluster (EMA smoothed)
        self.assertLess(anchors["SPEAKER_00"], 960)

    def test_anchor_smoothing_on_small_drift(self):
        # Small drift between turns → EMA-smoothed anchor
        # (new_anchor_x within 18% of old → blended)
        detections = []
        for t in range(0, 10):
            # First 5s: left face at x=400
            if t < 5:
                detections.append((float(t), [_face(400, track_id=0)]))
            else:
                # Next 5s: left face drifts slightly to x=430
                detections.append((float(t), [_face(430, track_id=0)]))
        segments = [
            (0.0, 5.0, "SPEAKER_00"),
            (5.0, 10.0, "SPEAKER_00"),
        ]
        _, anchors, _ = vp._choose_segment_tracks(
            segments=segments,
            tracked_detections=detections,
            speaker_side={"SPEAKER_00": "left"},
            speaker_anchor_x={"SPEAKER_00": 400.0},
            width=1920,
        )
        # Anchor is a blend, not a raw jump
        anchor = anchors["SPEAKER_00"]
        self.assertGreater(anchor, 400)
        self.assertLess(anchor, 430)


if __name__ == "__main__":
    unittest.main()


class AnchorOnlySegmentTests(unittest.TestCase):
    """_choose_track_segment_targets must not aim the camera at a face_map
    anchor that nothing in this clip stands on.

    Segments the chosen track never appears in fall through to the speaker's
    episode-wide anchor. That anchor was taken outright, so an invented or
    stale seat pointed the camera at the wall next to the only person on
    screen and held it there for the length of the turn.

    Segments longer than 1.5s recover through the gap-points path first, so
    these use short turns to reach the anchor branch itself.
    """

    WIDTH = 1920
    CROP_W = 607

    def _targets(self, segment_tracks, detections, anchors):
        return vp._choose_track_segment_targets(
            segment_tracks=segment_tracks,
            tracked_detections=detections,
            speaker_anchor_x=anchors,
            width=self.WIDTH,
            crop_w=self.CROP_W,
        )

    def _one_person(self, cx, end, fps=10):
        return [(i / fps, [_face(cx, track_id=0)]) for i in range(int(end * fps) + 1)]

    def _holds(self, crop_x, cx):
        return crop_x <= cx <= crop_x + self.CROP_W

    def test_unstood_anchor_is_replaced_by_a_real_face(self):
        # One person at 1400 all clip. The turn's speaker has no track of
        # their own and an anchor at 400, which is the invented seat.
        targets = self._targets(
            segment_tracks=[(0.0, 1.2, "SPEAKER_01", 7, 400.0)],
            detections=self._one_person(1400, 8.0),
            anchors={"SPEAKER_01": 400.0},
        )
        self.assertEqual(len(targets), 1)
        self.assertTrue(
            self._holds(targets[0][2], 1400),
            f"crop at {targets[0][2]} holds nobody; the only face is at 1400",
        )

    def test_a_stood_anchor_is_left_alone(self):
        # Two real tiles, anchor on the right one. Somebody is standing there,
        # so the anchor survives untouched.
        targets = self._targets(
            segment_tracks=[(0.0, 1.2, "SPEAKER_01", 7, 1440.0)],
            detections=_build_split_detections(480, 1440, 8.0),
            anchors={"SPEAKER_01": 1440.0},
        )
        self.assertEqual(len(targets), 1)
        self.assertTrue(self._holds(targets[0][2], 1440))

    def test_anchor_survives_when_the_face_is_merely_off_centre(self):
        # 1250 is inside the crop the 1400 anchor asks for. Nudging the camera
        # for that would fight the tripod, so the anchor stands.
        targets = self._targets(
            segment_tracks=[(0.0, 1.2, "SPEAKER_00", 7, 1400.0)],
            detections=self._one_person(1250, 8.0),
            anchors={"SPEAKER_00": 1400.0},
        )
        self.assertEqual(targets[0][2], vp_crop_x(1400.0, self.CROP_W, self.WIDTH))

    def test_no_faces_at_all_keeps_the_anchor(self):
        # Nothing to correct towards, so the anchor is still the best guess.
        targets = self._targets(
            segment_tracks=[(0.0, 1.2, "SPEAKER_00", 3, 800.0)],
            detections=[(i / 10, []) for i in range(30)],
            anchors={"SPEAKER_00": 800.0},
        )
        self.assertEqual(targets[0][2], vp_crop_x(800.0, self.CROP_W, self.WIDTH))


def vp_crop_x(center_x: float, crop_w: int, width: int) -> int:
    """The clamp _choose_track_segment_targets applies to a chosen centre."""
    return max(0, min(int(round(center_x - crop_w / 2.0)), width - crop_w))
