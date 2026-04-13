"""Tests for backend.services.video_cut.

Mocks proc_run so no actual ffmpeg process is spawned. Verifies the
command shapes, the single-segment shortcut path, and cleanup of
temporary concat artifacts.
"""

import os
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BACKEND_ROOT = os.path.join(ROOT, "backend")
if BACKEND_ROOT not in sys.path:
    sys.path.insert(0, BACKEND_ROOT)

from services import video_cut


def _ok() -> mock.Mock:
    return mock.Mock(returncode=0, stdout="", stderr="")


def _fail() -> mock.Mock:
    return mock.Mock(returncode=1, stdout="", stderr="ffmpeg error")


class CutSegmentTests(unittest.TestCase):
    def test_runs_ffmpeg_with_expected_flags(self):
        with mock.patch.object(video_cut, "proc_run", return_value=_ok()) as mocked:
            out = video_cut.cut_segment("/in.mp4", "/out.mp4", 10.5, 20.25)
            self.assertEqual(out, "/out.mp4")

        cmd = mocked.call_args.args[0]
        joined = " ".join(cmd)
        self.assertIn("-ss 10.5", joined)
        self.assertIn("-t 9.75", joined)  # end - start
        self.assertIn("-i /in.mp4", joined)
        self.assertTrue(cmd[-1] == "/out.mp4")

    def test_raises_on_failure(self):
        with mock.patch.object(video_cut, "proc_run", return_value=_fail()):
            with self.assertRaises(RuntimeError) as ctx:
                video_cut.cut_segment("/in.mp4", "/out.mp4", 0, 5)
            self.assertIn("FFmpeg cut failed", str(ctx.exception))


class CutMultiSegmentTests(unittest.TestCase):
    def test_single_segment_delegates_to_cut_segment(self):
        with mock.patch.object(video_cut, "cut_segment", return_value="/out.mp4") as cs:
            out = video_cut.cut_multi_segment(
                "/in.mp4", "/out.mp4", [{"start": 0, "end": 5}]
            )
            self.assertEqual(out, "/out.mp4")
            cs.assert_called_once_with("/in.mp4", "/out.mp4", 0, 5)

    def test_multi_segment_invokes_cut_then_concat(self):
        # Two-segment flow: two cut_segment calls + one proc_run for concat
        tmpdir = tempfile.mkdtemp(prefix="podcli-cut-test-")
        out_path = os.path.join(tmpdir, "out.mp4")

        def fake_cut(input_path, out_path, start, end):
            # Create a stub file so cleanup works
            with open(out_path, "w") as f:
                f.write("stub")
            return out_path

        try:
            with mock.patch.object(
                video_cut, "cut_segment", side_effect=fake_cut
            ) as cs, mock.patch.object(
                video_cut, "proc_run", return_value=_ok()
            ) as mocked:
                out = video_cut.cut_multi_segment(
                    "/in.mp4",
                    out_path,
                    [
                        {"start": 0, "end": 5},
                        {"start": 10, "end": 15},
                    ],
                )
                self.assertEqual(out, out_path)
                self.assertEqual(cs.call_count, 2)
                # concat call
                cmd = mocked.call_args.args[0]
                self.assertIn("concat", cmd)
                self.assertIn("-c", cmd)
                self.assertIn("copy", cmd)

            # Temp parts should be cleaned up
            for i in range(2):
                self.assertFalse(os.path.exists(os.path.join(tmpdir, f"_part_{i}.mp4")))
            self.assertFalse(os.path.exists(os.path.join(tmpdir, "_concat_parts.txt")))
        finally:
            import shutil
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_multi_segment_cleans_up_on_failure(self):
        tmpdir = tempfile.mkdtemp(prefix="podcli-cut-test-")
        out_path = os.path.join(tmpdir, "out.mp4")

        def fake_cut(input_path, out_path, start, end):
            with open(out_path, "w") as f:
                f.write("stub")
            return out_path

        try:
            with mock.patch.object(video_cut, "cut_segment", side_effect=fake_cut), \
                 mock.patch.object(video_cut, "proc_run", return_value=_fail()):
                with self.assertRaises(RuntimeError):
                    video_cut.cut_multi_segment(
                        "/in.mp4", out_path,
                        [{"start": 0, "end": 5}, {"start": 10, "end": 15}],
                    )
            # Even on failure, temp parts are cleaned up
            for i in range(2):
                self.assertFalse(os.path.exists(os.path.join(tmpdir, f"_part_{i}.mp4")))
        finally:
            import shutil
            shutil.rmtree(tmpdir, ignore_errors=True)


class ReExportTests(unittest.TestCase):
    def test_video_processor_reexports_cut_helpers(self):
        from services import video_processor as vp
        self.assertIs(vp.cut_segment, video_cut.cut_segment)
        self.assertIs(vp.cut_multi_segment, video_cut.cut_multi_segment)


if __name__ == "__main__":
    unittest.main()
