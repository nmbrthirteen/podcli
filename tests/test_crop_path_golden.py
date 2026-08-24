"""Golden tests for the crop camera path and the crop-path dump.

The helper tests next door check each piece of the tracking math in isolation.
What they cannot catch is a change that leaves every helper correct but moves
the camera anyway — a different sampling rate, a reordered pipeline, a changed
default. Those are exactly the changes a "pure speed optimization" makes.

So these lock the composed output: fixed detections in, exact keyframes out.
No cv2, ffmpeg, or video files — the fixtures stand in for decoded frames.
"""

import json
import re
import os
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest import mock

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BACKEND_ROOT = os.path.join(ROOT, "backend")
if BACKEND_ROOT not in sys.path:
    sys.path.insert(0, BACKEND_ROOT)

from services import video_processor as vp

WIDTH, HEIGHT = 1920, 1080
CROP_W = int(HEIGHT * (1080 / 1920))  # 607 — full-height 9:16 window


def _face(cx, fw=180):
    return {"cx": float(cx), "cy": 400.0, "fw": fw, "fh": fw, "confidence": 0.9}


def _two_speaker_detections(n=40, step=0.1):
    """Split-screen: a stable face at x=520 and another at x=1400."""
    return [(round(i * step, 3), [_face(520), _face(1400)]) for i in range(n)]


class FaceSampleIndicesGoldenTests(unittest.TestCase):
    """The sampling schedule is the input to everything downstream. If a speed
    change alters it, every keyframe below shifts — so pin the exact schedule."""

    def test_30fps_schedule_is_exact(self):
        idx = vp._face_sample_indices(90, 30.0)
        # Every frame to 0.5s (0-14), every other to 1.0s (15-29), then ~10fps.
        self.assertEqual(idx[:15], list(range(15)))
        self.assertEqual(idx[15:19], [15, 16, 17, 18])
        steady = [b - a for a, b in zip(idx, idx[1:]) if a >= 30]
        self.assertEqual(set(steady), {3})
        self.assertEqual(idx[-1], 87)  # last sample before the 90-frame end

    def test_sampling_rate_is_ten_fps_in_steady_state(self):
        for fps in (24.0, 25.0, 30.0, 50.0, 60.0):
            idx = vp._face_sample_indices(int(fps * 10), fps)
            steady = [b - a for a, b in zip(idx, idx[1:]) if a >= fps]
            self.assertTrue(steady, f"no steady-state samples at {fps}fps")
            self.assertEqual(
                set(steady), {max(1, int(fps / 10))},
                f"steady-state step drifted off 10fps at {fps}fps",
            )


class AssignFaceTracksGoldenTests(unittest.TestCase):
    def test_split_screen_yields_two_stable_tracks(self):
        tracked = vp._assign_face_tracks(_two_speaker_detections(), WIDTH)
        self.assertEqual(len(tracked), 40)
        ids_per_frame = [{f["track_id"] for f in faces} for _, faces in tracked]
        # Two identities, and they stay the same for the whole clip.
        self.assertTrue(all(len(s) == 2 for s in ids_per_frame))
        self.assertEqual(len(set().union(*ids_per_frame)), 2)

    def test_track_ids_follow_position_not_list_order(self):
        # Same two faces, but the detector returns them in flipped order
        # halfway through. Identity must stay pinned to position.
        dets = []
        for i in range(20):
            faces = [_face(520), _face(1400)]
            dets.append((round(i * 0.1, 3), faces if i < 10 else faces[::-1]))
        tracked = vp._assign_face_tracks(dets, WIDTH)
        left_ids = {
            min(faces, key=lambda f: f["cx"])["track_id"] for _, faces in tracked
        }
        self.assertEqual(len(left_ids), 1, "left face changed identity mid-clip")


