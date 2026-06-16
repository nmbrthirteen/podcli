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

    def test_import_rewrites_preset_and_ui_state_asset_paths(self):
        # Regression: a preset/ui-state asset path stored as its literal (possibly
        # symlinked, e.g. macOS /var) value must be rewritten to the new home on
        # import — otherwise it leaks the source machine's path and breaks once the
        # source is gone. self.asset_file is the literal src path (under a tmpdir,
        # which is symlinked on macOS so raw != realpath — exactly the failure case).
        with open(os.path.join(self.src_home, "presets", "branded.json"), "w", encoding="utf-8") as f:
            json.dump({"caption_style": "branded", "logo_path": self.asset_file}, f)
        with open(os.path.join(self.src_home, "ui-state.json"), "w", encoding="utf-8") as f:
            json.dump({"settings": {"logoPath": self.asset_file}}, f)

        export_config(self.bundle, source_home=self.src_home)
        import_config(self.bundle, target_home=self.dst_home)

        home_real = os.path.realpath(self.dst_home)
        with open(os.path.join(self.dst_home, "presets", "branded.json"), encoding="utf-8") as f:
            preset = json.load(f)
        with open(os.path.join(self.dst_home, "ui-state.json"), encoding="utf-8") as f:
            ui = json.load(f)

        self.assertTrue(preset["logo_path"].startswith(home_real), preset["logo_path"])
        self.assertTrue(os.path.exists(preset["logo_path"]))
        self.assertTrue(ui["settings"]["logoPath"].startswith(home_real), ui["settings"]["logoPath"])
        self.assertNotIn(self.src_home, preset["logo_path"])


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

    # The native CLI keeps the brand brain + cache global; migration reads the
    # working dir (PODCLI_CWD) and imports it into the global home/cache.
    def _enter_migration(self, proj_root, global_home):
        import config.paths as paths_mod

        self._paths_mod = paths_mod
        self._saved_paths = {k: paths_mod.paths.get(k) for k in ("home", "cache", "project_root")}
        self._saved_cwd_env = os.environ.get("PODCLI_CWD")
        paths_mod.paths["home"] = global_home
        paths_mod.paths["cache"] = os.path.join(global_home, "data", "cache")
        # The backend install dir must be ignored by migration; point it elsewhere.
        paths_mod.paths["project_root"] = self.src_home
        os.environ["PODCLI_CWD"] = proj_root

    def _exit_migration(self):
        for key, value in self._saved_paths.items():
            if value is None:
                self._paths_mod.paths.pop(key, None)
            else:
                self._paths_mod.paths[key] = value
        if self._saved_cwd_env is None:
            os.environ.pop("PODCLI_CWD", None)
        else:
            os.environ["PODCLI_CWD"] = self._saved_cwd_env

    def test_auto_migrate_skips_when_no_legacy(self):
        empty_proj = os.path.join(os.path.dirname(self.bundle), "empty-proj")
        os.makedirs(empty_proj, exist_ok=True)
        self._enter_migration(empty_proj, self.dst_home)
        try:
            self.assertIsNone(auto_migrate_legacy_if_pending(quiet=True))
        finally:
            self._exit_migration()

    def test_migrate_legacy_cache(self):
        from config_bundle import migrate_legacy_cache as mlc

        proj = os.path.join(os.path.dirname(self.bundle), "legacy-project")
        legacy = os.path.join(proj, ".podcli", "cache")
        os.makedirs(legacy, exist_ok=True)
        with open(os.path.join(legacy, "abc123.json"), "w", encoding="utf-8") as f:
            json.dump({"words": []}, f)

        self._enter_migration(proj, self.dst_home)
        try:
            summary = mlc(dry_run=False)
            target = os.path.join(self.dst_home, "data", "cache")
            self.assertEqual(summary["moved_json"], 1)
            self.assertTrue(os.path.exists(os.path.join(target, "abc123.json")))
            self.assertFalse(os.path.exists(os.path.join(legacy, "abc123.json")))
        finally:
            self._exit_migration()

    def test_status_reports_legacy_pending_without_migrating(self):
        proj = os.path.join(os.path.dirname(self.bundle), "legacy-status")
        legacy = os.path.join(proj, ".podcli", "cache")
        os.makedirs(legacy, exist_ok=True)
        stay = os.path.join(legacy, "stay.json")
        with open(stay, "w", encoding="utf-8") as f:
            json.dump({"words": []}, f)

        self._enter_migration(proj, self.dst_home)
        try:
            status = run_config_action("status")
            self.assertTrue(status.get("legacy_cache_pending"))
            self.assertTrue(os.path.exists(stay))
        finally:
            self._exit_migration()

    def test_migrate_legacy_top_presets(self):
        from config_bundle import migrate_legacy_presets as mlp

        proj = os.path.join(os.path.dirname(self.bundle), "legacy-presets-root")
        legacy_presets = os.path.join(proj, "presets")
        os.makedirs(legacy_presets, exist_ok=True)
        with open(os.path.join(legacy_presets, "myshow.json"), "w", encoding="utf-8") as f:
            json.dump({"caption_style": "branded"}, f)

        self._enter_migration(proj, self.dst_home)
        try:
            summary = mlp(dry_run=False)
            target = os.path.join(self.dst_home, "presets")
            self.assertEqual(summary["moved"], 1)
            self.assertTrue(os.path.exists(os.path.join(target, "myshow.json")))
        finally:
            self._exit_migration()

    def test_migrate_legacy_home_imports_brand_brain(self):
        from config_bundle import migrate_legacy_home as mlh

        proj = os.path.join(os.path.dirname(self.bundle), "legacy-home")
        os.makedirs(os.path.join(proj, ".podcli", "presets"), exist_ok=True)
        os.makedirs(os.path.join(proj, ".podcli", "knowledge"), exist_ok=True)
        with open(os.path.join(proj, ".podcli", "presets", "show.json"), "w", encoding="utf-8") as f:
            json.dump({"caption_style": "branded"}, f)
        with open(os.path.join(proj, ".podcli", "knowledge", "01-brand.md"), "w", encoding="utf-8") as f:
            f.write("# Brand\n")

        empty_global = os.path.join(os.path.dirname(self.bundle), "empty-global")
        os.makedirs(empty_global, exist_ok=True)
        self._enter_migration(proj, empty_global)
        try:
            summary = mlh(dry_run=False)
            self.assertTrue(summary["imported"])
            self.assertTrue(os.path.exists(os.path.join(empty_global, "presets", "show.json")))
            self.assertTrue(os.path.exists(os.path.join(empty_global, "knowledge", "01-brand.md")))
        finally:
            self._exit_migration()

    def test_migrate_legacy_home_skips_populated_global(self):
        from config_bundle import migrate_legacy_home as mlh

        proj = os.path.join(os.path.dirname(self.bundle), "legacy-home-skip")
        os.makedirs(os.path.join(proj, ".podcli", "presets"), exist_ok=True)
        with open(os.path.join(proj, ".podcli", "presets", "show.json"), "w", encoding="utf-8") as f:
            json.dump({"a": 1}, f)

        # A global home that already holds managed content must not be clobbered.
        os.makedirs(os.path.join(self.dst_home, "presets"), exist_ok=True)
        with open(os.path.join(self.dst_home, "presets", "existing.json"), "w", encoding="utf-8") as f:
            json.dump({"keep": 1}, f)
        self._enter_migration(proj, self.dst_home)
        try:
            summary = mlh(dry_run=False)
            self.assertFalse(summary.get("imported"))
            self.assertTrue(summary.get("skipped_existing"))
            self.assertFalse(os.path.exists(os.path.join(self.dst_home, "presets", "show.json")))
        finally:
            self._exit_migration()

    def test_auto_migrate_from_old_project_dir(self):
        # Running `podcli` in an old ./podcli folder imports its brand brain, cache,
        # ancient top-level presets, and .env into the GLOBAL store. PODCLI_CWD is
        # the folder the user runs in; paths["home"]/["cache"] are the global targets.
        proj = os.path.join(os.path.dirname(self.bundle), "old-project")
        os.makedirs(os.path.join(proj, ".podcli", "presets"), exist_ok=True)
        os.makedirs(os.path.join(proj, ".podcli", "knowledge"), exist_ok=True)
        os.makedirs(os.path.join(proj, ".podcli", "cache"), exist_ok=True)
        os.makedirs(os.path.join(proj, "presets"), exist_ok=True)
        with open(os.path.join(proj, ".podcli", "presets", "show.json"), "w", encoding="utf-8") as f:
            json.dump({"caption_style": "branded"}, f)
        with open(os.path.join(proj, ".podcli", "knowledge", "01-brand.md"), "w", encoding="utf-8") as f:
            f.write("# Brand\n")
        with open(os.path.join(proj, ".podcli", "cache", "ep1.json"), "w", encoding="utf-8") as f:
            json.dump({"words": []}, f)
        with open(os.path.join(proj, "presets", "ancient.json"), "w", encoding="utf-8") as f:
            json.dump({"old": 1}, f)
        with open(os.path.join(proj, ".env"), "w", encoding="utf-8") as f:
            f.write("OPENAI_API_KEY=sk-test\n")

        empty_global = os.path.join(os.path.dirname(self.bundle), "empty-global2")
        os.makedirs(empty_global, exist_ok=True)
        self._enter_migration(proj, empty_global)
        try:
            summary = auto_migrate_legacy_if_pending(quiet=True)
            self.assertIsNotNone(summary)
            self.assertTrue(summary["home_migration"]["imported"])
            self.assertEqual(summary["moved_json"], 1)
            self.assertEqual(summary["presets_migration"]["moved"], 1)
            self.assertTrue(summary["env_migration"]["copied"])
            self.assertTrue(os.path.exists(os.path.join(empty_global, "presets", "show.json")))
            self.assertTrue(os.path.exists(os.path.join(empty_global, "presets", "ancient.json")))
            self.assertTrue(os.path.exists(os.path.join(empty_global, "knowledge", "01-brand.md")))
            self.assertTrue(os.path.exists(os.path.join(empty_global, "data", "cache", "ep1.json")))
            self.assertTrue(os.path.exists(os.path.join(empty_global, ".env")))
        finally:
            self._exit_migration()

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
