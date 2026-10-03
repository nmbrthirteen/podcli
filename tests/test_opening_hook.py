import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import numpy as np

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BACKEND_ROOT = os.path.join(ROOT, "backend")
if BACKEND_ROOT not in sys.path:
    sys.path.insert(0, BACKEND_ROOT)

from services import clip_generator as cg
from services.opening_hook import (
    keyframes_to_playback,
    order_with_hook,
    snap_hook_to_words,
    validate_hook,
)


class ValidateHookTests(unittest.TestCase):
    def test_none_means_no_hook(self):
        self.assertIsNone(validate_hook(None, 10, 40))

    def test_valid_hook_is_normalized(self):
        hook = validate_hook({"start": 12, "end": 15, "mode": "move"}, 10, 40)
        self.assertEqual(hook, {"start": 12.0, "end": 15.0, "mode": "move"})

    def test_hook_on_the_clip_edges_is_inside(self):
        self.assertIsNotNone(validate_hook({"start": 10, "end": 12, "mode": "repeat"}, 10, 40))
        self.assertIsNotNone(validate_hook({"start": 38, "end": 40, "mode": "repeat"}, 10, 40))

    def test_end_must_follow_start(self):
        with self.assertRaisesRegex(ValueError, "greater than"):
            validate_hook({"start": 15, "end": 15, "mode": "repeat"}, 10, 40)

    def test_shorter_than_one_second_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "between 1 and 15"):
            validate_hook({"start": 12, "end": 12.5, "mode": "repeat"}, 10, 40)

    def test_longer_than_fifteen_seconds_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "between 1 and 15"):
            validate_hook({"start": 12, "end": 28, "mode": "repeat"}, 10, 40)

    def test_outside_the_clip_range_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "not inside the clip body"):
            validate_hook({"start": 8, "end": 11, "mode": "repeat"}, 10, 40)
        with self.assertRaisesRegex(ValueError, "not inside the clip body"):
            validate_hook({"start": 39, "end": 41, "mode": "repeat"}, 10, 40)

    def test_hook_in_a_gap_between_segments_is_rejected(self):
        segments = [{"start": 10, "end": 15}, {"start": 20, "end": 30}]
        with self.assertRaisesRegex(ValueError, "not inside the clip body"):
            validate_hook({"start": 14, "end": 17, "mode": "repeat"}, 10, 30, segments)

    def test_hook_across_touching_segments_is_inside(self):
        segments = [{"start": 10, "end": 15}, {"start": 15, "end": 30}]
        self.assertIsNotNone(
            validate_hook({"start": 14, "end": 17, "mode": "repeat"}, 10, 30, segments)
        )

    def test_unknown_mode_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "repeat"):
            validate_hook({"start": 12, "end": 14, "mode": "loop"}, 10, 40)
        with self.assertRaisesRegex(ValueError, "repeat"):
            validate_hook({"start": 12, "end": 14}, 10, 40)

    def test_non_numeric_bounds_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "numbers"):
            validate_hook({"start": "12", "end": 14, "mode": "repeat"}, 10, 40)
        with self.assertRaisesRegex(ValueError, "numbers"):
            validate_hook({"start": True, "end": 14, "mode": "repeat"}, 10, 40)

    def test_non_object_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "object"):
            validate_hook([12, 14], 10, 40)

    def test_generate_clip_rejects_a_bad_hook_before_rendering(self):
        with tempfile.TemporaryDirectory() as td:
            video = os.path.join(td, "v.mp4")
            open(video, "wb").close()
            with mock.patch.object(cg, "cut_segment") as cut, \
                 mock.patch.object(cg, "cut_multi_segment") as cut_multi:
                with self.assertRaisesRegex(ValueError, "not inside the clip body"):
                    cg.generate_clip(
                        video_path=video, start_second=10, end_second=20,
                        hook={"start": 25, "end": 27, "mode": "repeat"},
                    )
            cut.assert_not_called()
            cut_multi.assert_not_called()


class OrderWithHookTests(unittest.TestCase):
    body = [{"start": 10, "end": 20}, {"start": 22, "end": 30}]

    def test_repeat_plays_the_hook_then_the_whole_body(self):
        ranges = order_with_hook(self.body, {"start": 24, "end": 27, "mode": "repeat"})
        self.assertEqual(ranges, [
            {"start": 24, "end": 27},
            {"start": 10, "end": 20},
            {"start": 22, "end": 30},
        ])

    def test_move_removes_the_hook_from_the_body(self):
        ranges = order_with_hook(self.body, {"start": 24, "end": 27, "mode": "move"})
        self.assertEqual(ranges, [
            {"start": 24, "end": 27},
            {"start": 10, "end": 20},
            {"start": 22, "end": 24},
            {"start": 27, "end": 30},
        ])

    def test_move_drops_a_sliver_left_at_a_segment_edge(self):
        ranges = order_with_hook(self.body, {"start": 22.05, "end": 25, "mode": "move"})
        self.assertEqual(ranges, [
            {"start": 22.05, "end": 25},
            {"start": 10, "end": 20},
            {"start": 25, "end": 30},
        ])

    def test_move_cannot_take_the_whole_body(self):
        with self.assertRaisesRegex(ValueError, "whole clip"):
            order_with_hook([{"start": 10, "end": 14}], {"start": 10, "end": 14, "mode": "move"})


