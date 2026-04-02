import os
import subprocess
import sys
import unittest
from unittest import mock


ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BACKEND_ROOT = os.path.join(ROOT, "backend")
if BACKEND_ROOT not in sys.path:
    sys.path.insert(0, BACKEND_ROOT)

from services import thumbnail_html as th


class ThumbnailHtmlTests(unittest.TestCase):
    def test_prepare_thumbnail_lines_compacts_long_sentence_title(self):
        line1, line2 = th._prepare_thumbnail_lines(
            "We build 10 megawatt data centers in 200 days — everyone is shocked",
        )

        self.assertIn("10MW", f"{line1} {line2}")
        self.assertNotIn("everyone", f"{line1} {line2}".lower())
        self.assertLessEqual(len(line1), 24)
        self.assertLessEqual(len(line2), 24)
        self.assertLessEqual(len((line1 + " " + line2).split()), 8)

    def test_prepare_thumbnail_lines_rewrites_overlong_ai_lines(self):
        line1, line2 = th._prepare_thumbnail_lines(
            title="ignored",
            line1="WE BUILD 10 MEGAWATT DATA",
            line2="CENTERS IN 200 DAYS — EVERYONE IS SHOCKED",
        )

        self.assertLessEqual(len(line1), 24)
        self.assertLessEqual(len(line2), 24)
        self.assertNotIn("EVERYONE", f"{line1} {line2}")

    def test_build_remotion_screenshot_command(self):
        script_suffix = os.path.join("backend", "scripts", "remotion_screenshot.cjs")
        renderer_suffix = os.path.join("node_modules", "@remotion", "renderer", "package.json")

        def exists(path):
            return path.endswith(script_suffix) or path.endswith(renderer_suffix)

        with mock.patch.object(th.os.path, "exists", side_effect=exists), \
             mock.patch.object(th.shutil, "which", side_effect=lambda name: { "node": "/usr/local/bin/node" }.get(name)):
            cmd = th._build_remotion_screenshot_command(
                script_path="/repo/backend/scripts/remotion_screenshot.cjs",
                html_path="/tmp/thumb.html",
                output_path="/tmp/thumb.png",
                width=1080,
                height=1920,
                wait_ms=1500,
            )

        self.assertEqual(
            cmd,
            [
                "/usr/local/bin/node",
                "/repo/backend/scripts/remotion_screenshot.cjs",
                "/tmp/thumb.html",
                "/tmp/thumb.png",
                "1080",
                "1920",
                "1500",
            ],
        )

    def test_playwright_cli_candidates_prefers_local_then_global_then_npx(self):
        local_suffix = os.path.join("node_modules", ".bin", "playwright")

        with mock.patch.object(th.os.path, "exists", side_effect=lambda p: p.endswith(local_suffix)), \
             mock.patch.object(th.shutil, "which", side_effect=lambda name: {"playwright": "/usr/local/bin/playwright", "npx": "/usr/local/bin/npx"}.get(name)):
            candidates = th._playwright_cli_candidates()

        self.assertTrue(candidates[0][0].endswith(local_suffix))
        self.assertEqual(candidates[1:], [
            ["/usr/local/bin/playwright"],
            ["/usr/local/bin/npx", "--no-install", "playwright"],
            ["/usr/local/bin/npx", "playwright"],
        ])

    def test_build_playwright_screenshot_command(self):
        cmd = th._build_playwright_screenshot_command(
            cli_cmd=["/usr/local/bin/npx", "--no-install", "playwright"],
            html_path="/tmp/thumb.html",
            output_path="/tmp/thumb.png",
            width=1080,
            height=1920,
            wait_ms=1200,
        )

        self.assertEqual(
            cmd,
            [
                "/usr/local/bin/npx",
                "--no-install",
                "playwright",
                "screenshot",
                "--viewport-size",
                "1080, 1920",
                "--wait-for-timeout",
                "1200",
                "file:///tmp/thumb.html",
                "/tmp/thumb.png",
            ],
        )

    def test_generate_thumbnail_prefers_remotion_before_playwright(self):
        with mock.patch.object(th, "_build_html", return_value="<html></html>"), \
             mock.patch.object(th, "_build_remotion_screenshot_command", return_value=["node", "script", "a", "b", "1080", "1920", "1500"]), \
             mock.patch.object(th, "_playwright_cli_candidates", return_value=[["playwright"]]), \
             mock.patch.object(th, "_build_playwright_screenshot_command", return_value=["playwright", "screenshot"]), \
             mock.patch.object(th.subprocess, "run", side_effect=[
                 subprocess.CompletedProcess(args=["node"], returncode=0, stdout="", stderr=""),
             ]) as run_mock:
            output = th.generate_thumbnail("line1", "line2", "/tmp/thumb.png")

        self.assertEqual(output, "/tmp/thumb.png")
        self.assertEqual(run_mock.call_args_list[0].args[0], ["node", "script", "a", "b", "1080", "1920", "1500"])


if __name__ == "__main__":
    unittest.main()
