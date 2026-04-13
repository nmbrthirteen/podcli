"""Tests for backend.services.face_track_helpers — pure camera /
speaker-switch decision helpers.
"""

import os
import sys
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BACKEND_ROOT = os.path.join(ROOT, "backend")
if BACKEND_ROOT not in sys.path:
    sys.path.insert(0, BACKEND_ROOT)

from services import face_track_helpers as fth


class UpdateTripodCameraTests(unittest.TestCase):
    def test_no_target_clamps_within_frame(self):
        # No target → camera sits at current_center but clamped
        result = fth.update_tripod_camera(
            current_center_x=500, target_center_x=None,
            crop_w=600, video_width=1920, dt=0.1,
        )
        self.assertEqual(result, 500)

    def test_clamps_below_half_crop(self):
        # current 100 with crop_w=600 → min center = 300
        result = fth.update_tripod_camera(
            current_center_x=100, target_center_x=None,
            crop_w=600, video_width=1920, dt=0.1,
        )
        self.assertEqual(result, 300)

    def test_clamps_above_width_minus_half(self):
        # video_width - half_crop = 1920 - 300 = 1620
        result = fth.update_tripod_camera(
            current_center_x=1800, target_center_x=None,
            crop_w=600, video_width=1920, dt=0.1,
        )
        self.assertEqual(result, 1620)

    def test_force_snap_jumps_to_target(self):
        result = fth.update_tripod_camera(
            current_center_x=500, target_center_x=1200,
            crop_w=600, video_width=1920, dt=0.1, force_snap=True,
        )
        self.assertEqual(result, 1200)

    def test_small_drift_inside_safe_zone_holds(self):
        # diff = 50, safe_zone_radius = 600 * 0.22 = 132 → hold
        result = fth.update_tripod_camera(
            current_center_x=600, target_center_x=650,
            crop_w=600, video_width=1920, dt=0.1,
        )
        self.assertEqual(result, 600)

    def test_large_drift_moves_toward_target(self):
        # diff = 400 → outside safe zone → move toward it
        result = fth.update_tripod_camera(
            current_center_x=500, target_center_x=900,
            crop_w=600, video_width=1920, dt=0.1,
        )
        # Should have moved right but not snapped all the way
        self.assertGreater(result, 500)
        self.assertLess(result, 900)

    def test_movement_is_bounded_by_speed(self):
        # Very fast diff, small dt → step limited by max speed × dt
        result = fth.update_tripod_camera(
            current_center_x=500, target_center_x=1800,  # huge diff → fast_speed
            crop_w=600, video_width=1920, dt=0.1,
        )
        # fast_speed=360, dt=0.1 → max step 36 → 500 + 36 = 536
        self.assertEqual(result, 536)


class ChooseCameraSpeakerTests(unittest.TestCase):
    def test_no_transcript_speaker_holds(self):
        self.assertEqual(
            fth.choose_camera_speaker(
                transcript_speaker=None,
                transcript_duration=5.0,
                active_speaker="A",
                pending_speaker=None,
                pending_count=0,
            ),
            ("A", None, 0, False),
        )

    def test_first_speaker_becomes_active(self):
        self.assertEqual(
            fth.choose_camera_speaker(
                transcript_speaker="A",
                transcript_duration=5.0,
                active_speaker=None,
                pending_speaker=None,
                pending_count=0,
            ),
            ("A", None, 0, True),
        )

    def test_same_speaker_continues(self):
        self.assertEqual(
            fth.choose_camera_speaker(
                transcript_speaker="A",
                transcript_duration=5.0,
                active_speaker="A",
                pending_speaker="B",
                pending_count=2,
            ),
            ("A", None, 0, False),
        )

    def test_brief_interjection_ignored(self):
        # transcript_duration < min_turn_duration → hold
        self.assertEqual(
            fth.choose_camera_speaker(
                transcript_speaker="B",
                transcript_duration=1.0,  # brief
                active_speaker="A",
                pending_speaker=None,
                pending_count=0,
            ),
            ("A", None, 0, False),
        )

    def test_new_speaker_enters_pending(self):
        cam, pending, count, switched = fth.choose_camera_speaker(
            transcript_speaker="B",
            transcript_duration=5.0,
            active_speaker="A",
            pending_speaker=None,
            pending_count=0,
        )
        self.assertEqual(cam, "A")
        self.assertEqual(pending, "B")
        self.assertEqual(count, 1)
        self.assertFalse(switched)

    def test_pending_confirmed_after_confirmation_frames(self):
        # Previous pending=B, count=2; with confirmation_frames=3 the
        # third sighting commits the switch.
        cam, pending, count, switched = fth.choose_camera_speaker(
            transcript_speaker="B",
            transcript_duration=5.0,
            active_speaker="A",
            pending_speaker="B",
            pending_count=2,
            confirmation_frames=3,
        )
        self.assertEqual(cam, "B")
        self.assertEqual(pending, None)
        self.assertTrue(switched)

    def test_different_pending_resets_count(self):
        # Pending was B=1, now transcript speaker is C → reset to C=1
        cam, pending, count, switched = fth.choose_camera_speaker(
            transcript_speaker="C",
            transcript_duration=5.0,
            active_speaker="A",
            pending_speaker="B",
            pending_count=1,
        )
        self.assertEqual(pending, "C")
        self.assertEqual(count, 1)
        self.assertFalse(switched)


