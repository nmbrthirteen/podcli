"""
Shared asset registry — works from CLI, Web UI, and MCP.

Assets are named references to local files (logos, intros, outros, images).
Register once, use by name everywhere:
  CLI:    ./podcli process video.mp4 --logo deeptech
  MCP:    create_clip(logo_path="deeptech")
  Web UI: select from dropdown

Registry stored at .podcli/assets/registry.json
"""

import json
import os
import shutil
from typing import Optional


def _registry_path() -> str:
    """Path to the asset registry JSON file."""
    base = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".podcli", "assets")
    os.makedirs(base, exist_ok=True)
    return os.path.join(base, "registry.json")


def _load() -> list[dict]:
    path = _registry_path()
    if not os.path.exists(path):
        return []
    try:
        with open(path) as f:
            data = json.load(f)
        return data.get("assets", []) if isinstance(data, dict) else data
    except (json.JSONDecodeError, IOError):
        return []


def _save(assets: list[dict]):
    path = _registry_path()
    with open(path, "w") as f:
        json.dump({"assets": assets}, f, indent=2)


def register(name: str, file_path: str, asset_type: str = "auto") -> dict:
    """
    Register a file as a named asset.

    Args:
        name: Short name to reference this asset (e.g., "deeptech", "outro")
        file_path: Absolute or relative path to the file
        asset_type: "logo", "video", "image", "audio", or "auto" (detect from extension)

    Returns:
        The registered asset dict
    """
    file_path = os.path.abspath(file_path)
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"File not found: {file_path}")

    if asset_type == "auto":
        ext = os.path.splitext(file_path)[1].lower()
        type_map = {
            ".png": "logo", ".jpg": "image", ".jpeg": "image", ".svg": "logo",
            ".mp4": "video", ".mov": "video", ".mkv": "video", ".webm": "video",
            ".mp3": "audio", ".wav": "audio", ".m4a": "audio",
        }
        asset_type = type_map.get(ext, "other")

    assets = _load()

    # Update if name exists, otherwise add
    existing = next((i for i, a in enumerate(assets) if a["name"] == name), None)
    asset = {
        "name": name,
        "type": asset_type,
        "path": file_path,
    }

    if existing is not None:
        assets[existing] = asset
    else:
        assets.append(asset)

    _save(assets)
    return asset


def unregister(name: str) -> bool:
    """Remove a named asset. Returns True if found and removed."""
    assets = _load()
    before = len(assets)
    assets = [a for a in assets if a["name"] != name]
    if len(assets) < before:
        _save(assets)
        return True
    return False


def list_assets(asset_type: Optional[str] = None) -> list[dict]:
    """List all registered assets, optionally filtered by type."""
    assets = _load()
    if asset_type:
        return [a for a in assets if a["type"] == asset_type]
    return assets


def resolve(name_or_path: str) -> Optional[str]:
    """
    Resolve a name or path to an absolute file path.

    First checks if it's a registered asset name, then treats it as a path.
    Returns None if nothing found.
    """
    if not name_or_path:
        return None

    # Check registered assets
    assets = _load()
    for a in assets:
        if a["name"] == name_or_path:
            if os.path.exists(a["path"]):
                return a["path"]

    # Treat as direct path
    path = os.path.abspath(name_or_path)
    if os.path.exists(path):
        return path

    return None
