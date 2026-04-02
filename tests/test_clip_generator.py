import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BACKEND_ROOT = os.path.join(ROOT, "backend")
if BACKEND_ROOT not in sys.path:
    sys.path.insert(0, BACKEND_ROOT)

from services import clip_generator as cg


class ClipGeneratorTests(unittest.TestCase):
    def setUp(self):
        self._orig_remotion_available = cg._remotion_available
        cg._remotion_available = None

    def tearDown(self):
        cg._remotion_available = self._orig_remotion_available

    def _fake_exists(self, real_exists):
        def _exists(path):
            if path.endswith(os.path.join("remotion", "render.mjs")):
                return True
            if path.endswith(os.path.join("remotion-bundle", "index.html")):
                return True
            return real_exists(path)
        return _exists

    def test_remotion_runtime_failure_does_not_disable_future_clips(self):
        real_exists = os.path.exists
        fail_result = subprocess.CompletedProcess(
            args=["node"],
            returncode=1,
            stdout="Error: transient render failure",
            stderr="",
        )

        with tempfile.TemporaryDirectory() as td:
            video_path = os.path.join(td, "video.mp4")
            output_path = os.path.join(td, "captioned.mp4")
            with open(video_path, "wb"):
                pass

            with mock.patch.object(cg.os.path, "exists", side_effect=self._fake_exists(real_exists)), \
                 mock.patch.object(cg.shutil, "which", return_value="/usr/bin/node"), \
                 mock.patch("subprocess.run", return_value=fail_result) as mock_run:
                first = cg._render_with_remotion(
                    video_path=video_path,
                    words=[{"word": "hello", "start": 0.0, "end": 0.5}],
                    caption_style="branded",
                    output_path=output_path,
                )
                second = cg._render_with_remotion(
                    video_path=video_path,
                    words=[{"word": "world", "start": 0.5, "end": 1.0}],
                    caption_style="branded",
                    output_path=output_path,
                )

        self.assertFalse(first)
        self.assertFalse(second)
        self.assertIsNone(cg._remotion_available)
        self.assertGreaterEqual(mock_run.call_count, 4)

    def test_remotion_timeout_does_not_disable_future_clips(self):
        real_exists = os.path.exists

        with tempfile.TemporaryDirectory() as td:
            video_path = os.path.join(td, "video.mp4")
            output_path = os.path.join(td, "captioned.mp4")
            with open(video_path, "wb"):
                pass

            timeout_exc = subprocess.TimeoutExpired(cmd=["node"], timeout=600)
            with mock.patch.object(cg.os.path, "exists", side_effect=self._fake_exists(real_exists)), \
                 mock.patch.object(cg.shutil, "which", return_value="/usr/bin/node"), \
                 mock.patch("subprocess.run", side_effect=timeout_exc) as mock_run:
                first = cg._render_with_remotion(
                    video_path=video_path,
                    words=[{"word": "hello", "start": 0.0, "end": 0.5}],
                    caption_style="branded",
                    output_path=output_path,
                )
                second = cg._render_with_remotion(
                    video_path=video_path,
                    words=[{"word": "world", "start": 0.5, "end": 1.0}],
                    caption_style="branded",
                    output_path=output_path,
                )

        self.assertFalse(first)
        self.assertFalse(second)
        self.assertIsNone(cg._remotion_available)
        self.assertGreaterEqual(mock_run.call_count, 4)


if __name__ == "__main__":
    unittest.main()
