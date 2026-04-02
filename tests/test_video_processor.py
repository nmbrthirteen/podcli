import os
import sys
import unittest
from unittest import mock


ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BACKEND_ROOT = os.path.join(ROOT, "backend")
if BACKEND_ROOT not in sys.path:
    sys.path.insert(0, BACKEND_ROOT)

from services import video_processor as vp


class VideoProcessorTests(unittest.TestCase):
    def test_assign_face_tracks_keeps_ids_stable_across_frames(self):
        tracked = vp._assign_face_tracks(
            detections=[
                (0.0, [{"cx": 320, "fw": 180}, {"cx": 1420, "fw": 200}]),
                (0.1, [{"cx": 338, "fw": 176}, {"cx": 1402, "fw": 198}]),
                (0.2, [{"cx": 356, "fw": 172}, {"cx": 1388, "fw": 194}]),
            ],
            width=1920,
        )

        left_ids = [faces[0]["track_id"] for _, faces in tracked]
        right_ids = [faces[1]["track_id"] for _, faces in tracked]

        self.assertEqual(left_ids, [left_ids[0], left_ids[0], left_ids[0]])
        self.assertEqual(right_ids, [right_ids[0], right_ids[0], right_ids[0]])
        self.assertNotEqual(left_ids[0], right_ids[0])

    def test_assign_face_tracks_matches_center_solo_to_similar_size_split_face(self):
        tracked = vp._assign_face_tracks(
            detections=[
                (0.0, [{"cx": 1054, "cy": 643, "fw": 370}]),
                (0.8, [{"cx": 553, "cy": 655, "fw": 376}, {"cx": 1551, "cy": 458, "fw": 266}]),
            ],
            width=1920,
        )

        solo_id = tracked[0][1][0]["track_id"]
        split_left_id = tracked[1][1][0]["track_id"]
        split_right_id = tracked[1][1][1]["track_id"]

        self.assertEqual(solo_id, split_left_id)
        self.assertNotEqual(solo_id, split_right_id)

    def test_choose_segment_tracks_prefers_speaker_anchor_over_reaction_track(self):
        tracked_detections = [
            (0.0, [{"cx": 320, "fw": 200, "track_id": 0}]),
            (0.4, [{"cx": 330, "fw": 205, "track_id": 0}]),
            (0.8, [{"cx": 340, "fw": 198, "track_id": 0}]),
            (1.2, [{"cx": 320, "fw": 200, "track_id": 0}, {"cx": 1480, "fw": 240, "track_id": 1}]),
            (1.6, [{"cx": 1490, "fw": 238, "track_id": 1}]),
            (2.0, [{"cx": 1475, "fw": 235, "track_id": 1}]),
        ]

        segment_tracks, anchors, sides = vp._choose_segment_tracks(
            segments=[(0.0, 2.5, "SPEAKER_03")],
            tracked_detections=tracked_detections,
            speaker_side={"SPEAKER_03": "right"},
            speaker_anchor_x={"SPEAKER_03": 1500},
            width=1920,
        )

        self.assertEqual(segment_tracks[0][3], 1)
        self.assertGreater(anchors["SPEAKER_03"], 1200)
        self.assertEqual(sides["SPEAKER_03"], "right")

    def test_choose_segment_tracks_overrides_wrong_global_side_hint_with_local_evidence(self):
        tracked_detections = [
            (0.0, [{"cx": 1040, "fw": 370, "track_id": 0}]),
            (0.5, [{"cx": 1025, "fw": 365, "track_id": 0}]),
            (1.0, [{"cx": 553, "fw": 376, "track_id": 0}, {"cx": 1551, "fw": 266, "track_id": 1}]),
            (1.5, [{"cx": 560, "fw": 382, "track_id": 0}, {"cx": 1596, "fw": 297, "track_id": 1}]),
        ]

        segment_tracks, anchors, sides = vp._choose_segment_tracks(
            segments=[(0.0, 2.0, "SPEAKER_03")],
            tracked_detections=tracked_detections,
            speaker_side={"SPEAKER_03": "right"},
            speaker_anchor_x={"SPEAKER_03": 1518},
            width=1920,
        )

        self.assertEqual(segment_tracks[0][3], 0)
        self.assertLess(anchors["SPEAKER_03"], 1200)
        self.assertEqual(sides["SPEAKER_03"], "left")

    def test_choose_track_segment_targets_picks_one_stable_target_per_turn(self):
        targets = vp._choose_track_segment_targets(
            segment_tracks=[
                (0.0, 3.0, "SPEAKER_00", 0, True),
                (3.0, 6.0, "SPEAKER_01", 1, True),
            ],
            tracked_detections=[
                (0.2, [{"cx": 320, "fw": 200, "track_id": 0}]),
                (1.0, [{"cx": 336, "fw": 205, "track_id": 0}]),
                (2.0, [{"cx": 344, "fw": 198, "track_id": 0}]),
                (3.2, [{"cx": 1480, "fw": 200, "track_id": 1}]),
                (4.1, [{"cx": 1492, "fw": 205, "track_id": 1}]),
                (5.0, [{"cx": 1500, "fw": 198, "track_id": 1}]),
            ],
            speaker_anchor_x={},
            width=1920,
            crop_w=607,
        )

        self.assertEqual(len(targets), 2)
        self.assertEqual(targets[0][0:2], (0.0, 3.0))
        self.assertEqual(targets[1][0:2], (3.0, 6.0))
        self.assertLess(targets[0][2], 60)
        self.assertGreater(targets[1][2], 1100)
        self.assertEqual(targets[0][3], "SPEAKER_00")
        self.assertEqual(targets[1][3], "SPEAKER_01")

    def test_choose_track_segment_targets_splits_same_turn_on_big_reframed_shift(self):
        targets = vp._choose_track_segment_targets(
            segment_tracks=[
                (0.0, 6.0, "SPEAKER_00", 0, True),
            ],
            tracked_detections=[
                (0.2, [{"cx": 330, "fw": 200, "track_id": 0}]),
                (1.1, [{"cx": 338, "fw": 205, "track_id": 0}]),
                (2.0, [{"cx": 344, "fw": 198, "track_id": 0}]),
                (3.2, [{"cx": 980, "fw": 200, "track_id": 0}]),
                (3.5, [{"cx": 992, "fw": 205, "track_id": 0}]),
                (4.2, [{"cx": 1004, "fw": 198, "track_id": 0}]),
            ],
            speaker_anchor_x={},
            width=1920,
            crop_w=607,
        )

        self.assertEqual(len(targets), 2)
        self.assertEqual(targets[0][0:2], (0.0, 3.2))
        self.assertEqual(targets[1][0:2], (3.2, 6.0))
        self.assertLess(targets[0][2], 80)
        self.assertGreater(targets[1][2], 650)
        self.assertEqual(targets[0][3], "SPEAKER_00")
        self.assertEqual(targets[1][3], "SPEAKER_00")

    def test_build_track_turn_keyframes_uses_short_reframe_then_holds(self):
        keyframes = vp._build_track_turn_keyframes(
            segment_targets=[
                (0.0, 3.0, 0),
                (3.0, 6.0, 1147),
            ],
            default_x=0,
        )

        self.assertEqual(keyframes[0], (0.0, 0))
        self.assertIn((3.0, 0), keyframes)
        self.assertIn((3.3, 1147), keyframes)
        self.assertEqual(len(keyframes), 3)

    def test_build_track_turn_keyframes_reframes_faster_on_same_speaker_layout_shift(self):
        keyframes = vp._build_track_turn_keyframes(
            segment_targets=[
                (0.0, 3.0, 140, "SPEAKER_00"),
                (3.0, 6.0, 1147, "SPEAKER_00"),
            ],
            default_x=140,
        )

        self.assertIn((3.0, 140), keyframes)
        self.assertIn((3.18, 1147), keyframes)

    def test_build_track_turn_keyframes_snaps_out_of_bad_far_left_hold(self):
        keyframes = vp._build_track_turn_keyframes(
            segment_targets=[
                (0.0, 3.0, 0, "SPEAKER_00"),
                (3.0, 6.0, 620, "SPEAKER_00"),
            ],
            default_x=0,
        )

        self.assertIn((2.99, 0), keyframes)
        self.assertIn((3.0, 620), keyframes)
        self.assertNotIn((3.14, 620), keyframes)

    def test_update_tripod_camera_holds_inside_safe_zone(self):
        current = vp._update_tripod_camera(
            current_center_x=960.0,
            target_center_x=1000.0,
            crop_w=607,
            video_width=1920,
            dt=0.1,
        )

        self.assertAlmostEqual(current, 960.0)

    def test_update_tripod_camera_moves_gradually_when_target_is_far(self):
        current = vp._update_tripod_camera(
            current_center_x=960.0,
            target_center_x=1320.0,
            crop_w=607,
            video_width=1920,
            dt=0.1,
        )

        self.assertGreater(current, 960.0)
        self.assertLess(current, 1320.0)

    def test_update_tripod_camera_recenters_on_moderate_offset(self):
        current = vp._update_tripod_camera(
            current_center_x=960.0,
            target_center_x=1060.0,
            crop_w=607,
            video_width=1920,
            dt=0.1,
        )

        self.assertGreater(current, 960.0)
        self.assertLess(current, 1060.0)

    def test_choose_camera_speaker_ignores_short_interjection(self):
        speaker, pending, count, switched = vp._choose_camera_speaker(
            transcript_speaker="SPEAKER_01",
            transcript_duration=1.4,
            active_speaker="SPEAKER_00",
            pending_speaker=None,
            pending_count=0,
        )

        self.assertEqual(speaker, "SPEAKER_00")
        self.assertIsNone(pending)
        self.assertEqual(count, 0)
        self.assertFalse(switched)

    def test_choose_camera_speaker_requires_confirmation_for_real_turn_change(self):
        speaker, pending, count, switched = vp._choose_camera_speaker(
            transcript_speaker="SPEAKER_01",
            transcript_duration=4.0,
            active_speaker="SPEAKER_00",
            pending_speaker=None,
            pending_count=0,
        )
        self.assertEqual(speaker, "SPEAKER_00")
        self.assertEqual(pending, "SPEAKER_01")
        self.assertEqual(count, 1)
        self.assertFalse(switched)

        speaker, pending, count, switched = vp._choose_camera_speaker(
            transcript_speaker="SPEAKER_01",
            transcript_duration=4.0,
            active_speaker=speaker,
            pending_speaker=pending,
            pending_count=count,
        )
        self.assertEqual(speaker, "SPEAKER_00")
        self.assertEqual(pending, "SPEAKER_01")
        self.assertEqual(count, 2)
        self.assertFalse(switched)

        speaker, pending, count, switched = vp._choose_camera_speaker(
            transcript_speaker="SPEAKER_01",
            transcript_duration=4.0,
            active_speaker=speaker,
            pending_speaker=pending,
            pending_count=count,
        )
        self.assertEqual(speaker, "SPEAKER_01")
        self.assertIsNone(pending)
        self.assertEqual(count, 0)
        self.assertTrue(switched)

    def test_pick_tracking_face_holds_on_same_speaker_when_single_face_contradicts_anchor(self):
        face, reliable = vp._pick_tracking_face(
            faces=[{"cx": 1180, "fw": 180}],
            speaker="SPEAKER_00",
            speaker_side={"SPEAKER_00": "left"},
            width=1920,
            last_target_x=280,
            last_speaker="SPEAKER_00",
            speaker_anchor_x={"SPEAKER_00": 260},
            has_any_split=True,
        )

        self.assertIsNone(face)
        self.assertFalse(reliable)

    def test_pick_tracking_face_accepts_single_face_on_real_speaker_change(self):
        face, reliable = vp._pick_tracking_face(
            faces=[{"cx": 1180, "fw": 180}],
            speaker="SPEAKER_01",
            speaker_side={"SPEAKER_01": "right"},
            width=1920,
            last_target_x=280,
            last_speaker="SPEAKER_00",
            speaker_anchor_x={"SPEAKER_01": 1160},
            has_any_split=True,
        )

        self.assertEqual(face["cx"], 1180)
        self.assertTrue(reliable)

    def test_pick_tracking_face_uses_side_hint_in_split_screen(self):
        face, reliable = vp._pick_tracking_face(
            faces=[{"cx": 320, "fw": 160}, {"cx": 1420, "fw": 180}],
            speaker="SPEAKER_01",
            speaker_side={"SPEAKER_01": "right"},
            width=1920,
            last_target_x=300,
            last_speaker="SPEAKER_00",
            speaker_anchor_x={"SPEAKER_01": 1400},
            has_any_split=True,
        )

        self.assertEqual(face["cx"], 1420)
        self.assertTrue(reliable)

    def test_choose_segment_targets_prefers_reliable_anchor_over_ambiguous_reaction_shot(self):
        segments = [
            (0.0, 3.0, "SPEAKER_00"),
            (3.0, 6.0, "SPEAKER_01"),
        ]
        timed_crops = [
            (0.5, 0, 20, "SPEAKER_00", True),
            (1.5, 0, 20, "SPEAKER_00", True),
            (3.2, 1147, 40, "SPEAKER_01", True),
            (3.6, 1147, 40, "SPEAKER_01", True),
            (4.7, 0, 20, "SPEAKER_01", False),
            (5.2, 0, 20, "SPEAKER_01", False),
        ]

        targets = vp._choose_segment_targets(
            segments=segments,
            timed_crops=timed_crops,
            speakers=["SPEAKER_00", "SPEAKER_01"],
            default_x=0,
            default_y=20,
            max_crop_x=1374,
            preferred_margin=54,
        )

        self.assertEqual(targets[0], (0.0, 3.0, 0, 20))
        self.assertEqual(targets[1], (3.0, 6.0, 1147, 40))

    def test_choose_segment_targets_penalizes_edge_hugging_opening_frame(self):
        segments = [
            (0.0, 3.0, "SPEAKER_00"),
        ]
        timed_crops = [
            (0.1, 0, 20, "SPEAKER_00", True),
            (0.5, 96, 24, "SPEAKER_00", True),
            (1.0, 110, 25, "SPEAKER_00", True),
        ]

        targets = vp._choose_segment_targets(
            segments=segments,
            timed_crops=timed_crops,
            speakers=["SPEAKER_00"],
            default_x=0,
            default_y=20,
            max_crop_x=1374,
            preferred_margin=54,
        )

        self.assertEqual(targets[0], (0.0, 3.0, 96, 24))

    def test_choose_segment_targets_allows_local_segment_to_override_bad_anchor(self):
        segments = [
            (0.0, 3.0, "SPEAKER_00"),
            (3.0, 6.0, "SPEAKER_01"),
        ]
        timed_crops = [
            (0.2, 0, 20, "SPEAKER_00", True),
            (1.0, 0, 20, "SPEAKER_00", True),
            (4.2, 620, 32, "SPEAKER_01", False),
            (4.8, 640, 34, "SPEAKER_01", False),
            (6.2, 1147, 40, "SPEAKER_01", True),
            (6.6, 1147, 40, "SPEAKER_01", True),
        ]

        targets = vp._choose_segment_targets(
            segments=segments,
            timed_crops=timed_crops,
            speakers=["SPEAKER_00", "SPEAKER_01"],
            default_x=0,
            default_y=20,
            max_crop_x=1374,
            preferred_margin=54,
        )

        self.assertEqual(targets[1], (3.0, 6.0, 620, 32))

    def test_build_transition_keyframes_eases_between_speaker_turns(self):
        x_keyframes, y_keyframes = vp._build_transition_keyframes(
            segment_targets=[
                (0.0, 3.0, 0, 20),
                (3.0, 6.0, 1147, 40),
            ],
            default_x=0,
            default_y=20,
            adj_w=546,
            adjusted_h=972,
        )

        self.assertEqual(x_keyframes[0], (0.0, 0))
        self.assertIn((3.0, 0), x_keyframes)
        self.assertIn((3.38, 1147), x_keyframes)
        self.assertIn((3.0, 20), y_keyframes)
        self.assertIn((3.38, 40), y_keyframes)

    def test_build_cam_expr_uses_quick_reframe_for_moderate_jump(self):
        expr = vp._build_cam_expr(
            keyframes=[(0.0, 100), (1.0, 240)],
            duration=1.0,
            is_split=False,
        )

        self.assertIn("100+((240-100)*((6*(((t-0.000)/0.180))*(((t-0.000)/0.180))*(((t-0.000)/0.180))*(((t-0.000)/0.180))*(((t-0.000)/0.180)))-(15*(((t-0.000)/0.180))*(((t-0.000)/0.180))*(((t-0.000)/0.180))*(((t-0.000)/0.180)))+(10*(((t-0.000)/0.180))*(((t-0.000)/0.180))*(((t-0.000)/0.180)))))", expr)
        self.assertIn("if(between(t\\,0.180\\,1.000)\\,240\\,", expr)

    def test_build_cam_expr_uses_blurred_cut_for_very_large_jump(self):
        expr = vp._build_cam_expr(
            keyframes=[(0.0, 100), (0.18, 420), (1.0, 420)],
            duration=1.0,
            is_split=False,
        )

        self.assertIn("if(between(t\\,0.000\\,0.180)\\,420\\,", expr)
        self.assertNotIn("100+((420-100)", expr)

    def test_build_motion_blur_filter_targets_only_short_reframes(self):
        blur = vp._build_motion_blur_filter(
            keyframes=[(0.0, 140), (0.18, 620), (1.0, 620), (1.6, 650)],
        )

        self.assertEqual(
            blur,
            ",gblur=sigma=5.4:steps=2:enable='between(t\\,0.000\\,0.230)'",
        )

    def test_build_motion_zoom_filter_targets_only_short_reframes(self):
        zoom = vp._build_motion_zoom_filter(
            keyframes=[(0.0, 140), (0.18, 620), (1.0, 620), (1.6, 650)],
            target_w=1080,
            target_h=1920,
        )

        self.assertIn("scale=w='iw*(1+0.0180*", zoom)
        self.assertIn("between(t\\,0.000\\,0.180)", zoom)
        self.assertIn("crop=1080:1920:(iw-1080)/2:(ih-1920)/2", zoom)
        self.assertNotIn("between(t\\,1.000\\,1.600)", zoom)

    def test_crop_to_vertical_prefers_sticky_tracker_before_face_map(self):
        transcript_words = [
            {"word": "hi", "start": 0.0, "end": 0.4, "speaker": "SPEAKER_00"},
            {"word": "yo", "start": 0.5, "end": 0.9, "speaker": "SPEAKER_01"},
        ]

        with mock.patch.object(vp, "get_dimensions", return_value=(1920, 1080)), \
             mock.patch.object(vp, "_build_speaker_aware_crop") as speaker_crop, \
             mock.patch.object(vp, "_use_face_map") as face_map_crop, \
             mock.patch.object(vp, "_track_and_crop", return_value="ok.mp4") as track_crop, \
             mock.patch.object(vp, "_run_ffmpeg_with_fallback", return_value="ok.mp4") as runner:
            result = vp.crop_to_vertical(
                input_path="in.mp4",
                output_path="out.mp4",
                strategy="speaker",
                transcript_words=transcript_words,
                clip_start=0.0,
                face_map={"clusters": [{"center_x": 100, "crop_x": 0}], "video_width": 1920},
            )

        self.assertEqual(result, "ok.mp4")
        speaker_crop.assert_not_called()
        face_map_crop.assert_not_called()
        track_crop.assert_called_once()
        runner.assert_not_called()

    def test_crop_to_vertical_prefers_sticky_tracker_for_single_speaker_clip(self):
        transcript_words = [
            {"word": "we", "start": 0.0, "end": 0.2, "speaker": "SPEAKER_03"},
            {"word": "deploy", "start": 0.2, "end": 0.5, "speaker": "SPEAKER_03"},
        ]

        with mock.patch.object(vp, "get_dimensions", return_value=(1920, 1080)), \
             mock.patch.object(vp, "_build_speaker_aware_crop") as speaker_crop, \
             mock.patch.object(vp, "_use_face_map") as face_map_crop, \
             mock.patch.object(vp, "_track_and_crop", return_value="ok.mp4") as track_crop, \
             mock.patch.object(vp, "_run_ffmpeg_with_fallback", return_value="ok.mp4") as runner:
            result = vp.crop_to_vertical(
                input_path="in.mp4",
                output_path="out.mp4",
                strategy="face",
                transcript_words=transcript_words,
                clip_start=1196.0,
                face_map={"clusters": [{"center_x": 100, "crop_x": 0}], "video_width": 1920},
            )

        self.assertEqual(result, "ok.mp4")
        speaker_crop.assert_not_called()
        face_map_crop.assert_not_called()
        track_crop.assert_called_once()
        runner.assert_not_called()

    def test_crop_to_vertical_uses_face_map_when_no_speaker_labels(self):
        with mock.patch.object(vp, "get_dimensions", return_value=(1920, 1080)), \
             mock.patch.object(vp, "_build_speaker_aware_crop") as speaker_crop, \
             mock.patch.object(vp, "_use_face_map", return_value="456") as face_map_crop, \
             mock.patch.object(vp, "_track_and_crop") as track_crop, \
             mock.patch.object(vp, "_run_ffmpeg_with_fallback", return_value="ok.mp4") as runner:
            result = vp.crop_to_vertical(
                input_path="in.mp4",
                output_path="out.mp4",
                strategy="face",
                transcript_words=[{"word": "hi", "start": 0.0, "end": 0.4, "speaker": None}],
                clip_start=0.0,
                face_map={"clusters": [{"center_x": 100, "crop_x": 0}], "video_width": 1920},
            )

        self.assertEqual(result, "ok.mp4")
        speaker_crop.assert_not_called()
        face_map_crop.assert_called_once()
        track_crop.assert_not_called()
        runner.assert_called_once()


if __name__ == "__main__":
    unittest.main()
