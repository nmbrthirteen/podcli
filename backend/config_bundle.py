from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from config.paths import paths, reload_paths

MIGRATION_MARKER_NAME = ".paths-migrated-v1"


MANAGED_FILES = [
    "thumbnail-config.json",
    "corrections.json",
    "ui-state.json",
    "integrations.json",
]

MANAGED_DIRS = [
    "knowledge",
    "presets",
    "history",
    "sessions",
    "packed",
]

ASSET_ARCHIVE_DIR = "assets/files"
ASSET_PATH_KEYS = {
    "logo_path",
    "outro_path",
    "logoPath",
    "outroPath",
}


def _safe_part(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", value.strip())
    safe = safe.strip("._")
    return safe or "asset"


def _home_path() -> Path:
    return Path(paths["home"])


def _marker_path() -> Path:
    return Path(paths["profileMarker"])


def _data_dir() -> Path:
    return Path(paths["cache"]).parent


def _migration_marker_path() -> Path:
    return _data_dir() / MIGRATION_MARKER_NAME


# The old ./podcli kept everything project-local. The native CLI keeps the brand
# brain (presets, knowledge, assets, history, config) and the transcript cache in
# the global managed dir so they follow the user across directories; only clips
# stay in the working dir. Migration therefore reads the *working directory* —
# the old ./podcli folder the user is standing in — and imports it into the global
# home/cache. PODCLI_CWD is injected by the Go launcher; getcwd() is the fallback.
def _legacy_project_dir() -> Path:
    return Path(os.environ.get("PODCLI_CWD") or os.getcwd()).expanduser().resolve()


def _global_home() -> Path:
    return Path(paths["home"]).resolve()


def _legacy_home_dir() -> Path:
    return _legacy_project_dir() / ".podcli"


def _legacy_cache_dir() -> Path:
    # Old layouts stored the transcript cache under <proj>/.podcli/cache (original)
    # or <proj>/data/cache (interim project-local). Prefer whichever holds content.
    proj = _legacy_project_dir()
    for candidate in (proj / ".podcli" / "cache", proj / "data" / "cache"):
        if candidate.is_dir() and any(candidate.iterdir()):
            return candidate
    return proj / ".podcli" / "cache"


def _legacy_presets_dir() -> Path:
    # Ancient layout kept presets at <proj>/presets, outside .podcli. Presets
    # inside .podcli/presets are covered by the brand-brain import instead.
    return _legacy_project_dir() / "presets"


def _legacy_presets_has_content() -> bool:
    legacy = _legacy_presets_dir()
    return legacy.is_dir() and any(legacy.glob("*.json"))


def _legacy_env_file() -> Path:
    return _legacy_project_dir() / ".env"


def _global_env_file() -> Path:
    # Match the file the launcher/loader actually reads (PODCLI_ENV_FILE), so the
    # migrated secrets land where they'll be loaded and "pending" clears correctly.
    return Path(os.environ.get("PODCLI_ENV_FILE") or (_global_home() / ".env"))


def _legacy_env_pending() -> bool:
    src = _legacy_env_file()
    return src.is_file() and src.resolve() != _global_env_file().resolve() and not _global_env_file().exists()


def _legacy_home_pending() -> bool:
    legacy = _legacy_home_dir()
    if legacy.resolve() == _global_home():
        return False
    return _has_managed_content(legacy) and not _has_managed_content(_global_home())


def _asset_alias_keys(raw: str, source: Path) -> list[str]:
    """Every string form an asset path may take in stored JSON. Presets/ui-state
    keep the literal value the user/app wrote, which differs from its realpath
    whenever a path component is a symlink (e.g. macOS /var -> /private/var). The
    bundle rewrite matches literally, so register raw, expanded, and resolved."""
    keys: list[str] = []
    for k in (raw, str(Path(raw).expanduser()) if raw else "", str(source), str(source.resolve())):
        if k and k not in keys:
            keys.append(k)
    return keys


def _legacy_migration_pending() -> bool:
    # Never treat the global managed dir itself as a legacy project to import.
    if _legacy_project_dir().resolve() == _global_home().resolve():
        return False
    return (
        _legacy_home_pending()
        or _legacy_cache_has_content()
        or _legacy_presets_has_content()
        or _legacy_env_pending()
    )


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _iter_files(root: Path) -> list[Path]:
    if not root.exists():
        return []
    return [p for p in root.rglob("*") if p.is_file()]


def _collect_asset_paths(value: Any) -> list[str]:
    paths_found: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            if key in ASSET_PATH_KEYS and isinstance(child, str) and child:
                paths_found.append(child)
            else:
                paths_found.extend(_collect_asset_paths(child))
    elif isinstance(value, list):
        for child in value:
            paths_found.extend(_collect_asset_paths(child))
    return paths_found


def _archive_name_for(index: int, label: str, source: Path) -> str:
    return f"{index:02d}_{_safe_part(label)}_{_safe_part(source.name)}"


def export_config(bundle_path: str, source_home: str | None = None) -> dict[str, Any]:
    home = Path(source_home).expanduser().resolve() if source_home else _home_path()
    if not home.exists():
        raise FileNotFoundError(f"Config home not found: {home}")

    bundle = Path(bundle_path).expanduser().resolve()
    bundle.parent.mkdir(parents=True, exist_ok=True)

    manifest = {
        "version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_home": str(home),
        "managed_files": MANAGED_FILES,
        "managed_dirs": MANAGED_DIRS,
        "asset_archive_dir": ASSET_ARCHIVE_DIR,
    }

    path_map: dict[str, str] = {}

    with zipfile.ZipFile(bundle, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for file_name in MANAGED_FILES:
            src = home / file_name
            if src.exists():
                zf.write(src, arcname=file_name)

        for dir_name in MANAGED_DIRS:
            root = home / dir_name
            for file_path in _iter_files(root):
                zf.write(file_path, arcname=str(file_path.relative_to(home)))

        registry_source = home / "assets" / "registry.json"
        raw_registry = _read_json(registry_source)
        raw_assets = raw_registry.get("assets", []) if isinstance(raw_registry, dict) else []
        registry_export = []
        for index, item in enumerate(raw_assets):
            if not isinstance(item, dict):
                continue
            raw_path = str(item.get("path", ""))
            source = Path(raw_path).expanduser()
            if not source.exists():
                continue
            archive_name = _archive_name_for(index, str(item.get("name", "asset")), source)
            archive_path = f"{ASSET_ARCHIVE_DIR}/{archive_name}"
            zf.write(source, arcname=archive_path)
            registry_export.append({**item, "path": archive_path})
            for key in _asset_alias_keys(raw_path, source):
                path_map[key] = archive_path

        extra_sources: list[tuple[str, Path]] = []
        for rel in ["ui-state.json"]:
            src = home / rel
            if src.exists():
                raw = _read_json(src)
                if raw is not None:
                    for candidate in _collect_asset_paths(raw):
                        candidate_path = Path(candidate).expanduser()
                        if candidate_path.exists():
                            extra_sources.append((candidate, candidate_path))

        presets_dir = home / "presets"
        if presets_dir.exists():
            for preset_file in presets_dir.glob("*.json"):
                raw = _read_json(preset_file)
                if raw is None:
                    continue
                for candidate in _collect_asset_paths(raw):
                    candidate_path = Path(candidate).expanduser()
                    if candidate_path.exists():
                        extra_sources.append((candidate, candidate_path))

        for raw_candidate, source in extra_sources:
            resolved = str(source.resolve())
            if resolved in path_map:
                # Already archived; still register this literal so its JSON gets rewritten.
                path_map.setdefault(raw_candidate, path_map[resolved])
                continue
            archive_name = _archive_name_for(len(path_map), source.stem or "asset", source)
            archive_path = f"{ASSET_ARCHIVE_DIR}/{archive_name}"
            zf.write(source, arcname=archive_path)
            for key in _asset_alias_keys(raw_candidate, source):
                path_map[key] = archive_path

        zf.writestr("assets/registry.json", json.dumps({"assets": registry_export}, indent=2) + "\n")
        manifest["path_map"] = path_map
        manifest["asset_count"] = len(path_map)
        zf.writestr("manifest.json", json.dumps(manifest, indent=2) + "\n")

    return {"bundle": str(bundle), "home": str(home), "asset_count": manifest["asset_count"]}


_ZIP_SYMLINK_TYPE = 0xA


def _safe_extract_zip(zf: zipfile.ZipFile, target: Path) -> None:
    root = target.resolve()
    for info in zf.infolist():
        name = info.filename
        if name.startswith("/") or name.startswith("\\"):
            raise ValueError(f"Unsafe absolute path in bundle: {name}")
        # Reject symlink entries — extraction would follow them outside `root`.
        if (info.external_attr >> 28) & 0xF == _ZIP_SYMLINK_TYPE:
            raise ValueError(f"Symlink entries not allowed in bundle: {name}")
        # Validate directory entries too: extractall creates them, so a "../x/"
        # entry would otherwise escape root before any file is checked.
        dest = (root / name).resolve()
        if os.path.commonpath([str(root), str(dest)]) != str(root):
            raise ValueError(f"Unsafe path in bundle: {name}")
    zf.extractall(root)


def migrate_legacy_cache(*, dry_run: bool = False) -> dict[str, Any]:
    legacy = _legacy_cache_dir()
    target = Path(paths["cache"])
    transcripts = target / "transcripts"
    result: dict[str, Any] = {
        "legacy_dir": str(legacy),
        "target_dir": str(target),
        "moved_json": 0,
        "skipped_json": 0,
        "moved_remotion_bundle": False,
        "dry_run": dry_run,
    }

    if not legacy.is_dir() or legacy.resolve() == target.resolve():
        return result

    target.mkdir(parents=True, exist_ok=True)
    transcripts.mkdir(parents=True, exist_ok=True)

    for item in legacy.iterdir():
        if item.name == "remotion-bundle":
            continue
        if item.is_file() and item.suffix == ".json":
            dest = target / item.name
            if dest.exists():
                result["skipped_json"] += 1
                continue
            if not dry_run:
                shutil.move(str(item), str(dest))
            result["moved_json"] += 1

    legacy_bundle = legacy / "remotion-bundle"
    target_bundle = target / "remotion-bundle"
    if legacy_bundle.is_dir():
        legacy_index = legacy_bundle / "index.html"
        target_index = target_bundle / "index.html"
        if legacy_index.exists() and not target_index.exists():
            if not dry_run:
                if target_bundle.exists():
                    shutil.rmtree(target_bundle)
                shutil.move(str(legacy_bundle), str(target_bundle))
            result["moved_remotion_bundle"] = True
        elif legacy_index.exists() and target_index.exists() and not dry_run:
            shutil.rmtree(legacy_bundle)
            result["removed_duplicate_remotion_bundle"] = True

    if not dry_run:
        try:
            if legacy.is_dir() and not any(legacy.iterdir()):
                legacy.rmdir()
            legacy_parent = legacy.parent
            if legacy_parent.is_dir() and legacy_parent.name == ".podcli" and not any(legacy_parent.iterdir()):
                legacy_parent.rmdir()
        except OSError:
            pass
        try:
            from services.transcript_packer import migrate_transcript_cache_layout
            result["transcript_layout"] = migrate_transcript_cache_layout()
        except Exception:
            result["transcript_layout"] = {"moved_to_transcripts": 0, "skipped": 0}

    return result


def _legacy_cache_has_content() -> bool:
    legacy = _legacy_cache_dir()
    if not legacy.is_dir():
        return False
    return any(legacy.iterdir())


def migrate_legacy_presets(*, dry_run: bool = False) -> dict[str, Any]:
    legacy = _legacy_presets_dir()
    target = Path(paths["home"]) / "presets"
    result: dict[str, Any] = {
        "legacy_dir": str(legacy),
        "target_dir": str(target),
        "moved": 0,
        "skipped": 0,
        "dry_run": dry_run,
    }
    if not legacy.is_dir() or legacy.resolve() == target.resolve():
        return result
    target.mkdir(parents=True, exist_ok=True)
    for src in legacy.glob("*.json"):
        dest = target / src.name
        if dest.exists():
            result["skipped"] += 1
            continue
        if not dry_run:
            shutil.move(str(src), str(dest))
        result["moved"] += 1
    if not dry_run:
        try:
            if legacy.is_dir() and not any(legacy.iterdir()):
                legacy.rmdir()
        except OSError:
            pass
    return result


def migrate_legacy_home(*, dry_run: bool = False) -> dict[str, Any]:
    """Import a project-local .podcli brand brain (presets, knowledge, assets,
    history, config) from the working dir into the global home. Only runs when the
    global home is still empty so it never clobbers an existing global profile."""
    legacy = _legacy_home_dir()
    home = _global_home()
    result: dict[str, Any] = {
        "legacy_home": str(legacy),
        "target_home": str(home),
        "imported": False,
        "dry_run": dry_run,
    }
    if legacy.resolve() == home or not _has_managed_content(legacy):
        return result
    if _has_managed_content(home):
        result["skipped_existing"] = True
        return result
    if dry_run:
        result["imported"] = True
        return result
    # Reuse export/import so asset files get archived and absolute path references
    # inside presets/config are rewritten to the new global home.
    with tempfile.TemporaryDirectory() as tmp:
        bundle = os.path.join(tmp, "legacy-home.zip")
        export_config(bundle, source_home=str(legacy))
        import_config(bundle, target_home=str(home), activate=False)
    result["imported"] = True
    return result


def migrate_legacy_env(*, dry_run: bool = False) -> dict[str, Any]:
    src = _legacy_env_file()
    dest = _global_env_file()
    result: dict[str, Any] = {
        "source": str(src),
        "target": str(dest),
        "copied": False,
        "dry_run": dry_run,
    }
    if not src.is_file() or src.resolve() == dest.resolve() or dest.exists():
        return result
    if not dry_run:
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(src), str(dest))
    result["copied"] = True
    return result


def auto_migrate_legacy_if_pending(*, quiet: bool = True) -> dict[str, Any] | None:
    if not _legacy_migration_pending():
        return None
    return ensure_legacy_migrated(quiet=quiet)


def ensure_legacy_migrated(*, quiet: bool = True) -> dict[str, Any]:
    marker = _migration_marker_path()
    had_marker = marker.exists()
    home_summary = migrate_legacy_home(dry_run=False)
    summary = migrate_legacy_cache(dry_run=False)
    presets_summary = migrate_legacy_presets(dry_run=False)
    env_summary = migrate_legacy_env(dry_run=False)
    summary["home_migration"] = home_summary
    summary["presets_migration"] = presets_summary
    summary["env_migration"] = env_summary
    try:
        from services.transcript_packer import migrate_transcript_cache_layout

        layout = migrate_transcript_cache_layout()
        summary["transcript_layout"] = layout
        layout_moved = layout.get("moved_to_transcripts")
    except Exception:
        summary["transcript_layout"] = {"moved_to_transcripts": 0, "skipped": 0}
        layout_moved = 0
    changed = bool(
        home_summary.get("imported")
        or summary.get("moved_json")
        or summary.get("moved_remotion_bundle")
        or summary.get("removed_duplicate_remotion_bundle")
        or layout_moved
        or presets_summary.get("moved")
        or env_summary.get("copied")
    )
    if not had_marker or changed:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(
            json.dumps(
                {
                    "migrated_at": datetime.now(timezone.utc).isoformat(),
                    "summary": summary,
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    summary["already_migrated"] = had_marker and not changed and not _legacy_migration_pending()
    summary["marker"] = str(marker) if marker.exists() else None
    return summary


def _has_managed_content(home: Path) -> bool:
    for file_name in MANAGED_FILES:
        if (home / file_name).exists():
            return True
    for dir_name in MANAGED_DIRS + ["assets"]:
        if (home / dir_name).exists():
            return True
    return False


def _backup_managed_paths(home: Path) -> Path | None:
    if not _has_managed_content(home):
        return None
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = home.parent / f"{home.name}.backup-{stamp}"
    backup.mkdir(parents=True, exist_ok=True)
    for file_name in MANAGED_FILES:
        src = home / file_name
        if src.exists():
            shutil.copy2(src, backup / file_name)
    for dir_name in MANAGED_DIRS + ["assets"]:
        src = home / dir_name
        if src.exists():
            shutil.copytree(src, backup / dir_name, dirs_exist_ok=True)
    return backup


def _restore_from_backup(home: Path, backup: Path) -> None:
    _cleanup_managed_paths(home)
    for file_name in MANAGED_FILES:
        src = backup / file_name
        if src.exists():
            shutil.copy2(src, home / file_name)
    for dir_name in MANAGED_DIRS + ["assets"]:
        src = backup / dir_name
        if src.exists():
            shutil.copytree(src, home / dir_name, dirs_exist_ok=True)


def _cleanup_managed_paths(home: Path) -> None:
    # manifest.json isn't a managed file but the importer extracts it; clear it
    # here so a rollback restores a home identical to the pre-import state.
    for file_name in MANAGED_FILES + ["manifest.json"]:
        path = home / file_name
        if path.exists():
            path.unlink()

    for dir_name in MANAGED_DIRS + ["assets"]:
        path = home / dir_name
        if path.exists():
            shutil.rmtree(path)


def _rewrite_asset_paths(home: Path, path_map: dict[str, str]) -> None:
    if not path_map:
        return

    archive_to_target = {
        archive_path: str((home / archive_path).resolve())
        for archive_path in path_map.values()
    }
    source_to_target = {
        source_path: archive_to_target[archive_path]
        for source_path, archive_path in path_map.items()
        if archive_path in archive_to_target
    }

    for json_path in [p for p in home.rglob("*.json") if p.is_file() and p.name != "manifest.json"]:
        if json_path.parts[-2:] == ("assets", "registry.json"):
            continue
        try:
            data = json.loads(json_path.read_text(encoding="utf-8"))
        except Exception:
            continue

        def rewrite(value: Any) -> Any:
            if isinstance(value, dict):
                return {k: rewrite(v) for k, v in value.items()}
            if isinstance(value, list):
                return [rewrite(v) for v in value]
            if isinstance(value, str) and value in source_to_target:
                return source_to_target[value]
            return value

        updated = rewrite(data)
        if updated != data:
            _write_json(json_path, updated)


def import_config(bundle_path: str, target_home: str | None = None, activate: bool = False) -> dict[str, Any]:
    bundle = Path(bundle_path).expanduser().resolve()
    if not bundle.exists():
        raise FileNotFoundError(f"Bundle not found: {bundle}")

    target = Path(target_home).expanduser().resolve() if target_home else _home_path()
    target.mkdir(parents=True, exist_ok=True)

    backup_dir: Path | None = None
    manifest: dict[str, Any] = {}

    try:
        with zipfile.ZipFile(bundle, "r") as zf:
            try:
                manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
            except Exception:
                pass

            backup_dir = _backup_managed_paths(target)
            _cleanup_managed_paths(target)
            _safe_extract_zip(zf, target)

        registry_path = target / "assets" / "registry.json"
        registry = _read_json(registry_path)
        if isinstance(registry, dict):
            assets = registry.get("assets", [])
            rewritten: list[dict[str, Any]] = []
            for item in assets:
                if not isinstance(item, dict):
                    continue
                archive_path = str(item.get("path", ""))
                if not archive_path:
                    continue
                rewritten.append({**item, "path": str((target / archive_path).resolve())})
            _write_json(registry_path, {"assets": rewritten})

        _rewrite_asset_paths(target, manifest.get("path_map", {}) if isinstance(manifest, dict) else {})

        # The extracted manifest carries the exporter's absolute source paths;
        # drop it so they don't sit in the imported home.
        (target / "manifest.json").unlink(missing_ok=True)

        if activate:
            _marker_path().write_text(str(target) + "\n", encoding="utf-8")
            reload_paths()
    except Exception:
        if backup_dir and backup_dir.exists():
            _restore_from_backup(target, backup_dir)
        raise

    return {
        "bundle": str(bundle),
        "home": str(target),
        "activated": activate,
        "manifest": manifest,
        "backup": str(backup_dir) if backup_dir else None,
    }


def set_active_home(home_path: str) -> str:
    target = Path(home_path).expanduser().resolve()
    target.mkdir(parents=True, exist_ok=True)
    _marker_path().write_text(str(target) + "\n", encoding="utf-8")
    reload_paths()
    return str(target)


def get_active_home() -> str:
    return str(_home_path())


def get_config_status() -> dict[str, Any]:
    marker = _migration_marker_path()
    migration_info: dict[str, Any] | None = None
    if marker.exists():
        raw = _read_json(marker)
        if isinstance(raw, dict):
            migration_info = raw
    return {
        "home": get_active_home(),
        "cache": paths["cache"],
        "profile_marker": paths["profileMarker"],
        "legacy_home_pending": _legacy_home_pending(),
        "legacy_cache_pending": _legacy_cache_has_content(),
        "legacy_presets_pending": _legacy_presets_has_content(),
        "legacy_env_pending": _legacy_env_pending(),
        "migration_marker": str(marker) if marker.exists() else None,
        "migration": migration_info,
    }


def run_config_action(
    action: str,
    *,
    bundle_path: str | None = None,
    home: str | None = None,
    activate: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    act = (action or "status").strip().lower()
    if act == "status":
        return get_config_status()
    if act == "migrate":
        if dry_run:
            cache = migrate_legacy_cache(dry_run=True)
            cache["home_migration"] = migrate_legacy_home(dry_run=True)
            cache["presets_migration"] = migrate_legacy_presets(dry_run=True)
            cache["env_migration"] = migrate_legacy_env(dry_run=True)
            return cache
        return ensure_legacy_migrated(quiet=True)
    if act == "export":
        if not bundle_path:
            raise ValueError("bundle_path is required")
        return export_config(bundle_path, source_home=home)
    if act == "import":
        if not bundle_path:
            raise ValueError("bundle_path is required")
        return import_config(bundle_path, target_home=home, activate=activate)
    if act == "use":
        if not home:
            raise ValueError("home is required")
        return {"home": set_active_home(home)}
    raise ValueError(f"Unknown config action: {action}")