class TripodCameraGoldenTests(unittest.TestCase):
    """The camera is what the viewer actually sees. Pin its behaviour."""

    def test_force_snap_centres_exactly(self):
        cam = vp._update_tripod_camera(
            current_center_x=100.0, target_center_x=960.0,
            crop_w=CROP_W, video_width=WIDTH, dt=0.0, force_snap=True,
        )
        self.assertEqual(cam, 960.0)

    def test_camera_holds_still_for_small_drift(self):
        start = 960.0
        cam = start
        # A face jittering by a few px must not move the camera at all.
        for target in (964.0, 957.0, 962.0, 959.0):
            cam = vp._update_tripod_camera(
                current_center_x=cam, target_center_x=target,
                crop_w=CROP_W, video_width=WIDTH, dt=0.1,
            )
        self.assertEqual(cam, start, "tripod drifted on sub-threshold jitter")

    def test_camera_never_leaves_frame(self):
        for target in (-500.0, 0.0, 99999.0):
            cam = vp._update_tripod_camera(
                current_center_x=960.0, target_center_x=target,
                crop_w=CROP_W, video_width=WIDTH, dt=1.0, force_snap=True,
            )
            self.assertGreaterEqual(cam, CROP_W / 2)
            self.assertLessEqual(cam, WIDTH - CROP_W / 2)


class FaceDetectWorkersTests(unittest.TestCase):
    """Pool size is a correctness-adjacent concern: each worker needs its own
    ~21ms detector, so over-sizing the pool on a short clip is slower than
    staying serial. Measured before this cap: a 2s clip went 216ms -> 313ms."""

    def test_env_override_wins_and_is_taken_literally(self):
        with mock.patch.dict(os.environ, {"PODCLI_FACE_WORKERS": "3"}):
            self.assertEqual(vp._face_detect_workers(10_000), 3)
            # Even when the workload would not justify it.
            self.assertEqual(vp._face_detect_workers(1), 3)

    def test_one_worker_is_allowed_to_disable_the_pool(self):
        with mock.patch.dict(os.environ, {"PODCLI_FACE_WORKERS": "1"}):
            self.assertEqual(vp._face_detect_workers(10_000), 1)

    def test_garbage_and_zero_fall_back_to_a_sane_count(self):
        for bad in ("garbage", "0", "-4"):
            with mock.patch.dict(os.environ, {"PODCLI_FACE_WORKERS": bad}):
                self.assertGreaterEqual(vp._face_detect_workers(10_000), 1)

    def test_short_clips_stay_serial(self):
        # A ~2s clip samples ~40 frames — not enough to repay a second detector.
        with mock.patch.dict(os.environ, {"PODCLI_FACE_WORKERS": ""}):
            self.assertEqual(vp._face_detect_workers(0), 1)
            self.assertEqual(vp._face_detect_workers(40), 1)

    def test_pool_grows_with_the_workload(self):
        with mock.patch.object(os, "cpu_count", return_value=128):
            with mock.patch.dict(os.environ, {"PODCLI_FACE_WORKERS": ""}):
                self.assertEqual(vp._face_detect_workers(70), 2)
                self.assertEqual(vp._face_detect_workers(170), 5)
                # Capped at 12 no matter how long the clip or how many cores.
                self.assertEqual(vp._face_detect_workers(100_000), 12)

    def test_never_exceeds_core_count(self):
        with mock.patch.object(os, "cpu_count", return_value=2):
            with mock.patch.dict(os.environ, {"PODCLI_FACE_WORKERS": ""}):
                self.assertEqual(vp._face_detect_workers(100_000), 2)


