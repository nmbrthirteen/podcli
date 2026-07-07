"""
Shared asset registry — works from CLI, Web UI, and MCP.

Assets are named references to local files (logos, intros, outros, music,
images). Register once, use by name everywhere:
  CLI:    ./podcli process video.mp4 --logo deeptech
  MCP:    create_clip(logo_path="deeptech")
  Web UI: pick from the Assets page

One of each defaultable type (logo/outro/intro/music) can be marked the
brand default, applied automatically when no explicit asset is passed.

Registry stored at .podcli/assets/registry.json
"""

import json
import os
import shutil
import subprocess
import sys
import urllib.request
from typing import Optional

from config.paths import paths

SCHEMA_VERSION = 2

# One default stacks per group; legacy "video" assets are outros.
DEFAULTABLE_TYPES = ("logo", "outro", "intro", "music")

_VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".webm", ".m4v"}
_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".svg", ".webp", ".gif"}
_AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg"}


def _registry_path() -> str:
    """Path to the asset registry JSON file."""
    base = paths["assets"]
    os.makedirs(base, exist_ok=True)
    return os.path.join(base, "registry.json")


def _is_outro_type(asset_type: str) -> bool:
    return asset_type in ("outro", "video")


def _same_default_group(a: str, b: str) -> bool:
    if _is_outro_type(a) and _is_outro_type(b):
        return True
    return a == b


def _migrate(assets: list[dict]) -> tuple[list[dict], bool]:
    """Idempotent upgrade: normalize legacy 'video' type to 'outro'."""
    changed = False
    for a in assets:
        if a.get("type") == "video":
            a["type"] = "outro"
            changed = True
    return assets, changed


def _load() -> list[dict]:
    path = _registry_path()
    if not os.path.exists(path):
        return []
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        raw = data.get("assets", []) if isinstance(data, dict) else data
        stored_version = data.get("schemaVersion") if isinstance(data, dict) else None
        assets, changed = _migrate(raw)
        if changed or stored_version != SCHEMA_VERSION:
            try:
                _save(assets)
            except OSError:
                pass  # Read-only FS or racing writer; serve the migrated view.
        return assets
    except (json.JSONDecodeError, IOError):
        return []


def _save(assets: list[dict]):
    path = _registry_path()
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"schemaVersion": SCHEMA_VERSION, "assets": assets}, f, indent=2)
    os.replace(tmp, path)


def _safe_name(name: str) -> str:
    """Keep names URL-safe so they round-trip through the web /assets/:name routes."""
    slug = "".join(c if c.isalnum() or c in "._-" else "-" for c in (name or "").strip())
    return slug.strip("-") or "asset"


def _infer_type(file_path: str) -> str:
    ext = os.path.splitext(file_path)[1].lower()
    if ext in (".png", ".svg"):
        return "logo"
    if ext in _IMAGE_EXTS:
        return "image"
    if ext in _VIDEO_EXTS:
        return "video"
    if ext in _AUDIO_EXTS:
        return "audio"
    return "other"


def register(name: str, file_path: str, asset_type: str = "auto") -> dict:
    """
    Register a file as a named asset.

    Args:
        name: Short name to reference this asset (e.g., "deeptech", "outro")
        file_path: Absolute or relative path to the file
        asset_type: "logo", "outro", "intro", "music", "image", "audio", or
            "auto" (detect from extension)

    Returns:
        The registered asset dict
    """
    file_path = os.path.abspath(file_path)
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"File not found: {file_path}")

    if asset_type == "auto":
        asset_type = _infer_type(file_path)

    name = _safe_name(name)
    assets = _load()

    existing = next((i for i, a in enumerate(assets) if a["name"] == name), None)
    asset = {
        "name": name,
        "type": asset_type,
        "path": file_path,
    }
    if existing is not None and assets[existing].get("default"):
        asset["default"] = True

    if existing is not None:
        assets[existing] = asset
    else:
        assets.append(asset)

    _save(assets)
    return asset


def import_file(source_path: str, name: str, asset_type: str = "auto") -> dict:
    """Copy a file into .podcli/assets/ then register it (self-contained)."""
    source_path = os.path.abspath(source_path)
    if not os.path.exists(source_path):
        raise FileNotFoundError(f"File not found: {source_path}")
    base = paths["assets"]
    os.makedirs(base, exist_ok=True)
    dest = os.path.join(base, _safe_filename(name, source_path))
    shutil.copyfile(source_path, dest)
    return register(name, dest, asset_type)


def import_url(url: str, name: str, asset_type: str = "auto") -> dict:
    """Download a remote URL into .podcli/assets/ then register it."""
    base = paths["assets"]
    os.makedirs(base, exist_ok=True)
    if _looks_like_direct_file(url):
        dest = os.path.join(base, _safe_filename(name, url))
        _download_direct(url, dest)
    else:
        dest = _download_yt_dlp(url, base, name)
    resolved_type = asset_type if asset_type != "auto" else _infer_type(dest)
    return register(name, dest, resolved_type)


def unregister(name: str) -> bool:
    """Remove a named asset. Returns True if found and removed."""
    assets = _load()
    before = len(assets)
    assets = [a for a in assets if a["name"] != name]
    if len(assets) < before:
        _save(assets)
        return True
    return False


