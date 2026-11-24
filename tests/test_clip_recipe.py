import json
import os
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BACKEND_ROOT = os.path.join(ROOT, "backend")
if BACKEND_ROOT not in sys.path:
    sys.path.insert(0, BACKEND_ROOT)

from services import clip_recipe


def _recipe(tmp: str, **overrides) -> dict:
    output = os.path.join(tmp, "Big_Answer_short.mp4")
    recipe = clip_recipe.build(
        video_path=os.path.join(tmp, "episode.mp4"),
        clip={
            "start_second": 10.0,
            "end_second": 40.0,
            "title": "Big Answer",
            "segments": [{"start": 10.0, "end": 20.0}, {"start": 25.0, "end": 40.0}],
            "hook": {"start": 30.0, "end": 33.0, "mode": "repeat"},
        },
        settings={"caption_style": "branded", "crop_strategy": "face", "format": "vertical", "logo_path": None},
        result={"output_path": output},
        words_path=os.path.join(tmp, ".podcli", "abc.words.json"),
        quality="high",
    )
    recipe.update(overrides)
    return recipe


class BuildAndSaveTests(unittest.TestCase):
    def test_round_trips_through_the_file_next_to_the_clip(self):
        with tempfile.TemporaryDirectory() as tmp:
            recipe = _recipe(tmp)
            path = clip_recipe.save(recipe)
            self.assertEqual(path, os.path.join(tmp, "Big_Answer_short.recipe.json"))
            self.assertEqual(clip_recipe.load(path), recipe)

    def test_drops_unset_settings(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertNotIn("logo_path", _recipe(tmp)["settings"])

    def test_refuses_a_recipe_from_another_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "x.recipe.json")
            with open(path, "w") as fh:
                json.dump({"version": 99}, fh)
            with self.assertRaises(ValueError):
                clip_recipe.load(path)


class ApplyChangesTests(unittest.TestCase):
    def test_changes_settings_and_clip_fields_without_touching_the_original(self):
        with tempfile.TemporaryDirectory() as tmp:
            recipe = _recipe(tmp)
            updated = clip_recipe.apply_changes(recipe, {"caption_style": "karaoke", "title": "New"})
            self.assertEqual(updated["settings"]["caption_style"], "karaoke")
            self.assertEqual(updated["title"], "New")
            self.assertEqual(recipe["settings"]["caption_style"], "branded")

    def test_none_removes_a_setting(self):
        with tempfile.TemporaryDirectory() as tmp:
            updated = clip_recipe.apply_changes(_recipe(tmp), {"crop_strategy": None})
            self.assertNotIn("crop_strategy", updated["settings"])

    def test_unknown_field_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, "caption_colour"):
                clip_recipe.apply_changes(_recipe(tmp), {"caption_colour": "red"})

    def test_end_before_start_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                clip_recipe.apply_changes(_recipe(tmp), {"end_second": 5.0})

    def test_trim_fits_kept_pieces_to_the_new_edges(self):
        with tempfile.TemporaryDirectory() as tmp:
            updated = clip_recipe.apply_changes(_recipe(tmp), {"start_second": 15.0, "end_second": 30.0})
            self.assertEqual(updated["keep_segments"], [{"start": 15.0, "end": 20.0}, {"start": 25.0, "end": 30.0}])

    def test_a_hand_picked_start_turns_off_the_opening_snap(self):
        with tempfile.TemporaryDirectory() as tmp:
            recipe = _recipe(tmp)
            self.assertIsNone(recipe["trim_opening"])
            self.assertFalse(clip_recipe.apply_changes(recipe, {"start_second": 12.0})["trim_opening"])
            self.assertIsNone(clip_recipe.apply_changes(recipe, {"end_second": 35.0})["trim_opening"])

    def test_trim_past_every_kept_piece_renders_the_plain_range(self):
        with tempfile.TemporaryDirectory() as tmp:
            updated = clip_recipe.apply_changes(_recipe(tmp), {"start_second": 20.5, "end_second": 24.5})
            self.assertIsNone(updated["keep_segments"])


class KeepExistingOutputsTests(unittest.TestCase):
    def test_next_render_skips_every_version_already_on_disk(self):
        from services import clip_generator

        with tempfile.TemporaryDirectory() as tmp:
            for name in ("Big_Answer_short.mp4", "Big_Answer_short-2.mp4", "Other_short.mp4"):
                open(os.path.join(tmp, name), "wb").close()
            clip_generator.keep_existing_outputs(tmp, "Big Answer")
            path = clip_generator._reserve_output_path(tmp, "Big_Answer_short", ".mp4")
            self.assertEqual(path, os.path.join(tmp, "Big_Answer_short-3.mp4"))


class ParseValueTests(unittest.TestCase):
    def test_reads_json_and_falls_back_to_text(self):
        self.assertEqual(clip_recipe.parse_value("0.3"), 0.3)
        self.assertEqual(clip_recipe.parse_value("true"), True)
        self.assertEqual(clip_recipe.parse_value('{"a": 1}'), {"a": 1})
        self.assertEqual(clip_recipe.parse_value("karaoke"), "karaoke")


class RenderTests(unittest.TestCase):
    def test_renders_a_new_version_with_saved_words_and_points_back_to_the_old_one(self):
        with tempfile.TemporaryDirectory() as tmp:
            open(os.path.join(tmp, "episode.mp4"), "wb").close()
            recipe = _recipe(tmp)
            clip_recipe.save_episode_words(recipe["words"], [{"word": "hi", "start": 11.0, "end": 11.4}], {"faces": 1})
            new_output = os.path.join(tmp, "Big_Answer_short-2.mp4")
            with mock.patch("services.clip_generator.generate_clip", return_value={"output_path": new_output}) as generate, \
                    mock.patch("services.clip_generator.keep_existing_outputs") as keep:
                result, path = clip_recipe.render(clip_recipe.apply_changes(recipe, {"caption_style": "karaoke"}))
            keep.assert_called_once_with(tmp, "Big Answer")
            kwargs = generate.call_args.kwargs
            self.assertEqual(kwargs["caption_style"], "karaoke")
            self.assertEqual(kwargs["transcript_words"][0]["word"], "hi")
            self.assertEqual(kwargs["face_map"], {"faces": 1})
            self.assertEqual(kwargs["hook"], {"start": 30.0, "end": 33.0, "mode": "repeat"})
            self.assertIsNone(kwargs["trim_opening"])
            self.assertEqual(result["output_path"], new_output)
            saved = clip_recipe.load(path)
            self.assertEqual(saved["output"], new_output)
            self.assertEqual(saved["previous"], recipe["output"])
            self.assertEqual(saved["settings"]["caption_style"], "karaoke")

    def test_hook_outside_the_trimmed_clip_is_dropped(self):
        with tempfile.TemporaryDirectory() as tmp:
            open(os.path.join(tmp, "episode.mp4"), "wb").close()
            recipe = clip_recipe.apply_changes(_recipe(tmp), {"end_second": 22.0})
            clip_recipe.save_episode_words(recipe["words"], [], None)
            with mock.patch("services.clip_generator.generate_clip", return_value={"output_path": os.path.join(tmp, "v2.mp4")}) as generate, \
                    mock.patch("services.clip_generator.keep_existing_outputs"):
                clip_recipe.render(recipe)
            self.assertIsNone(generate.call_args.kwargs["hook"])

    def test_missing_source_video_fails_before_rendering(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(FileNotFoundError):
                clip_recipe.render(_recipe(tmp))


if __name__ == "__main__":
    unittest.main()