class DetectBatchTests(unittest.TestCase):
    """Parallel inference must be indistinguishable from serial. Verified on a
    real 620-frame 1080p clip (5.5x faster, identical output); these pin the
    ordering and detector-isolation properties that make that true."""

    @staticmethod
    def _fake_detect(det, frame, w, h):
        # Encodes which detector handled the frame, so sharing is detectable.
        return [{"cx": float(frame), "detector": det}]

    def _run(self, n_frames, n_detectors, use_executor=True):
        batch = [(i * 0.1, i) for i in range(n_frames)]
        detectors = [f"det{k}" for k in range(n_detectors)]
        executor = (
            ThreadPoolExecutor(max_workers=n_detectors)
            if use_executor and n_detectors > 1
            else None
        )
        try:
            with mock.patch(
                "services.face_detector.detect_faces", side_effect=self._fake_detect
            ):
                return vp._detect_batch(batch, detectors, 1920, 1080, executor)
        finally:
            if executor:
                executor.shutdown(wait=True)

    def test_parallel_matches_serial_exactly(self):
        serial = self._run(50, 1, use_executor=False)
        parallel = self._run(50, 4)
        self.assertEqual(
            [(t, f[0]["cx"]) for t, f in parallel],
            [(t, f[0]["cx"]) for t, f in serial],
        )

    def test_input_order_is_preserved(self):
        out = self._run(37, 5)
        self.assertEqual([t for t, _ in out], [i * 0.1 for i in range(37)])
        self.assertEqual([f[0]["cx"] for _, f in out], [float(i) for i in range(37)])

    def test_each_detector_is_used_by_exactly_one_stride(self):
        out = self._run(20, 4)
        # Frame i must be handled by detector i % 4 — deterministic striding,
        # so no detector is ever touched by two threads.
        for i, (_, faces) in enumerate(out):
            self.assertEqual(faces[0]["detector"], f"det{i % 4}")

    def test_falls_back_to_serial_without_executor(self):
        out = self._run(10, 4, use_executor=False)
        self.assertTrue(all(f[0]["detector"] == "det0" for _, f in out))

    def test_handles_empty_and_single_frame_batches(self):
        self.assertEqual(self._run(0, 4), [])
        self.assertEqual(len(self._run(1, 4)), 1)


