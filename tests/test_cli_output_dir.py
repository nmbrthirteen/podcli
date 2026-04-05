import os
import sys
import unittest


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


if __name__ == "__main__":
    unittest.main()
