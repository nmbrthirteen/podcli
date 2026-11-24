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

    def test_refuses_a_file_that_is_not_an_object(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "x.recipe.json")
            with open(path, "w") as fh:
                json.dump([], fh)
            with self.assertRaisesRegex(ValueError, "not a clip recipe"):
                clip_recipe.load(path)

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

    def test_edges_that_are_not_numbers_are_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            for bad in (None, "12", True, float("nan")):
                with self.assertRaisesRegex(ValueError, "start_second"):
                    clip_recipe.apply_changes(_recipe(tmp), {"start_second": bad})

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


class SnapshotWordsTests(unittest.TestCase):
    def test_same_content_reuses_one_file_and_new_content_never_overwrites_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            first = clip_recipe.snapshot_words(tmp, [{"word": "a"}], None)
            again = clip_recipe.snapshot_words(tmp, [{"word": "a"}], None)
            other = clip_recipe.snapshot_words(tmp, [{"word": "b"}], None)
            self.assertEqual(first, again)
            self.assertNotEqual(first, other)
            with open(first) as fh:
                self.assertEqual(json.load(fh)["words"], [{"word": "a"}])


class SnapshotEditedFileTests(unittest.TestCase):
    def test_an_edited_snapshot_is_left_alone_and_a_new_one_written(self):
        with tempfile.TemporaryDirectory() as tmp:
            first = clip_recipe.snapshot_words(tmp, [{"word": "a"}], None)
            with open(first, "w") as fh:
                fh.write("edited")
            second = clip_recipe.snapshot_words(tmp, [{"word": "a"}], None)
            self.assertNotEqual(first, second)
            with open(first) as fh:
                self.assertEqual(fh.read(), "edited")
            with open(second) as fh:
                self.assertEqual(json.load(fh)["words"], [{"word": "a"}])


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
            recipe["words"] = clip_recipe.snapshot_words(tmp, [{"word": "hi", "start": 11.0, "end": 11.4}], {"faces": 1})
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
            recipe["words"] = clip_recipe.snapshot_words(tmp, [], None)
            with mock.patch("services.clip_generator.generate_clip", return_value={"output_path": os.path.join(tmp, "v2.mp4")}) as generate, \
                    mock.patch("services.clip_generator.keep_existing_outputs"):
                clip_recipe.render(recipe)
            self.assertIsNone(generate.call_args.kwargs["hook"])
            self.assertIsNone(clip_recipe.load(clip_recipe.recipe_path_for(os.path.join(tmp, "v2.mp4")))["hook"])

    def test_missing_source_video_fails_before_rendering(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(FileNotFoundError):
                clip_recipe.render(_recipe(tmp))


class ProcessRenderTests(unittest.TestCase):
    def test_each_render_reads_the_current_settings_and_saves_a_matching_recipe(self):
        import cli

        with tempfile.TemporaryDirectory() as tmp:
            config = {"caption_style": "branded", "crop_strategy": "face"}
            outputs = iter([os.path.join(tmp, "one_short.mp4"), os.path.join(tmp, "two_short.mp4")])
            generate = mock.Mock(side_effect=lambda **kw: {"output_path": next(outputs)})
            render = lambda clip: cli._render_clip_with_recipe(
                generate, clip, config=config, video_path=os.path.join(tmp, "episode.mp4"),
                words=[{"word": "hi"}], face_map=None, output_dir=tmp,
            )
            first = render({"start_second": 1.0, "end_second": 20.0, "title": "one"})
            config["caption_style"] = "karaoke"
            second = render({"start_second": 30.0, "end_second": 50.0, "title": "two"})
            self.assertEqual(generate.call_args_list[0].kwargs["caption_style"], "branded")
            self.assertEqual(generate.call_args_list[1].kwargs["caption_style"], "karaoke")
            self.assertEqual(clip_recipe.load(first["recipe_path"])["settings"]["caption_style"], "branded")
            self.assertEqual(clip_recipe.load(second["recipe_path"])["settings"]["caption_style"], "karaoke")
            self.assertEqual(clip_recipe.load(second["recipe_path"])["start_second"], 30.0)


if __name__ == "__main__":
    unittest.main()