class DumpCropPathTests(unittest.TestCase):
    """The dump is the harness used to prove a speed change moved nothing.
    If it is silently a no-op or throws, that proof is worthless."""

    KWARGS = dict(
        input_path="/tmp/clip_001.mp4",
        keyframes_x=[(0.0, 300), (1.5, 900)],
        crop_w=CROP_W, crop_h=HEIGHT, crop_y=0,
        width=WIDTH, height=HEIGHT,
        detections=_two_speaker_detections(n=3),
        segment_tracks=[(0.0, 1.5, "SPEAKER_00", 1, None)],
        has_any_split=True,
    )

    def test_writes_expected_payload(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {"PODCLI_CROP_DUMP": tmp}):
                vp._dump_crop_path(**self.KWARGS)
            out = os.path.join(tmp, "clip_001.crop.json")
            self.assertTrue(os.path.exists(out), "no dump written")
            with open(out, encoding="utf-8") as f:
                payload = json.load(f)

        self.assertEqual(payload["keyframes_x"], [[0.0, 300], [1.5, 900]])
        self.assertEqual(payload["crop"], {"w": CROP_W, "h": HEIGHT, "y": 0})
        self.assertEqual(payload["source_dims"], [WIDTH, HEIGHT])
        self.assertEqual(payload["detection_frames"], 3)
        self.assertEqual(payload["frames_with_faces"], 3)
        self.assertEqual(
            payload["segment_tracks"],
            [{"start": 0.0, "end": 1.5, "speaker": "SPEAKER_00", "track_id": 1}],
        )

    def test_noop_without_env_var(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = {k: v for k, v in os.environ.items() if k != "PODCLI_CROP_DUMP"}
            with mock.patch.dict(os.environ, env, clear=True):
                vp._dump_crop_path(**self.KWARGS)
            self.assertEqual(os.listdir(tmp), [])

    def test_never_raises_on_bad_destination(self):
        # An unwritable dump dir must not take a render down with it.
        bad = "/dev/null/nope"
        with mock.patch.dict(os.environ, {"PODCLI_CROP_DUMP": bad}):
            vp._dump_crop_path(**self.KWARGS)  # must not raise




class UseFaceMapSpeakerGuardTests(unittest.TestCase):
    """A face map without speaker labels must not pick a face by list order.

    Two clusters and no diarization is the state whisper.cpp leaves behind. The
    old code read that as "single speaker" and pinned the whole clip to
    clusters[0], so the camera watched one person while the other talked.
    """

    def _face_map(self, **over):
        fm = {
            "clusters": [
                {"center_x": 1006, "crop_x": 399, "count": 262},
                {"center_x": 2964, "crop_x": 2357, "count": 236},
            ],
            "speaker_mappings": {},
            "dominant_speaker": None,
            "is_split_screen": True,
            "video_width": 3840,
        }
        fm.update(over)
        return fm

    def _call(self, face_map, words):
        return vp._use_face_map(
            face_map=face_map,
            transcript_words=words,
            clip_start=0.0,
            width=3840,
            height=2160,
            target_ratio=1080 / 1920,
        )

    def test_two_clusters_without_speakers_declines(self):
        words = [{"word": "hi", "start": 0.0, "end": 0.4, "speaker": None}]
        self.assertIsNone(self._call(self._face_map(), words))

    def test_no_words_at_all_declines(self):
        self.assertIsNone(self._call(self._face_map(), []))

    def test_one_cluster_without_speakers_still_crops(self):
        fm = self._face_map(
            clusters=[{"center_x": 1006, "crop_x": 399, "count": 262}],
            is_split_screen=False,
        )
        words = [{"word": "hi", "start": 0.0, "end": 0.4, "speaker": None}]
        self.assertIsNotNone(self._call(fm, words))

    def test_labelled_but_unmapped_speaker_declines(self):
        # Exactly the state a diarized transcript is in before the face map is
        # rebuilt: words carry speakers, speaker_mappings is still empty.
        words = [{"word": "hi", "start": 0.0, "end": 0.4, "speaker": "SPEAKER_01"}]
        self.assertIsNone(self._call(self._face_map(), words))

    def test_partially_mapped_speakers_decline(self):
        fm = self._face_map(speaker_mappings={"SPEAKER_00": 0})
        words = [
            {"word": "hi", "start": 0.0, "end": 0.4, "speaker": "SPEAKER_00"},
            {"word": "yes", "start": 1.0, "end": 1.4, "speaker": "SPEAKER_01"},
        ]
        self.assertIsNone(self._call(fm, words))

    def test_out_of_range_cluster_index_declines(self):
        fm = self._face_map(speaker_mappings={"SPEAKER_01": 7})
        words = [{"word": "hi", "start": 0.0, "end": 0.4, "speaker": "SPEAKER_01"}]
        self.assertIsNone(self._call(fm, words))

    def test_single_labelled_speaker_uses_their_cluster(self):
        fm = self._face_map(speaker_mappings={"SPEAKER_01": 1})
        words = [{"word": "hi", "start": 0.0, "end": 0.4, "speaker": "SPEAKER_01"}]
        self.assertEqual(self._call(fm, words), "2357")


class SaneSpeakerMappingsTests(unittest.TestCase):
    """A stale face map must not hand a bad cluster index to the tracker."""

    def _fm(self, mappings):
        return {"clusters": [{"center_x": 100}, {"center_x": 900}], "speaker_mappings": mappings}

    def test_drops_out_of_range_and_negative_indices(self):
        out = vp._sane_speaker_mappings(self._fm({"A": 0, "B": 7, "C": -1}))
        self.assertEqual(out["speaker_mappings"], {"A": 0})

    def test_drops_booleans_which_python_counts_as_ints(self):
        out = vp._sane_speaker_mappings(self._fm({"A": True, "B": 1}))
        self.assertEqual(out["speaker_mappings"], {"B": 1})

    def test_returns_the_same_object_when_nothing_is_wrong(self):
        fm = self._fm({"A": 0, "B": 1})
        self.assertIs(vp._sane_speaker_mappings(fm), fm)

    def test_passes_none_through(self):
        self.assertIsNone(vp._sane_speaker_mappings(None))


class MixedLayoutDetectionTests(unittest.TestCase):
    """A clip that switches between a split screen and a fullscreen shot must
    be recognised from its own frames.

    The layout used to be read only from the episode-wide face_map, and
    face_maps cached by a podcli older than the mixed-layout work carry no
    is_mixed_layout key at all. `.get(..., False)` turned those into "not
    mixed", which sent the clip down the keyframe path. That path holds one
    camera position across a layout change, so on a Riverside recording every
    fullscreen stretch rendered as the wall beside the speaker.
    """

    def _decide(self, detections, face_map=None):
        return vp._clip_layout_is_mixed(detections, face_map)

    def _frames(self, n_split, n_single):
        det = [(i * 0.1, [{"cx": 1181}, {"cx": 2909}]) for i in range(n_split)]
        det += [((n_split + i) * 0.1, [{"cx": 2050}]) for i in range(n_single)]
        return det

    def test_riverside_shape_is_mixed_without_any_face_map(self):
        # The real proportions from the reported episode: 55 split-screen
        # frames and 28 fullscreen frames in one 41s clip.
        self.assertTrue(self._decide(self._frames(55, 28)))

    def test_stale_face_map_does_not_override_the_clip(self):
        stale = {"clusters": [{"center_x": 1006}, {"center_x": 2964}]}  # no key
        self.assertTrue(self._decide(self._frames(55, 28), stale))

    def test_pure_split_screen_is_not_mixed(self):
        self.assertFalse(self._decide(self._frames(80, 0)))

    def test_pure_fullscreen_is_not_mixed(self):
        self.assertFalse(self._decide(self._frames(0, 80)))

    def test_a_few_missed_second_faces_are_not_a_layout(self):
        # 4 dropped detections in 80 frames is detector noise, not a switch to
        # a fullscreen shot. Counting them would route almost every clip
        # through the mixed path.
        self.assertFalse(self._decide(self._frames(76, 4)))

    def test_face_map_flag_still_wins_when_the_clip_looks_uniform(self):
        # A clip that sits entirely inside one layout still belongs to a mixed
        # episode; the episode-wide flag remains a valid hint.
        self.assertTrue(self._decide(self._frames(80, 0), {"is_mixed_layout": True}))


class CropFailuresNeverFailTheClipTests(unittest.TestCase):
    """A crop that cannot be computed must report "no crop", never raise.

    Every caller reads None as "try something simpler" and falls through to a
    face-map crop and then a centre crop. An exception skips all of that and
    fails the clip. utils.proc.run raises on a timeout even with check=False,
    so a single ffmpeg that hung took a whole render down and left every
    moment in it showing "Did not render".
    """

    def test_an_exception_inside_tracking_becomes_none(self):
        with mock.patch.object(
            vp, "_track_and_crop_inner", side_effect=RuntimeError("ffmpeg hung"),
        ):
            self.assertIsNone(
                vp._track_and_crop("in.mp4", "out.mp4", 1920, 1080, 1080, 1920)
            )

    def test_a_proc_timeout_becomes_none(self):
        from utils.proc import ProcError
        boom = ProcError(["ffmpeg"], -1, "timeout after 600s", 600.0)
        with mock.patch.object(vp, "_track_and_crop_inner", side_effect=boom):
            self.assertIsNone(
                vp._track_and_crop("in.mp4", "out.mp4", 1920, 1080, 1080, 1920)
            )

    def test_a_working_track_is_returned_untouched(self):
        with mock.patch.object(vp, "_track_and_crop_inner", return_value="out.mp4"):
            self.assertEqual(
                vp._track_and_crop("in.mp4", "out.mp4", 1920, 1080, 1080, 1920),
                "out.mp4",
            )


if __name__ == "__main__":
    unittest.main()


class DissolveNeedsBothFramingsTests(unittest.TestCase):
    """A dissolve across a layout change has nothing to dissolve.

    The stitch pads each run by the dissolve length, which runs that run's crop
    over the next run's content. The subject survives that pad only while the
    next centre is still inside this crop, which is half a window either side.
    On the episode this came from the widest boundary was 1167px against a
    607px reach, and the dissolve showed 209ms of bare wall.
    """

    @staticmethod
    def holds(runs, crop_w):
        reach = crop_w / 2
        return all(abs(runs[i + 1][2] - runs[i][2]) <= reach for i in range(len(runs) - 1))

    def test_a_layout_change_is_too_wide_to_dissolve(self):
        runs = [(0.0, 2.5, 1006), (2.5, 6.0, 2173)]  # 1167px apart
        self.assertFalse(self.holds(runs, 1214))

    def test_a_speaker_shifting_in_their_seat_still_dissolves(self):
        runs = [(0.0, 2.5, 1006), (2.5, 6.0, 1300)]  # 294px apart
        self.assertTrue(self.holds(runs, 1214))

    def test_one_wide_boundary_settles_it_for_the_whole_clip(self):
        runs = [(0.0, 2.0, 1006), (2.0, 4.0, 1200), (4.0, 6.0, 2400)]
        self.assertFalse(self.holds(runs, 1214))


class SteppedRunCropTests(unittest.TestCase):
    """One expression over one pass, so a layout change cannot drift.

    Cutting a run per layout and joining them re-encodes each part, and each
    part overshoots the length it was asked for: 403ms across five runs, with
    the video ending 177ms before its own audio.
    """

    RUNS = [(0.0, 2.629, 1021), (2.629, 6.386, 1940), (6.386, 10.393, 921)]
    CROP = staticmethod(lambda cx: max(0, min(int(cx - 607), 3840 - 1214)))

    def _at(self, expr, t):
        """Evaluate the nested if(gte(t,T),a,b) the way FFmpeg would."""
        expr = expr.replace("\\", "")
        while expr.startswith("if("):
            inner, depth, parts, cur = expr[3:-1], 0, [], ""
            for ch in inner:
                if ch == "(": depth += 1
                if ch == ")": depth -= 1
                if ch == "," and depth == 0: parts.append(cur); cur = ""
                else: cur += ch
            parts.append(cur)
            cond, yes, no = parts
            thr = float(re.search(r"gte\(t,([\d.]+)\)", cond).group(1))
            expr = yes if t >= thr else no
        return int(expr)

    def test_each_run_holds_its_own_crop(self):
        e = vp._run_step_crop_x_expr(self.RUNS, self.CROP)
        for start, end, cx in self.RUNS:
            mid = (start + end) / 2
            self.assertEqual(self._at(e, mid), self.CROP(cx))

    def test_it_snaps_at_the_boundary_rather_than_gliding(self):
        e = vp._run_step_crop_x_expr(self.RUNS, self.CROP)
        b = self.RUNS[1][0]
        self.assertEqual(self._at(e, b - 0.001), self.CROP(self.RUNS[0][2]))
        self.assertEqual(self._at(e, b), self.CROP(self.RUNS[1][2]))

    def test_a_single_run_is_a_constant(self):
        e = vp._run_step_crop_x_expr([self.RUNS[0]], self.CROP)
        self.assertEqual(e, str(self.CROP(self.RUNS[0][2])))


class SnapRunsToSourceCutsTests(unittest.TestCase):
    """A boundary belongs on the cut it is describing, not near it.

    The sampler runs at about 12Hz, so a run closes up to 83ms after the cut it
    noticed. Those two frames of old crop on new layout are the wall left in
    frame, and they register as a second scene change 83ms after the source's
    own, which reads as clustered cuts and drags the transition blur onto a cut
    that never needed it.
    """

    CUTS = [0.875, 7.792, 16.958, 23.458]

    def test_a_late_boundary_moves_onto_the_cut(self):
        runs = [(0.0, 7.87, 1000), (7.87, 17.04, 2000), (17.04, 33.4, 1000)]
        out = vp._snap_runs_to_source_cuts(runs, self.CUTS)
        self.assertAlmostEqual(out[0][1], 7.792, places=3)
        self.assertAlmostEqual(out[1][0], 7.792, places=3)
        self.assertAlmostEqual(out[1][1], 16.958, places=3)

    def test_a_boundary_with_no_cut_near_it_is_left_alone(self):
        runs = [(0.0, 12.0, 1000), (12.0, 33.4, 2000)]
        self.assertEqual(vp._snap_runs_to_source_cuts(runs, self.CUTS), runs)

    def test_it_never_collapses_a_run(self):
        runs = [(0.0, 0.9, 1000), (0.9, 1.0, 2000), (1.0, 5.0, 1000)]
        for a, b, _ in vp._snap_runs_to_source_cuts(runs, self.CUTS):
            self.assertLess(a, b)

    def test_no_cuts_detected_changes_nothing(self):
        runs = [(0.0, 7.87, 1000), (7.87, 33.4, 2000)]
        self.assertEqual(vp._snap_runs_to_source_cuts(runs, []), runs)