class SnapHookTests(unittest.TestCase):
    words = [
        {"word": "one", "start": 1.0, "end": 1.6},
        {"word": "two", "start": 1.8, "end": 2.4},
        {"word": "three", "start": 2.6, "end": 3.3},
    ]

    def test_edges_inside_words_widen_to_the_whole_word(self):
        hook = snap_hook_to_words({"start": 1.2, "end": 3.0, "mode": "repeat"}, self.words)
        self.assertEqual((hook["start"], hook["end"]), (1.0, 3.3))
        self.assertEqual(hook["mode"], "repeat")

    def test_edges_in_gaps_are_left_alone(self):
        hook = snap_hook_to_words({"start": 1.7, "end": 2.5, "mode": "move"}, self.words)
        self.assertEqual((hook["start"], hook["end"]), (1.7, 2.5))


class KeyframesToPlaybackTests(unittest.TestCase):
    def test_identity_for_one_range_at_the_origin(self):
        kf = [{"t": 0.0, "x_pct": 30}, {"t": 4.0, "x_pct": 70}]
        out = keyframes_to_playback(kf, 10.0, [{"start": 10.0, "end": 20.0}])
        self.assertEqual(out, kf)

    def test_each_range_opens_on_the_keyframe_in_force(self):
        # Source clock: 30% from 10s, 70% from 14s. The hook 15-17 plays first
        # at 70%; the body then replays 10-20 from 30% and switches at 4s.
        kf = [{"t": 0.0, "x_pct": 30}, {"t": 4.0, "x_pct": 70}]
        ranges = [{"start": 15.0, "end": 17.0}, {"start": 10.0, "end": 20.0}]
        out = keyframes_to_playback(kf, 10.0, ranges, part_durations=[2.0, 10.0])
        self.assertEqual(out, [
            {"t": 0.0, "x_pct": 70},
            {"t": 2.0, "x_pct": 30},
            {"t": 6.0, "x_pct": 70},
        ])


