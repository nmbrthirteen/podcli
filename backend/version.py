import json
import os
from pathlib import Path


def podcli_version() -> str:
    env_version = os.environ.get("PODCLI_VERSION", "").strip()
    if env_version:
        return env_version

    package_json = Path(__file__).resolve().parent.parent / "package.json"
    try:
        version = json.loads(package_json.read_text(encoding="utf-8")).get("version")
        if isinstance(version, str) and version.strip():
            return version
    except (OSError, json.JSONDecodeError):
        pass

    return "0.0.0-dev"


VERSION = podcli_version()
