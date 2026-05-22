"""Tests for portable config bundle export/import."""

import json
import os
import sys
import tempfile
import unittest
import zipfile

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BACKEND_ROOT = os.path.join(ROOT, "backend")
if BACKEND_ROOT not in sys.path:
    sys.path.insert(0, BACKEND_ROOT)

from pathlib import Path

from config_bundle import (
    auto_migrate_legacy_if_pending,
    export_config,
    import_config,
    migrate_legacy_cache,
    run_config_action,
)


class ConfigBundleTests(unittest.TestCase):
    def setUp(self):
        self.src_home = tempfile.mkdtemp(prefix="podcli-src-home-")
        self.dst_home = tempfile.mkdtemp(prefix="podcli-dst-home-")
        self.bundle = os.path.join(tempfile.mkdtemp(prefix="podcli-bundle-"), "profile.zip")

        os.makedirs(os.path.join(self.src_home, "knowledge"), exist_ok=True)
        os.makedirs(os.path.join(self.src_home, "presets"), exist_ok=True)
        os.makedirs(os.path.join(self.src_home, "history"), exist_ok=True)
        os.makedirs(os.path.join(self.src_home, "assets"), exist_ok=True)

        with open(os.path.join(self.src_home, "thumbnail-config.json"), "w", encoding="utf-8") as f:
            json.dump({"line1_font_size": "64px"}, f)
        with open(os.path.join(self.src_home, "corrections.json"), "w", encoding="utf-8") as f:
            json.dump({"Boxel": "Voxel"}, f)
        with open(os.path.join(self.src_home, "ui-state.json"), "w", encoding="utf-8") as f:
            json.dump({"settings": {"captionStyle": "karaoke"}}, f)
        with open(os.path.join(self.src_home, "integrations.json"), "w", encoding="utf-8") as f:
            json.dump({"resolve": {"enabled": True}}, f)
        with open(os.path.join(self.src_home, "knowledge", "style.md"), "w", encoding="utf-8") as f:
            f.write("# Style\ncustom voice\n")
        with open(os.path.join(self.src_home, "presets", "myshow.json"), "w", encoding="utf-8") as f:
            json.dump({"caption_style": "branded"}, f)
        with open(os.path.join(self.src_home, "history", "clips.json"), "w", encoding="utf-8") as f:
            json.dump([{"id": "clip-1"}], f)

        self.asset_file = os.path.join(self.src_home, "assets", "logo.png")
        with open(self.asset_file, "wb") as f:
            f.write(b"logo")
        with open(os.path.join(self.src_home, "assets", "registry.json"), "w", encoding="utf-8") as f:
            json.dump({"assets": [{"name": "main-logo", "type": "logo", "path": self.asset_file}]}, f)

    def tearDown(self):
        import shutil

        shutil.rmtree(self.src_home, ignore_errors=True)
        shutil.rmtree(self.dst_home, ignore_errors=True)
        shutil.rmtree(os.path.dirname(self.bundle), ignore_errors=True)

    def test_export_and_import_round_trip(self):
        result = export_config(self.bundle, source_home=self.src_home)
        self.assertTrue(os.path.exists(result["bundle"]))

        with zipfile.ZipFile(self.bundle, "r") as zf:
            self.assertIn("manifest.json", zf.namelist())
            self.assertIn("assets/registry.json", zf.namelist())

        imported = import_config(self.bundle, target_home=self.dst_home)
        self.assertEqual(imported["home"], os.path.realpath(self.dst_home))

        with open(os.path.join(self.dst_home, "thumbnail-config.json"), encoding="utf-8") as f:
            self.assertEqual(json.load(f)["line1_font_size"], "64px")
        with open(os.path.join(self.dst_home, "knowledge", "style.md"), encoding="utf-8") as f:
            self.assertIn("custom voice", f.read())
        with open(os.path.join(self.dst_home, "assets", "registry.json"), encoding="utf-8") as f:
            registry = json.load(f)
        self.assertEqual(len(registry["assets"]), 1)
        self.assertTrue(registry["assets"][0]["path"].startswith(os.path.realpath(self.dst_home)))
        self.assertTrue(os.path.exists(registry["assets"][0]["path"]))


    def test_import_restores_backup_on_failure(self):
        export_config(self.bundle, source_home=self.src_home)
        keep_dir = os.path.join(self.dst_home, "knowledge")
        os.makedirs(keep_dir, exist_ok=True)
        keep_file = os.path.join(keep_dir, "keep.md")
        with open(keep_file, "w", encoding="utf-8") as f:
            f.write("must survive")

        real_extract = zipfile.ZipFile.extractall

        def boom(self, path):
            raise OSError("simulated extract failure")

        zipfile.ZipFile.extractall = boom
        try:
            with self.assertRaises(OSError):
                import_config(self.bundle, target_home=self.dst_home)
        finally:
            zipfile.ZipFile.extractall = real_extract

        self.assertTrue(os.path.exists(keep_file))

    def test_auto_migrate_skips_when_no_legacy_cache(self):
        self.assertIsNone(auto_migrate_legacy_if_pending(quiet=True))

    def test_migrate_legacy_presets(self):
        import shutil

        import config.paths as paths_mod
        from config_bundle import migrate_legacy_presets

        legacy_root = os.path.join(os.path.dirname(self.bundle), "legacy-presets-root")
        legacy_presets = os.path.join(legacy_root, "presets")
        os.makedirs(legacy_presets, exist_ok=True)
        with open(os.path.join(legacy_presets, "myshow.json"), "w", encoding="utf-8") as f:
            json.dump({"caption_style": "branded"}, f)

        old_root = paths_mod.paths["project_root"]
        old_home = paths_mod.paths["home"]
        try:
            paths_mod.paths["project_root"] = legacy_root
            paths_mod.paths["home"] = self.dst_home
            target = os.path.join(self.dst_home, "presets")
            os.makedirs(target, exist_ok=True)

            summary = migrate_legacy_presets(dry_run=False)
            self.assertEqual(summary["moved"], 1)
            self.assertTrue(os.path.exists(os.path.join(target, "myshow.json")))
            self.assertFalse(os.path.exists(os.path.join(legacy_presets, "myshow.json")))
        finally:
            paths_mod.paths["project_root"] = old_root
            paths_mod.paths["home"] = old_home
            shutil.rmtree(legacy_root, ignore_errors=True)

    def test_status_does_not_migrate_legacy_cache(self):
        import importlib
        import config.paths as paths_mod
        import config_bundle

        legacy_root = os.path.join(os.path.dirname(self.bundle), "legacy-status")
        legacy = os.path.join(legacy_root, ".podcli", "cache")
        os.makedirs(legacy, exist_ok=True)
        legacy_file = os.path.join(legacy, "stay.json")
        with open(legacy_file, "w", encoding="utf-8") as f:
            json.dump({"words": []}, f)

        old_root = paths_mod.paths["project_root"]
        try:
            paths_mod.paths["project_root"] = legacy_root
            config_bundle.paths["project_root"] = legacy_root
            status = run_config_action("status")
            self.assertTrue(status.get("legacy_cache_pending"))
            self.assertTrue(os.path.exists(legacy_file))
        finally:
            paths_mod.paths["project_root"] = old_root
            config_bundle.paths["project_root"] = old_root
            import shutil
            shutil.rmtree(legacy_root, ignore_errors=True)

    def test_migrate_legacy_cache(self):
        import shutil

        import config.paths as paths_mod
        import config_bundle

        legacy_root = os.path.join(os.path.dirname(self.bundle), "legacy-project")
        legacy = os.path.join(legacy_root, ".podcli", "cache")
        os.makedirs(legacy, exist_ok=True)
        legacy_file = os.path.join(legacy, "abc123.json")
        with open(legacy_file, "w", encoding="utf-8") as f:
            json.dump({"words": []}, f)

        old_root = paths_mod.paths["project_root"]
        old_cache = paths_mod.paths["cache"]
        try:
            paths_mod.paths["project_root"] = legacy_root
            paths_mod.paths["cache"] = os.path.join(self.dst_home, "cache")
            config_bundle.paths["project_root"] = legacy_root
            config_bundle.paths["cache"] = paths_mod.paths["cache"]
            target = paths_mod.paths["cache"]
            os.makedirs(target, exist_ok=True)

            summary = migrate_legacy_cache(dry_run=False)
            self.assertEqual(summary["moved_json"], 1)
            self.assertTrue(os.path.exists(os.path.join(target, "abc123.json")))
            self.assertFalse(os.path.exists(legacy_file))
        finally:
            paths_mod.paths["project_root"] = old_root
            paths_mod.paths["cache"] = old_cache
            config_bundle.paths["project_root"] = old_root
            config_bundle.paths["cache"] = old_cache
            shutil.rmtree(legacy_root, ignore_errors=True)

    def test_safe_extract_rejects_zip_slip(self):
        import zipfile

        evil = os.path.join(os.path.dirname(self.bundle), "evil.zip")
        target = os.path.join(self.dst_home, "import-target")
        os.makedirs(target, exist_ok=True)
        with zipfile.ZipFile(evil, "w") as zf:
            zf.writestr("../outside.txt", "bad")
        with zipfile.ZipFile(evil, "r") as zf:
            from config_bundle import _safe_extract_zip
            with self.assertRaises(ValueError):
                _safe_extract_zip(zf, Path(target))
        os.remove(evil)

    def test_unified_transcript_cache_round_trip(self):
        import config.paths as paths_mod
        import config_bundle
        from services import transcript_packer as tp
        from services.transcript_packer import (
            compute_cache_hash,
            load_cached_transcript_for_video,
            save_cached_transcript_for_video,
        )

        video = os.path.join(self.src_home, "episode.mp4")
        with open(video, "wb") as f:
            f.write(b"fake video bytes for hashing")
        payload = {"words": [{"word": "hi", "start": 0, "end": 1}], "segments": []}
        old_transcripts = paths_mod.paths["transcripts"]
        try:
            paths_mod.paths["transcripts"] = os.path.join(self.dst_home, "cache", "transcripts")
            config_bundle.paths["transcripts"] = paths_mod.paths["transcripts"]
            tp.paths["transcripts"] = paths_mod.paths["transcripts"]
            os.makedirs(paths_mod.paths["transcripts"], exist_ok=True)
            save_cached_transcript_for_video(video, payload)
            loaded = load_cached_transcript_for_video(video)
            self.assertEqual(loaded["words"][0]["word"], "hi")
            h = compute_cache_hash(video)
            self.assertTrue(os.path.exists(os.path.join(paths_mod.paths["transcripts"], f"{h}.json")))
        finally:
            paths_mod.paths["transcripts"] = old_transcripts
            config_bundle.paths["transcripts"] = old_transcripts
            tp.paths["transcripts"] = old_transcripts


if __name__ == "__main__":
    unittest.main()
