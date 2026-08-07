"""Tests for local_reframe scene-cut detection.

count_scene_cuts runs a full decode of every split-screen clip, so it is worth
keeping cheap. It downscales to 320px and drops audio before the scene filter:
the scene score is a whole-frame statistic, so a small copy yields the same
cuts. Measured on a 1080p source, that is ~2x faster with scene scores within
0.005 of the full-resolution values.

These tests pin the flags that make it cheap, so the optimization cannot be
quietly dropped, and cover the parsing/failure contract callers rely on.
"""

import os
import sys
import unittest
from unittest import mock

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BACKEND_ROOT = os.path.join(ROOT, "backend")
if BACKEND_ROOT not in sys.path:
    sys.path.insert(0, BACKEND_ROOT)

from services import local_reframe
from utils.proc import ProcError


def _result(returncode=0, stderr=""):
    return mock.Mock(returncode=returncode, stdout="", stderr=stderr)


# Two cuts, in the showinfo format the parser scrapes.
_TWO_CUTS = (
    "[Parsed_showinfo_1 @ 0x1] n:0 pts:61440 pts_time:4 pos:1 fmt:yuv420p\n"
    "[Parsed_showinfo_1 @ 0x1] n:1 pts:122880 pts_time:8 pos:2 fmt:yuv420p\n"
)


class CountSceneCutsCommandTests(unittest.TestCase):
    def _captured_cmd(self, **kwargs):
        with mock.patch.object(
            local_reframe, "proc_run", return_value=_result(stderr=_TWO_CUTS)
        ) as run:
            local_reframe.count_scene_cuts("/tmp/clip.mp4", **kwargs)
        return run.call_args[0][0]

    def test_downscales_before_scene_filter(self):
        cmd = self._captured_cmd()
        vf = cmd[cmd.index("-filter:v") + 1]
        # Order matters: scale must come first so select sees small frames.
        self.assertTrue(
            vf.startswith("scale=320:-2,"),
            f"scene filter is not running on a downscaled copy: {vf}",
        )
        self.assertIn("select=", vf)
        self.assertIn("showinfo", vf)

    def test_skips_audio_and_subtitle_decode(self):
        cmd = self._captured_cmd()
        self.assertIn("-an", cmd)
        self.assertIn("-sn", cmd)

    def test_threshold_is_passed_through(self):
        vf = self._captured_cmd(threshold=0.5)[
            self._captured_cmd(threshold=0.5).index("-filter:v") + 1
        ]
        self.assertIn("gt(scene,0.5)", vf)


class CountSceneCutsResultTests(unittest.TestCase):
    def test_counts_showinfo_lines(self):
        with mock.patch.object(
            local_reframe, "proc_run", return_value=_result(stderr=_TWO_CUTS)
        ):
            self.assertEqual(local_reframe.count_scene_cuts("/tmp/clip.mp4"), 2)

    def test_no_cuts_returns_zero(self):
        with mock.patch.object(local_reframe, "proc_run", return_value=_result()):
            self.assertEqual(local_reframe.count_scene_cuts("/tmp/clip.mp4"), 0)

    def test_ffmpeg_failure_returns_zero_not_raise(self):
        # Callers treat 0 as "no info" and proceed with their default plan;
        # a scene-detect failure must never take a render down.
        with mock.patch.object(
            local_reframe, "proc_run", return_value=_result(returncode=1)
        ):
            self.assertEqual(local_reframe.count_scene_cuts("/tmp/clip.mp4"), 0)

    def test_proc_error_returns_zero_not_raise(self):
        err = ProcError(["ffmpeg"], returncode=-9, stderr="timed out", duration=180.0)
        with mock.patch.object(local_reframe, "proc_run", side_effect=err):
            self.assertEqual(local_reframe.count_scene_cuts("/tmp/clip.mp4"), 0)


if __name__ == "__main__":
    unittest.main()
