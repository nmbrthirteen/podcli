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


class CropCenterKeepingFacesVisibleTests(unittest.TestCase):
    CROP_W = 607   # 9:16 out of a 1920x1080 source
    WIDTH = 1920

    def _center(self, xs):
        return fth.crop_center_keeping_faces_visible(xs, self.CROP_W, self.WIDTH)

    def test_unimodal_run_lands_on_the_group(self):
        xs = [940, 950, 960, 970, 980]
        center = self._center(xs)
        self.assertLess(abs(center - 960), 30)

    def test_bimodal_run_picks_the_bigger_side_not_the_gap(self):
        # A run that spans a layout change: four samples fullscreen at 960,
        # two on a split-screen tile at 1500. The median is 1230, which on a
        # 607-wide crop holds on the wall between them and shows no one.
        xs = [960, 955, 965, 950, 1500, 1505]
        center = self._center(xs)
        self.assertGreaterEqual(
            sum(1 for x in xs if abs(x - center) < self.CROP_W / 2), 4
        )
        self.assertLess(abs(center - 960), 60)

    def test_never_returns_a_center_with_nobody_in_frame(self):
        # The exact shape that rendered as a hold on empty wall: two tiles far
        # apart, nothing between them.
        xs = [480, 470, 490, 1440, 1450, 1430]
        center = self._center(xs)
        self.assertTrue(any(abs(x - center) < self.CROP_W / 2 for x in xs))

    def test_clamps_into_the_frame(self):
        center = self._center([20, 25, 30])
        self.assertGreaterEqual(center - self.CROP_W / 2, 0)

    def test_no_faces_falls_back_to_center(self):
        self.assertEqual(self._center([]), self.WIDTH / 2)


class SeatsFromFramesTests(unittest.TestCase):
    WIDTH = 1920

    def test_two_tiles_are_two_seats(self):
        frames = [[480, 1440]] * 20
        self.assertEqual(fth.seats_from_frames(frames, self.WIDTH), (480, 1440))

    def test_one_person_wandering_across_the_midline_is_one_seat(self):
        # The bug this replaced: 40 solo frames, some left of centre and some
        # right of it, were counted as two people and rendered as a cut
        # between a face and a wall.
        frames = [[900]] * 20 + [[1020]] * 20
        self.assertIsNone(fth.seats_from_frames(frames, self.WIDTH))

    def test_two_heads_sharing_one_camera_are_one_seat(self):
        frames = [[900, 1050]] * 20
        self.assertIsNone(fth.seats_from_frames(frames, self.WIDTH))

    def test_a_few_false_positives_cannot_seat_a_second_person(self):
        frames = [[960]] * 100 + [[300, 1600]] * 3
        self.assertIsNone(fth.seats_from_frames(frames, self.WIDTH))

    def test_mixed_layout_still_finds_both_seats(self):
        # Riverside-style: sometimes both tiles, sometimes one fullscreen.
        frames = ([[480, 1440]] * 15) + ([[960]] * 25)
        self.assertEqual(fth.seats_from_frames(frames, self.WIDTH), (480, 1440))

    def test_no_frame_ever_held_two_faces(self):
        self.assertIsNone(fth.seats_from_frames([[960]] * 50, self.WIDTH))


class ReExportTests(unittest.TestCase):
    def test_video_processor_reexports_all_helpers(self):
        from services import video_processor as vp
        self.assertIs(vp._update_tripod_camera, fth.update_tripod_camera)
        self.assertIs(vp._choose_camera_speaker, fth.choose_camera_speaker)
        self.assertIs(vp._safe_default_center, fth.safe_default_center)
        self.assertIs(vp._clamp_away_from_dead_zone, fth.clamp_away_from_dead_zone)
        self.assertIs(vp._upgrade_speaker_mappings, fth.upgrade_speaker_mappings)
        self.assertIs(
            vp._crop_center_keeping_faces_visible,
            fth.crop_center_keeping_faces_visible,
        )
        self.assertIs(vp._clip_layout_is_mixed, fth.clip_layout_is_mixed)
        self.assertIs(vp._followed_face_cx_at, fth.followed_face_cx_at)


if __name__ == "__main__":
    unittest.main()


class FollowedFaceCxAtTests(unittest.TestCase):
    """The crop validator must ask where the *followed speaker* is, not where
    the biggest face is.

    On the recording this was written for, the two speakers sit at very
    different distances from their own cameras: 845px of face against 518px.
    Taking the larger one made a correct crop onto the quieter speaker look
    like a crop onto nobody, so the validator pulled every keyframe back onto
    the same person and the camera stopped switching for a 29-second turn.
    """

    def _split(self, duration=10.0, fps=10):
        # Left speaker has the much larger face, as the real recording does.
        return [
            (i / fps, [
                {"cx": 950, "fw": 845, "track_id": 0},
                {"cx": 2880, "fw": 518, "track_id": 1},
            ])
            for i in range(int(duration * fps) + 1)
        ]

    def test_follows_the_smaller_face_when_it_is_the_speaker(self):
        tracks = [(0.0, 10.0, "SPEAKER_R", 1, 2880.0)]
        cx = fth.followed_face_cx_at(5.0, self._split(), tracks)
        self.assertEqual(cx, 2880.0)

    def test_follows_the_larger_face_when_it_is_the_speaker(self):
        tracks = [(0.0, 10.0, "SPEAKER_L", 0, 950.0)]
        cx = fth.followed_face_cx_at(5.0, self._split(), tracks)
        self.assertEqual(cx, 950.0)

    def test_picks_the_track_for_the_turn_that_covers_the_time(self):
        tracks = [(0.0, 4.0, "SPEAKER_L", 0, 950.0), (4.0, 10.0, "SPEAKER_R", 1, 2880.0)]
        self.assertEqual(fth.followed_face_cx_at(2.0, self._split(), tracks), 950.0)
        self.assertEqual(fth.followed_face_cx_at(8.0, self._split(), tracks), 2880.0)

    def test_falls_back_to_the_prominent_face_when_the_speaker_is_absent(self):
        # Followed track 1 is never on screen; any face beats none.
        detections = [(i / 10, [{"cx": 950, "fw": 845, "track_id": 0}]) for i in range(50)]
        tracks = [(0.0, 5.0, "SPEAKER_R", 1, 2880.0)]
        self.assertEqual(fth.followed_face_cx_at(2.0, detections, tracks), 950.0)

    def test_uses_the_fallback_track_outside_every_turn(self):
        tracks = [(0.0, 2.0, "SPEAKER_L", 0, 950.0)]
        cx = fth.followed_face_cx_at(6.0, self._split(), tracks, fallback_track_id=1)
        self.assertEqual(cx, 2880.0)

    def test_no_detections_nearby_gives_nothing(self):
        detections = [(0.0, [{"cx": 950, "fw": 845, "track_id": 0}])]
        tracks = [(0.0, 10.0, "SPEAKER_L", 0, 950.0)]
        self.assertIsNone(fth.followed_face_cx_at(9.0, detections, tracks))