class SafeDefaultCenterTests(unittest.TestCase):
    def test_uses_first_speaker_anchor_when_available(self):
        self.assertEqual(
            fth.safe_default_center(
                width=1920, crop_w=600,
                face_map=None, has_any_split=False,
                first_speaker="A",
                speaker_anchor_x={"A": 800.0},
            ),
            800.0,
        )

    def test_uses_largest_face_map_cluster(self):
        face_map = {
            "clusters": [
                {"center_x": 400, "count": 10},
                {"center_x": 1500, "count": 50},  # largest
            ],
        }
        self.assertEqual(
            fth.safe_default_center(
                width=1920, crop_w=600,
                face_map=face_map, has_any_split=True,
                first_speaker=None,
                speaker_anchor_x={},
            ),
            1500.0,
        )

    def test_non_split_returns_center(self):
        self.assertEqual(
            fth.safe_default_center(
                width=1920, crop_w=600,
                face_map=None, has_any_split=False,
                first_speaker=None, speaker_anchor_x={},
            ),
            960.0,
        )

    def test_split_with_no_map_returns_left_quarter(self):
        # Safer than the seam at the exact center.
        self.assertEqual(
            fth.safe_default_center(
                width=1920, crop_w=600,
                face_map=None, has_any_split=True,
                first_speaker=None, speaker_anchor_x={},
            ),
            480.0,
        )


class ClampAwayFromDeadZoneTests(unittest.TestCase):
    def test_non_split_is_noop(self):
        self.assertEqual(
            fth.clamp_away_from_dead_zone(
                crop_x=100, crop_w=600, width=1920,
                face_map={"clusters": []}, has_any_split=False,
            ),
            100,
        )

    def test_no_face_map_is_noop(self):
        self.assertEqual(
            fth.clamp_away_from_dead_zone(
                crop_x=660, crop_w=600, width=1920,
                face_map=None, has_any_split=True,
            ),
            660,
        )

    def test_crop_far_from_seam_passes_through(self):
        # crop_center = 100 + 300 = 400; mid_x = 960; distance = 560 > margin
        face_map = {"clusters": [{"center_x": 400}, {"center_x": 1500}]}
        self.assertEqual(
            fth.clamp_away_from_dead_zone(
                crop_x=100, crop_w=600, width=1920,
                face_map=face_map, has_any_split=True,
            ),
            100,
        )

    def test_crop_near_seam_snaps_to_nearest_cluster(self):
        # crop_x=660 → crop_center=960 (exactly on seam); nearest cluster = 1500
        face_map = {"clusters": [{"center_x": 400}, {"center_x": 1500}]}
        snapped = fth.clamp_away_from_dead_zone(
            crop_x=660, crop_w=600, width=1920,
            face_map=face_map, has_any_split=True,
        )
        # Should have moved off the seam
        self.assertNotEqual(snapped, 660)
        # crop_x still keeps the crop window on-frame
        self.assertLessEqual(snapped + 600, 1920)
        self.assertGreaterEqual(snapped, 0)

    def test_near_seam_with_no_clusters_returns_left_quarter(self):
        face_map = {"clusters": []}
        result = fth.clamp_away_from_dead_zone(
            crop_x=660, crop_w=600, width=1920,
            face_map=face_map, has_any_split=True,
        )
        # width // 4 - crop_w // 2 = 480 - 300 = 180
        self.assertEqual(result, 180)


class UpgradeSpeakerMappingsTests(unittest.TestCase):
    def test_clears_old_mappings(self):
        original = {
            "clusters": [{"center_x": 400}],
            "speaker_mappings": {"A": 0, "B": 1},
        }
        result = fth.upgrade_speaker_mappings(original)
        self.assertEqual(result["speaker_mappings"], {})
        self.assertTrue(result["_mappings_v2"])

    def test_preserves_clusters(self):
        original = {
            "clusters": [{"center_x": 400}, {"center_x": 1500}],
            "speaker_mappings": {"A": 0},
        }
        result = fth.upgrade_speaker_mappings(original)
        self.assertEqual(len(result["clusters"]), 2)

    def test_does_not_mutate_input(self):
        original = {
            "clusters": [],
            "speaker_mappings": {"A": 0},
        }
        fth.upgrade_speaker_mappings(original)
        # Original should still have the old mapping
        self.assertEqual(original["speaker_mappings"], {"A": 0})


class ReExportTests(unittest.TestCase):
    def test_video_processor_reexports_all_helpers(self):
        from services import video_processor as vp
        self.assertIs(vp._update_tripod_camera, fth.update_tripod_camera)
        self.assertIs(vp._choose_camera_speaker, fth.choose_camera_speaker)
        self.assertIs(vp._safe_default_center, fth.safe_default_center)
        self.assertIs(vp._clamp_away_from_dead_zone, fth.clamp_away_from_dead_zone)
        self.assertIs(vp._upgrade_speaker_mappings, fth.upgrade_speaker_mappings)


if __name__ == "__main__":
    unittest.main()
