import os
import sys
import tempfile
import unittest
from unittest import mock


ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BACKEND_ROOT = os.path.join(ROOT, "backend")
if BACKEND_ROOT not in sys.path:
    sys.path.insert(0, BACKEND_ROOT)

import cli as cli_mod


class CliOutputDirTests(unittest.TestCase):
    def test_resolve_output_dir_uses_preset_subfolder_by_default(self):
        output_dir = cli_mod._resolve_output_dir(
            video_path="/Users/nik/Downloads/episode.mp4",
            preset_name="deeptech-show",
            configured_output_dir="",
            explicit_output_dir=None,
        )

        self.assertEqual(output_dir, "/Users/nik/Downloads/clips/deeptech-show")

    def test_resolve_output_dir_uses_configured_root_with_preset_subfolder(self):
        output_dir = cli_mod._resolve_output_dir(
            video_path="/Users/nik/Downloads/episode.mp4",
            preset_name="deeptech-show",
            configured_output_dir="/Users/nik/Downloads/renders",
            explicit_output_dir=None,
        )

        self.assertEqual(output_dir, "/Users/nik/Downloads/renders/deeptech-show")

    def test_resolve_output_dir_keeps_explicit_output_exact(self):
        output_dir = cli_mod._resolve_output_dir(
            video_path="/Users/nik/Downloads/episode.mp4",
            preset_name="deeptech-show",
            configured_output_dir="/Users/nik/Downloads/renders",
            explicit_output_dir="/Users/nik/Downloads/custom-job",
        )

        self.assertEqual(output_dir, "/Users/nik/Downloads/custom-job")

    def test_resolve_output_dir_avoids_double_appending_preset_name(self):
        output_dir = cli_mod._resolve_output_dir(
            video_path="/Users/nik/Downloads/episode.mp4",
            preset_name="deeptech-show",
            configured_output_dir="/Users/nik/Downloads/renders/deeptech-show",
            explicit_output_dir=None,
        )

        self.assertEqual(output_dir, "/Users/nik/Downloads/renders/deeptech-show")

    def test_has_successful_results_true_when_output_exists(self):
        self.assertTrue(
            cli_mod._has_successful_results(
                [
                    {"status": "error", "error": "boom"},
                    {"output_path": "/tmp/clip.mp4"},
                ]
            )
        )

    def test_has_successful_results_false_without_outputs(self):
        self.assertFalse(
            cli_mod._has_successful_results(
                [
                    {"status": "error", "error": "boom"},
                    {"status": "error", "error": "still boom"},
                ]
            )
        )

    def test_should_enter_post_render_loop_when_interrupted_with_completed_results(self):
        should_enter = cli_mod._should_enter_post_render_loop(
            config={"post_render_review": False},
            interrupted=True,
            results=[{"output_path": "/tmp/clip.mp4"}],
        )

        self.assertTrue(should_enter)

    def test_should_not_enter_post_render_loop_when_interrupted_without_completed_results(self):
        should_enter = cli_mod._should_enter_post_render_loop(
            config={"post_render_review": False},
            interrupted=True,
            results=[{"status": "error", "error": "boom"}],
        )

        self.assertFalse(should_enter)

    def test_should_enter_post_render_loop_when_config_enabled(self):
        should_enter = cli_mod._should_enter_post_render_loop(
            config={"post_render_review": True},
            interrupted=False,
            results=[],
        )

        self.assertTrue(should_enter)

    def test_thumbnail_lead_timestamp_uses_frame_before_clip(self):
        self.assertAlmostEqual(
            cli_mod._thumbnail_lead_timestamp(10.0, frame_offset=0.04),
            9.96,
        )

    def test_thumbnail_lead_timestamp_clamps_at_zero(self):
        self.assertEqual(cli_mod._thumbnail_lead_timestamp(0.01, frame_offset=0.04), 0.0)

    def test_extract_thumbnail_lead_frame_returns_path_on_success(self):
        with tempfile.TemporaryDirectory() as td:
            output_path = os.path.join(td, "lead.jpg")

            def fake_run(cmd, timeout, check):
                with open(output_path, "wb") as f:
                    f.write(b"jpg")
                return mock.Mock(returncode=0)

            with mock.patch("utils.proc.run", side_effect=fake_run) as run_mock:
                result = cli_mod._extract_thumbnail_lead_frame(
                    "/tmp/source.mp4",
                    output_path,
                    start_second=12.0,
                )

        self.assertEqual(result, output_path)
        cmd = run_mock.call_args.args[0]
        self.assertIn("11.967", cmd)


if __name__ == "__main__":
    unittest.main()