def set_default(name: str) -> Optional[dict]:
    """Mark an asset the default for its type, clearing the flag on siblings."""
    assets = _load()
    target = next((a for a in assets if a["name"] == name), None)
    if target is None:
        return None
    for a in assets:
        if _same_default_group(a.get("type", ""), target.get("type", "")):
            a.pop("default", None)
    target["default"] = True
    _save(assets)
    return target


def clear_default(name: str) -> bool:
    assets = _load()
    target = next((a for a in assets if a["name"] == name), None)
    if target is None or not target.get("default"):
        return False
    target.pop("default", None)
    _save(assets)
    return True


def rename(name: str, new_name: str) -> dict:
    """Rename an asset, keeping its file, type, and default flag."""
    new_name = _safe_name(new_name)
    if not new_name:
        raise ValueError("New name is required")
    assets = _load()
    target = next((a for a in assets if a["name"] == name), None)
    if target is None:
        raise KeyError(f"Asset not found: {name}")
    if new_name != name and any(a["name"] == new_name for a in assets):
        raise ValueError(f'An asset named "{new_name}" already exists')
    target["name"] = new_name
    _save(assets)
    return target


def list_assets(asset_type: Optional[str] = None) -> list[dict]:
    """List all registered assets, optionally filtered by type."""
    assets = _load()
    if asset_type:
        return [a for a in assets if a["type"] == asset_type]
    return assets


def _default_of(*types: str) -> Optional[str]:
    """Default asset path for a group: flagged first, then first-existing."""
    assets = _load()
    group = [a for a in assets if any(_same_default_group(a.get("type", ""), t) for t in types)]
    for a in group:
        if a.get("default") and os.path.exists(a["path"]):
            return a["path"]
    for a in group:
        if os.path.exists(a["path"]):
            return a["path"]
    return None


def default_logo() -> Optional[str]:
    """The default logo path, else first existing logo, else None."""
    return _default_of("logo")


def default_outro() -> Optional[str]:
    """The default outro path (incl. legacy 'video'), else first existing, else None."""
    return _default_of("outro")


def default_intro() -> Optional[str]:
    return _default_of("intro")


def default_music() -> Optional[str]:
    return _default_of("music")


def resolve_logo(explicit: Optional[str]) -> Optional[str]:
    """Resolve an explicit logo name/path, falling back to the default logo."""
    if explicit:
        return resolve(explicit)
    return default_logo()


def resolve_outro(explicit: Optional[str]) -> Optional[str]:
    """Resolve an explicit outro name/path, falling back to the default outro."""
    if explicit:
        return resolve(explicit)
    return default_outro()


def resolve_intro(explicit: Optional[str]) -> Optional[str]:
    """Resolve an explicit intro name/path, falling back to the default intro."""
    if explicit:
        return resolve(explicit)
    return default_intro()


def resolve(name_or_path: str) -> Optional[str]:
    """
    Resolve a name or path to an absolute file path.

    First checks if it's a registered asset name, then treats it as a path.
    Returns None if nothing found.
    """
    if not name_or_path:
        return None

    assets = _load()
    for a in assets:
        if a["name"] == name_or_path:
            if os.path.exists(a["path"]):
                return a["path"]

    path = os.path.abspath(name_or_path)
    if os.path.exists(path):
        return path

    return None


def _safe_filename(name: str, source: str) -> str:
    slug = "".join(c if c.isalnum() or c in "._-" else "_" for c in name) or "asset"
    ext = _url_ext(source) or os.path.splitext(source)[1].lower()
    return slug + ext


def _url_ext(url: str) -> str:
    from urllib.parse import urlparse

    return os.path.splitext(urlparse(url).path)[1].lower()


def _looks_like_direct_file(url: str) -> bool:
    ext = _url_ext(url)
    return ext in _IMAGE_EXTS or ext in _AUDIO_EXTS or ext in _VIDEO_EXTS


def _download_direct(url: str, dest: str):
    req = urllib.request.Request(url, headers={"User-Agent": "podcli"})
    with urllib.request.urlopen(req, timeout=60) as resp, open(dest, "wb") as out:
        shutil.copyfileobj(resp, out)


def _download_yt_dlp(url: str, dest_dir: str, name: str) -> str:
    slug = "".join(c if c.isalnum() or c in "._-" else "_" for c in name) or "asset"
    cmd = [
        sys.executable, "-m", "yt_dlp",
        "--no-playlist",
        "--format", "bv*[height<=1080]+ba/b[height<=1080]/bv*+ba/b",
        "--merge-output-format", "mp4",
        "--restrict-filenames", "--windows-filenames",
        "--paths", dest_dir,
        "--output", f"{slug}.%(ext)s",
        "--print", "after_move:podcli-filepath:%(filepath)s",
    ]
    # Hermetic installs bundle ffmpeg off-PATH; yt-dlp needs it to merge streams.
    ffmpeg = os.environ.get("PODCLI_FFMPEG")
    if ffmpeg and os.path.exists(ffmpeg):
        cmd += ["--ffmpeg-location", ffmpeg]
    cmd.append(url)
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"yt-dlp failed for {url}: {proc.stderr[-800:]}")
    file_path = ""
    for line in proc.stdout.splitlines():
        line = line.strip()
        if line.startswith("podcli-filepath:"):
            file_path = line[len("podcli-filepath:"):]
    if not file_path or not os.path.exists(file_path):
        raise RuntimeError(f"yt-dlp reported no output file for {url}")
    return file_path
