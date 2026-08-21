"""Tests for `podcli studio --format`.

The studio pipeline renders three things and stitches them: the fragment, the
two bookend cards, and the concat that joins them. All three had the vertical
canvas baked in, so the shape has to reach every one of them or the result is a
correctly-shaped fragment pillarboxed onto a 1080x1920 canvas.

These assert the wiring rather than the pixels: that the shape survives the
hand-off to the render script, that it defaults to what the command did before,
and that each stage is handed the canvas the format spec names.
"""

import argparse
import os
import sys
import unittest
from unittest import mock

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BACKEND_ROOT = os.path.join(ROOT, "backend")
if BACKEND_ROOT not in sys.path:
    sys.path.insert(0, BACKEND_ROOT)

import cli as cli_mod
from services.formats import get_format


def _studio_args(**overrides):
    """The namespace `podcli studio` builds, with only the shape worth varying."""
    args = argparse.Namespace(
        video="video.mp4", save_brand=False, start=0.0, end=10.0, paragraph=None,
        language=None, engine=None, assemblyai_api_key=None,
        caption_style="hormozi", crop="face", format="vertical",
        intro_seconds=2.0, outro_seconds=3.0, outro_title=None, platforms=None,
        accent=None, bg=None, intro_title=None, handle=None, output=None,
        no_intro=True, no_outro=True,
    )
    for key, value in overrides.items():
        setattr(args, key, value)
    return args


def _run_studio(args):
    """Run cmd_studio with the render script stubbed, and return its argv."""
    with mock.patch("subprocess.run") as run:
        run.return_value = mock.Mock(returncode=0)
        with mock.patch.object(cli_mod.os.path, "exists", return_value=True):
            with self_exit():
                cli_mod.cmd_studio(args)
    return run.call_args[0][0]


class self_exit:
    """cmd_studio ends by handing the script's exit code back up."""

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return exc_type is SystemExit


class StudioFormatTests(unittest.TestCase):
    def test_the_shape_reaches_the_render_script(self):
        cmd = _run_studio(_studio_args(format="square"))
        self.assertIn("--format", cmd)
        self.assertEqual(cmd[cmd.index("--format") + 1], "square")

    def test_defaults_to_the_shape_it_always_produced(self):
        cmd = _run_studio(_studio_args())
        self.assertEqual(cmd[cmd.index("--format") + 1], "vertical")

    def test_a_caller_that_names_no_shape_still_gets_one(self):
        """Older callers build this namespace without a format at all."""
        args = _studio_args()
        del args.format
        cmd = _run_studio(args)
        self.assertEqual(cmd[cmd.index("--format") + 1], "vertical")


class StudioCanvasTests(unittest.TestCase):
    """Each stage must be handed the canvas, and it must be the spec's."""

    def test_bookend_is_rendered_at_the_canvas_it_is_given(self):
        import clip_studio

        with mock.patch.object(clip_studio.subprocess, "run") as run:
            run.return_value = mock.Mock(returncode=0, stderr="")
            with mock.patch.object(clip_studio.os.path, "exists", return_value=True):
                clip_studio._render_bookend(
                    "intro", "Title", "@handle", ["tiktok"], 2.0, "/tmp/x.mp4",
                    "#FFE000", "#0B0B0F", width=1080, height=1080,
                )

        cmd = run.call_args[0][0]
        self.assertEqual(cmd[cmd.index("--width") + 1], "1080")
        self.assertEqual(cmd[cmd.index("--height") + 1], "1080")

    def test_bookend_still_defaults_to_the_vertical_canvas(self):
        import clip_studio

        with mock.patch.object(clip_studio.subprocess, "run") as run:
            run.return_value = mock.Mock(returncode=0, stderr="")
            with mock.patch.object(clip_studio.os.path, "exists", return_value=True):
                clip_studio._render_bookend(
                    "outro", "Title", None, ["tiktok"], 3.0, "/tmp/x.mp4",
                    "#FFE000", "#0B0B0F",
                )

        cmd = run.call_args[0][0]
        self.assertEqual(cmd[cmd.index("--width") + 1], "1080")
        self.assertEqual(cmd[cmd.index("--height") + 1], "1920")

    def test_concat_normalizes_onto_the_canvas_it_is_given(self):
        import clip_studio

        with mock.patch.object(clip_studio.subprocess, "run") as run:
            run.return_value = mock.Mock(returncode=0, stderr="")
            with mock.patch.object(clip_studio.os.path, "exists", return_value=True):
                clip_studio._concat(["/a.mp4", "/b.mp4"], "/out.mp4",
                                    width=1920, height=1080)

        cmd = run.call_args[0][0]
        graph = cmd[cmd.index("-filter_complex") + 1]
        self.assertIn("scale=1920:1080", graph)
        self.assertIn("pad=1920:1080", graph)
        self.assertNotIn("1080:1920", graph)

    def test_concat_still_defaults_to_the_vertical_canvas(self):
        import clip_studio

        with mock.patch.object(clip_studio.subprocess, "run") as run:
            run.return_value = mock.Mock(returncode=0, stderr="")
            with mock.patch.object(clip_studio.os.path, "exists", return_value=True):
                clip_studio._concat(["/a.mp4", "/b.mp4"], "/out.mp4")

        cmd = run.call_args[0][0]
        graph = cmd[cmd.index("-filter_complex") + 1]
        self.assertIn("scale=1080:1920", graph)

    def test_every_shape_names_a_canvas(self):
        for shape in ("vertical", "horizontal", "square"):
            spec = get_format(shape)
            self.assertGreater(spec.width, 0)
            self.assertGreater(spec.height, 0)


if __name__ == "__main__":
    unittest.main()