@unittest.skipUnless(
    shutil.which("ffmpeg") and shutil.which("ffprobe"), "ffmpeg/ffprobe not installed"
)
class OpeningHookRenderTests(unittest.TestCase):
    """Real render of a source whose every second has its own colour and its
    own tone, so the order of the output can be read back frame by frame."""

    FPS = 25
    COLORS = ["red", "green", "blue", "yellow", "cyan", "magenta", "white", "gray", "orange", "purple"]
    RGB = {
        "red": (255, 0, 0), "green": (0, 128, 0), "blue": (0, 0, 255),
        "yellow": (255, 255, 0), "cyan": (0, 255, 255), "magenta": (255, 0, 255),
        "white": (255, 255, 255), "gray": (128, 128, 128), "orange": (255, 165, 0),
        "purple": (128, 0, 128),
    }

    @staticmethod
    def freq(second):
        return 300 + 100 * second

    @classmethod
    def setUpClass(cls):
        cls.tmpdir = tempfile.mkdtemp(prefix="podcli-hook-test-")
        cls.src = os.path.join(cls.tmpdir, "src.mp4")
        n = len(cls.COLORS)
        cmd = ["ffmpeg", "-y", "-loglevel", "error"]
        for color in cls.COLORS:
            cmd += ["-f", "lavfi", "-i", f"color=c={color}:s=320x240:r={cls.FPS}:d=1"]
        for i in range(n):
            cmd += ["-f", "lavfi", "-i", f"sine=frequency={cls.freq(i)}:sample_rate=44100:duration=1"]
        pairs = "".join(f"[{i}:v][{n + i}:a]" for i in range(n))
        cmd += [
            "-filter_complex", f"{pairs}concat=n={n}:v=1:a=1[v][a]",
            "-map", "[v]", "-map", "[a]",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
            cls.src,
        ]
        subprocess.run(cmd, check=True, capture_output=True)
        # One word per second, well inside it.
        cls.words = [
            {"word": f"w{i}", "start": i + 0.2, "end": i + 0.7, "speaker": None}
            for i in range(n)
        ]

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmpdir, ignore_errors=True)

    def _render(self, mode):
        captured = {}
        real_write = cg.write_sidecars

        def spy(words, base, *a, **kw):
            captured["words"] = words
            return real_write(words, base, *a, **kw)

        with mock.patch.object(cg, "write_sidecars", side_effect=spy):
            result = cg.generate_clip(
                video_path=self.src,
                start_second=2.0,
                end_second=8.0,
                caption_style="subtle",
                crop_strategy="center",
                transcript_words=self.words,
                title=f"hook_{mode}",
                output_dir=os.path.join(self.tmpdir, mode),
                captions=False,
                # Sidecars otherwise follow `captions`; this test inspects
                # the sidecar words directly, so request them explicitly.
                write_subtitles=True,
                clean_fillers=False,
                trim_opening=False,
                hook={"start": 5.0, "end": 7.0, "mode": mode},
            )
        return result, captured["words"]

    def _frame_rgb(self, path, t):
        raw = subprocess.run(
            ["ffmpeg", "-loglevel", "error", "-ss", f"{t:.3f}", "-i", path,
             "-frames:v", "1", "-vf", "scale=32:32", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
            check=True, capture_output=True,
        ).stdout
        px = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 3)
        return tuple(int(c) for c in px.mean(axis=0).round())

    def _dominant_freq(self, path, t, window=0.4):
        raw = subprocess.run(
            ["ffmpeg", "-loglevel", "error", "-ss", f"{t:.3f}", "-i", path, "-t", f"{window}",
             "-vn", "-ac", "1", "-ar", "16000", "-f", "s16le", "-"],
            check=True, capture_output=True,
        ).stdout
        samples = np.frombuffer(raw, dtype=np.int16).astype(np.float64)
        spectrum = np.abs(np.fft.rfft(samples * np.hanning(len(samples))))
        freqs = np.fft.rfftfreq(len(samples), 1 / 16000)
        return float(freqs[int(spectrum.argmax())])

    def _video_duration(self, path):
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_frames",
             "-show_entries", "stream=nb_read_frames", "-of", "csv=p=0", path],
            check=True, capture_output=True, text=True,
        ).stdout.strip()
        return int(out) / self.FPS

    def _assert_plays(self, path, playback_t, source_second):
        got = self._frame_rgb(path, playback_t)
        want = self.RGB[self.COLORS[source_second]]
        for g, w in zip(got, want):
            self.assertLessEqual(
                abs(g - w), 40,
                f"at {playback_t}s the frame is {got}, expected source second "
                f"{source_second} ({self.COLORS[source_second]} {want})",
            )
        self.assertAlmostEqual(
            self._dominant_freq(path, playback_t - 0.2), self.freq(source_second), delta=15,
            msg=f"at {playback_t}s the tone is not source second {source_second}",
        )

    def _assert_words(self, words, expected):
        got = [(w["word"], w["start"]) for w in words]
        self.assertEqual([g[0] for g in got], [e[0] for e in expected])
        for (word, start), (_, want) in zip(got, expected):
            self.assertAlmostEqual(start, want, delta=0.05, msg=f"{word} at {start}, expected {want}")

    def test_repeat_plays_the_hook_first_then_the_whole_body(self):
        result, words = self._render("repeat")
        path = result["output_path"]
        # Hook 5-7, then body 2-8: 2 + 6 seconds.
        plan = [(0.5, 5), (1.5, 6), (2.5, 2), (3.5, 3), (4.5, 4), (5.5, 5), (6.5, 6), (7.5, 7)]
        for playback_t, source_second in plan:
            self._assert_plays(path, playback_t, source_second)
        self.assertAlmostEqual(self._video_duration(path), 8.0, delta=1 / self.FPS)
        self.assertAlmostEqual(result["duration"], 8.0, delta=0.1)
        self.assertEqual(result["hook"], {"start": 5.0, "end": 7.0, "mode": "repeat"})
        self._assert_words(words, [
            ("w5", 0.2), ("w6", 1.2),
            ("w2", 2.2), ("w3", 3.2), ("w4", 4.2), ("w5", 5.2), ("w6", 6.2), ("w7", 7.2),
        ])
        with open(result["srt_path"], encoding="utf-8") as f:
            self.assertIn("w5 w6 w2 w3 w4 w5 w6 w7", f.read())

    def test_move_plays_the_hook_first_and_drops_it_from_the_body(self):
        result, words = self._render("move")
        path = result["output_path"]
        # Hook 5-7, then body 2-5 and 7-8: 2 + 3 + 1 seconds.
        plan = [(0.5, 5), (1.5, 6), (2.5, 2), (3.5, 3), (4.5, 4), (5.5, 7)]
        for playback_t, source_second in plan:
            self._assert_plays(path, playback_t, source_second)
        # Each of the three parts can round up to a whole frame on its own,
        # depending on the ffmpeg build; captions follow the probed lengths.
        self.assertAlmostEqual(self._video_duration(path), 6.0, delta=3 / self.FPS)
        self.assertAlmostEqual(result["duration"], 6.0, delta=0.1)
        self._assert_words(words, [
            ("w5", 0.2), ("w6", 1.2), ("w2", 2.2), ("w3", 3.2), ("w4", 4.2), ("w7", 5.2),
        ])


if __name__ == "__main__":
    unittest.main()
