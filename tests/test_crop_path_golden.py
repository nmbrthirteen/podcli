"""Golden tests for the crop camera path and the crop-path dump.

The helper tests next door check each piece of the tracking math in isolation.
What they cannot catch is a change that leaves every helper correct but moves
the camera anyway — a different sampling rate, a reordered pipeline, a changed
default. Those are exactly the changes a "pure speed optimization" makes.

So these lock the composed output: fixed detections in, exact keyframes out.
No cv2, ffmpeg, or video files — the fixtures stand in for decoded frames.
"""

import json
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


if __name__ == "__main__":
    unittest.main()
