"""Tests for backend.services.asset_store — named asset registry."""

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

from services import asset_store


class AssetStoreTests(unittest.TestCase):
    def setUp(self):
        # Redirect the registry to a fresh temp file per test
        self.tmpdir = tempfile.mkdtemp(prefix="podcli-asset-test-")
        self.registry_file = os.path.join(self.tmpdir, "registry.json")
        self._patcher = mock.patch.object(
            asset_store, "_registry_path", return_value=self.registry_file
        )
        self._patcher.start()

    def tearDown(self):
        self._patcher.stop()
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _make_file(self, name: str) -> str:
        path = os.path.join(self.tmpdir, name)
        with open(path, "w") as f:
            f.write("stub")
        return path

    def test_register_new_asset(self):
        file_path = self._make_file("logo.png")
        asset = asset_store.register("brand", file_path, "logo")
        self.assertEqual(asset["name"], "brand")
        self.assertEqual(asset["type"], "logo")
        self.assertTrue(asset["path"].endswith("logo.png"))

    def test_register_auto_detects_type_from_extension(self):
        self.assertEqual(
            asset_store.register("l", self._make_file("l.png"))["type"], "logo"
        )
        self.assertEqual(
            asset_store.register("v", self._make_file("v.mp4"))["type"], "video"
        )
        self.assertEqual(
            asset_store.register("a", self._make_file("a.mp3"))["type"], "audio"
        )
        self.assertEqual(
            asset_store.register("i", self._make_file("i.jpeg"))["type"], "image"
        )
        self.assertEqual(
            asset_store.register("o", self._make_file("o.xyz"))["type"], "other"
        )

    def test_register_rejects_missing_files(self):
        with self.assertRaises(FileNotFoundError):
            asset_store.register("ghost", "/nonexistent/path/to/file.png")

    def test_register_upserts_on_duplicate_name(self):
        a = self._make_file("a.png")
        b = self._make_file("b.png")
        asset_store.register("same", a, "logo")
        asset_store.register("same", b, "logo")
        assets = asset_store.list_assets()
        self.assertEqual(len(assets), 1)
        self.assertTrue(assets[0]["path"].endswith("b.png"))

    def test_list_filters_by_type(self):
        asset_store.register("l1", self._make_file("l1.png"), "logo")
        asset_store.register("o1", self._make_file("o1.mp4"), "outro")
        self.assertEqual(len(asset_store.list_assets("logo")), 1)
        self.assertEqual(len(asset_store.list_assets("outro")), 1)
        self.assertEqual(len(asset_store.list_assets()), 2)

    def test_legacy_video_type_migrates_to_outro(self):
        asset_store.register("legacy", self._make_file("legacy.mp4"), "video")
        loaded = asset_store.list_assets()
        self.assertEqual(loaded[0]["type"], "outro")
        self.assertEqual(len(asset_store.list_assets("outro")), 1)
        self.assertEqual(len(asset_store.list_assets("video")), 0)
        with open(self.registry_file) as f:
            self.assertEqual(json.load(f)["schemaVersion"], asset_store.SCHEMA_VERSION)

    def test_set_and_clear_default(self):
        a = self._make_file("d1.png")
        b = self._make_file("d2.png")
        asset_store.register("d1", a, "logo")
        asset_store.register("d2", b, "logo")
        self.assertEqual(asset_store.default_logo(), a)  # first-existing fallback
        asset_store.set_default("d2")
        self.assertEqual(asset_store.default_logo(), b)
        flagged = [x for x in asset_store.list_assets("logo") if x.get("default")]
        self.assertEqual(len(flagged), 1)
        self.assertTrue(asset_store.clear_default("d2"))
        self.assertEqual(asset_store.default_logo(), a)

    def test_outro_and_video_share_default_group(self):
        vid = self._make_file("share.mp4")
        asset_store.register("shared", vid, "outro")
        asset_store.set_default("shared")
        self.assertEqual(asset_store.default_outro(), vid)

    def test_rename_keeps_file_and_default(self):
        p = self._make_file("r.png")
        asset_store.register("old", p, "logo")
        asset_store.set_default("old")
        renamed = asset_store.rename("old", "new")
        self.assertEqual(renamed["name"], "new")
        self.assertTrue(renamed["default"])
        self.assertEqual(asset_store.resolve("new"), p)
        self.assertIsNone(asset_store.resolve("old"))

    def test_rename_rejects_duplicate(self):
        asset_store.register("a", self._make_file("a.png"), "logo")
        asset_store.register("b", self._make_file("b.png"), "logo")
        with self.assertRaises(ValueError):
            asset_store.rename("a", "b")

    def test_register_sanitizes_unsafe_names(self):
        asset = asset_store.register("my logo/v2", self._make_file("s.png"), "logo")
        self.assertEqual(asset["name"], "my-logo-v2")
        self.assertTrue(asset_store.resolve("my-logo-v2"))

    def test_import_file_copies_into_assets_dir(self):
        src = self._make_file("orig.png")
        with mock.patch.dict(asset_store.paths, {"assets": self.tmpdir}):
            asset = asset_store.import_file(src, "copied", "logo")
        self.assertNotEqual(asset["path"], src)
        self.assertTrue(os.path.exists(asset["path"]))

    def test_unregister_returns_true_when_removed(self):
        asset_store.register("tmp", self._make_file("t.png"), "logo")
        self.assertTrue(asset_store.unregister("tmp"))
        self.assertEqual(asset_store.list_assets(), [])

    def test_unregister_returns_false_for_missing_name(self):
        self.assertFalse(asset_store.unregister("never-existed"))

    def test_resolve_registered_name(self):
        file_path = self._make_file("r.png")
        asset_store.register("friendly", file_path, "logo")
        self.assertEqual(asset_store.resolve("friendly"), file_path)

    def test_resolve_direct_path(self):
        file_path = self._make_file("direct.png")
        self.assertEqual(asset_store.resolve(file_path), file_path)

    def test_resolve_returns_none_for_missing(self):
        self.assertIsNone(asset_store.resolve("nothing-like-this"))
        self.assertIsNone(asset_store.resolve(""))

    def test_resolve_skips_registered_name_if_file_gone(self):
        file_path = self._make_file("gone.png")
        asset_store.register("missing", file_path, "logo")
        os.remove(file_path)
        # registered name points to deleted file → returns None (not the dead path)
        self.assertIsNone(asset_store.resolve("missing"))

    def test_default_logo_returns_first_existing_logo(self):
        self.assertIsNone(asset_store.default_logo())
        asset_store.register("l1", self._make_file("l1.png"), "logo")
        asset_store.register("l2", self._make_file("l2.png"), "logo")
        self.assertTrue(asset_store.default_logo().endswith("l1.png"))

    def test_default_logo_skips_deleted_files(self):
        first = self._make_file("first.png")
        asset_store.register("first", first, "logo")
        second = self._make_file("second.png")
        asset_store.register("second", second, "logo")
        os.remove(first)
        self.assertTrue(asset_store.default_logo().endswith("second.png"))

    def test_resolve_logo_prefers_explicit_over_default(self):
        asset_store.register("fallback", self._make_file("fb.png"), "logo")
        chosen = self._make_file("chosen.png")
        asset_store.register("chosen", chosen, "logo")
        self.assertEqual(asset_store.resolve_logo("chosen"), chosen)

    def test_resolve_logo_falls_back_when_no_explicit(self):
        only = self._make_file("only.png")
        asset_store.register("only", only, "logo")
        self.assertEqual(asset_store.resolve_logo(None), only)

    def test_resolve_outro_uses_default_video(self):
        asset_store.register("logo", self._make_file("l.png"), "logo")
        vid = self._make_file("outro.mp4")
        asset_store.register("outro", vid, "video")
        self.assertEqual(asset_store.resolve_outro(None), vid)

    def test_load_tolerates_corrupt_registry(self):
        with open(self.registry_file, "w") as f:
            f.write("{not valid json")
        # Should return empty, not raise
        self.assertEqual(asset_store.list_assets(), [])

    def test_persistence_round_trip(self):
        asset_store.register("persist-me", self._make_file("p.png"), "logo")
        # Read the raw file back
        with open(self.registry_file) as f:
            raw = json.load(f)
        self.assertIn("assets", raw)
        self.assertEqual(raw["assets"][0]["name"], "persist-me")


if __name__ == "__main__":
    unittest.main()
