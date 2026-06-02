from __future__ import annotations

import os
from pathlib import Path


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _marker_path() -> Path:
    return _project_root() / ".podcli-home"


def _read_home_marker() -> str | None:
    marker = _marker_path()
    if not marker.exists():
        return None
    try:
        value = marker.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return value or None


def _resolve_home() -> Path:
    env_home = os.environ.get("PODCLI_HOME")
    if env_home:
        return Path(env_home).expanduser().resolve()

    marker_home = _read_home_marker()
    if marker_home:
        marker_path = Path(marker_home).expanduser()
        if not marker_path.is_absolute():
            marker_path = (_project_root() / marker_path).resolve()
        else:
            marker_path = marker_path.resolve()
        return marker_path

    return (_project_root() / ".podcli").resolve()


def _build_paths() -> dict[str, str]:
    home = _resolve_home()
    project_root = _project_root()
    data_dir = Path(os.environ.get("PODCLI_DATA", str(project_root / "data"))).expanduser().resolve()
    return {
        "home": str(home),
        "project_root": str(project_root),
        "cache": str(data_dir / "cache"),
        "transcripts": str(data_dir / "cache" / "transcripts"),
        "packed": str(home / "packed"),
        "working": str(data_dir / "working"),
        "output": str(data_dir / "output"),
        "logs": str(data_dir / "logs"),
        "assets": str(home / "assets"),
        "assetsRegistry": str(home / "assets" / "registry.json"),
        "history": str(home / "history"),
        "clipsHistory": str(home / "history" / "clips.json"),
        "knowledge": str(home / "knowledge"),
        "uiState": str(home / "ui-state.json"),
        "thumbnailConfig": str(home / "thumbnail-config.json"),
        "corrections": str(home / "corrections.json"),
        "integrations": str(home / "integrations.json"),
        "profileMarker": str(_marker_path()),
    }


paths = _build_paths()


def reload_paths() -> dict[str, str]:
    """Recompute paths in place after the active-home marker changes, so existing
    `from config.paths import paths` references reflect the new home without a
    process restart."""
    paths.clear()
    paths.update(_build_paths())
    return paths

