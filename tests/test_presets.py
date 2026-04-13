"""Tests for backend.presets — named show configurations."""

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

import presets


class PresetsTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="podcli-presets-test-")
        self._patcher = mock.patch.object(presets, "PRESETS_DIR", self.tmpdir)
        self._patcher.start()

    def tearDown(self):
        self._patcher.stop()
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_list_presets_empty_dir(self):
        self.assertEqual(presets.list_presets(), [])

    def test_list_presets_nonexistent_dir(self):
        with mock.patch.object(presets, "PRESETS_DIR", "/nonexistent/nowhere"):
            self.assertEqual(presets.list_presets(), [])

    def test_save_and_get_round_trip(self):
        presets.save_preset("myshow", {"caption_style": "karaoke", "top_clips": 8})
        loaded = presets.get_preset("myshow")
        self.assertEqual(loaded["caption_style"], "karaoke")
        self.assertEqual(loaded["top_clips"], 8)
        self.assertEqual(loaded["name"], "myshow")

    def test_get_preset_merges_with_defaults(self):
        presets.save_preset("partial", {"caption_style": "hormozi"})
        loaded = presets.get_preset("partial")
        # Provided key takes precedence
        self.assertEqual(loaded["caption_style"], "hormozi")
        # Missing keys come from defaults
        self.assertEqual(loaded["crop_strategy"], presets.DEFAULT_PRESET["crop_strategy"])
        self.assertEqual(loaded["whisper_model"], presets.DEFAULT_PRESET["whisper_model"])

    def test_get_preset_default_returns_copy(self):
        # "default" is special — returns a fresh copy of DEFAULT_PRESET
        d = presets.get_preset("default")
        self.assertEqual(d, presets.DEFAULT_PRESET)
        # Mutating the returned dict must not affect subsequent calls
        d["caption_style"] = "MUTATED"
        d2 = presets.get_preset("default")
        self.assertEqual(d2["caption_style"], presets.DEFAULT_PRESET["caption_style"])

    def test_get_preset_missing_raises(self):
        with self.assertRaises(FileNotFoundError):
            presets.get_preset("does-not-exist")

    def test_save_strips_name_field(self):
        presets.save_preset("stripped", {"name": "ignore-me", "top_clips": 10})
        # Verify the "name" key is NOT written to disk
        with open(os.path.join(self.tmpdir, "stripped.json")) as f:
            raw = json.load(f)
        self.assertNotIn("name", raw)
        self.assertEqual(raw["top_clips"], 10)

    def test_save_preserves_arbitrary_keys(self):
        # save_preset stores ALL provided keys, even ones not in DEFAULT_PRESET
        presets.save_preset("custom", {"experimental_flag": True, "caption_style": "x"})
        with open(os.path.join(self.tmpdir, "custom.json")) as f:
            raw = json.load(f)
        self.assertTrue(raw["experimental_flag"])

    def test_list_presets_returns_names_and_configs(self):
        presets.save_preset("one", {"caption_style": "a"})
        presets.save_preset("two", {"caption_style": "b"})
        listing = presets.list_presets()
        names = {p["name"] for p in listing}
        self.assertEqual(names, {"one", "two"})

    def test_list_presets_skips_corrupt_files(self):
        # Valid preset
        presets.save_preset("good", {"caption_style": "a"})
        # Corrupt file
        with open(os.path.join(self.tmpdir, "bad.json"), "w") as f:
            f.write("{not valid")
        listing = presets.list_presets()
        names = {p["name"] for p in listing}
        self.assertEqual(names, {"good"})

    def test_delete_preset_existing(self):
        presets.save_preset("goner", {"caption_style": "x"})
        self.assertTrue(presets.delete_preset("goner"))
        self.assertFalse(os.path.exists(os.path.join(self.tmpdir, "goner.json")))

    def test_delete_preset_missing_returns_false(self):
        self.assertFalse(presets.delete_preset("never-existed"))

    def test_default_preset_has_required_keys(self):
        # Regression: verify DEFAULT_PRESET has all the keys the CLI expects
        required_keys = {
            "caption_style", "crop_strategy", "time_adjust",
            "whisper_model", "top_clips", "max_clip_duration",
            "min_clip_duration", "target_lufs", "corrections",
        }
        self.assertTrue(required_keys.issubset(presets.DEFAULT_PRESET.keys()))


if __name__ == "__main__":
    unittest.main()
