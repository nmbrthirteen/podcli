"""Every H.264 encode in the pipeline states its pixel format.

FFmpeg picks the output format by negotiating with the filtergraph when the
command does not say, and some filters answer with 4:4:4 even when every input
is 4:2:0. xfade is one: the mixed-layout crop stitched its runs together and
wrote High 4:4:4 Predictive from yuv420p parts. Everything downstream asks for
profile high, which is 4:2:0 only, so the caption burn aborted with "high
profile doesn't support 4:4:4" and every clip in the render was lost. podcli
carries on past a failed clip, so the run exited 0 with an empty output folder.

The commands are spread across five modules and the next one added will not
remember this, so the rule is checked against the source rather than against
the one command that broke.
"""

import ast
import os
import sys
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BACKEND_ROOT = os.path.join(ROOT, "backend")
if BACKEND_ROOT not in sys.path:
    sys.path.insert(0, BACKEND_ROOT)

from services import encoder, media_probe

SERVICES = os.path.join(BACKEND_ROOT, "services")


def _string_items(node: ast.List) -> list[str]:
    return [e.value for e in node.elts if isinstance(e, ast.Constant) and isinstance(e.value, str)]


def _encode_command_literals():
    """Every list literal in the backend that spells out a libx264 encode."""
    for dirpath, _dirs, files in os.walk(BACKEND_ROOT):
        if "__pycache__" in dirpath:
            continue
        for name in sorted(files):
            if not name.endswith(".py"):
                continue
            path = os.path.join(dirpath, name)
            with open(path, encoding="utf-8") as fh:
                tree = ast.parse(fh.read(), filename=path)
            for node in ast.walk(tree):
                if not isinstance(node, ast.List):
                    continue
                items = _string_items(node)
                if "-c:v" in items and "libx264" in items:
                    yield os.path.relpath(path, ROOT), node.lineno, items


class PixelFormatTests(unittest.TestCase):
    def test_every_libx264_command_pins_yuv420p(self):
        found = 0
        for rel_path, lineno, items in _encode_command_literals():
            found += 1
            with self.subTest(source=f"{rel_path}:{lineno}"):
                self.assertIn("-pix_fmt", items)
                self.assertEqual(items[items.index("-pix_fmt") + 1], "yuv420p")
        # A rename that stops the walk finding anything would pass silently.
        self.assertGreater(found, 5)

    def test_cpu_flags_pin_yuv420p(self):
        self.assertIn("-pix_fmt", media_probe.CPU_FLAGS)
        idx = media_probe.CPU_FLAGS.index("-pix_fmt")
        self.assertEqual(media_probe.CPU_FLAGS[idx + 1], "yuv420p")

    def test_every_preset_asking_for_high_profile_pins_yuv420p(self):
        for name in ("libx264", "h264_videotoolbox", "h264_nvenc"):
            with self.subTest(encoder=name):
                flags = encoder._get_encoder_flags(name)
                self.assertIn("-profile:v", flags)
                self.assertIn("-pix_fmt", flags)
                self.assertEqual(flags[flags.index("-pix_fmt") + 1], "yuv420p")


if __name__ == "__main__":
    unittest.main()
