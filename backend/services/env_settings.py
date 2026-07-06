"""Manage user secrets/settings stored in the global .env (PODCLI_ENV_FILE),
the file dotenv loads at startup. Currently just HF_TOKEN; the registry keeps it
easy to add more."""

from __future__ import annotations

import os
from typing import Any, Optional

SETTINGS = [
    {
        "key": "HF_TOKEN",
        "label": "HuggingFace token",
        "help": "Enables speaker detection (pyannote), which makes face tracking "
        "speaker-aware. Create a token and accept the model terms for "
        "pyannote/speaker-diarization-3.1, segmentation-3.0, and "
        "speaker-diarization-community-1.",
        "url": "https://huggingface.co/settings/tokens",
        "secret": True,
    },
    {
        "key": "ASSEMBLYAI_API_KEY",
        "label": "AssemblyAI API key",
        "help": "Enables AssemblyAI transcription in the Studio and CLI.",
        "url": "https://www.assemblyai.com/dashboard/activation",
        "secret": True,
        "placeholder": "aai_...",
    },
    {
        "key": "PODCLI_CLAUDE_PATH",
        "label": "Claude Code CLI path",
        "help": "Full path to the claude binary when auto-discovery fails. "
        "Usually ~/.local/bin/claude on macOS/Linux or "
        "%USERPROFILE%\\.local\\bin\\claude.exe on Windows.",
        "secret": False,
        "placeholder": "/home/you/.local/bin/claude",
    },
    {
        "key": "PODCLI_CODEX_PATH",
        "label": "Codex CLI path",
        "help": "Full path to the codex binary when auto-discovery fails.",
        "secret": False,
        "placeholder": "/home/you/.local/bin/codex",
    },
]

_KEYS = {s["key"] for s in SETTINGS}


def _env_path() -> str:
    return os.environ.get("PODCLI_ENV_FILE") or os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "..", ".env"
    )


def _read_pairs() -> dict[str, str]:
    path = _env_path()
    pairs: dict[str, str] = {}
    if not os.path.exists(path):
        return pairs
    with open(path, encoding="utf-8") as f:
        for line in f:
            s = line.strip()
            if not s or s.startswith("#") or "=" not in s:
                continue
            k, v = s.split("=", 1)
            pairs[k.strip()] = v.strip()
    return pairs


def _mask(value: str) -> str:
    if not value:
        return ""
    return value[:3] + "…" + value[-4:] if len(value) > 10 else "•" * len(value)


def list_settings() -> list[dict[str, Any]]:
    pairs = _read_pairs()
    out = []
    for s in SETTINGS:
        raw = pairs.get(s["key"], "")
        out.append({
            "key": s["key"],
            "label": s["label"],
            "help": s["help"],
            "url": s.get("url"),
            "placeholder": s.get("placeholder"),
            "secret": s["secret"],
            "set": bool(raw),
            "preview": _mask(raw) if s["secret"] else raw,
        })
    return out


def _write_pairs(pairs: dict[str, str]) -> None:
    """Rewrite the .env preserving comments/unknown lines; upsert known keys in
    place, append new ones. Atomic via temp+rename; mode 0600 (holds secrets)."""
    path = os.path.abspath(_env_path())
    os.makedirs(os.path.dirname(path), exist_ok=True)
    existing_lines = []
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            existing_lines = f.read().splitlines()

    seen = set()
    out_lines = []
    for line in existing_lines:
        s = line.strip()
        if s and not s.startswith("#") and "=" in s:
            k = s.split("=", 1)[0].strip()
            if k in pairs:
                seen.add(k)
                if pairs[k] is None:
                    continue  # unset: drop the line
                out_lines.append(f"{k}={pairs[k]}")
                continue
        out_lines.append(line)
    for k, v in pairs.items():
        if k not in seen and v is not None:
            out_lines.append(f"{k}={v}")

    # Create the temp 0600 up front so the secret is never world-readable, even
    # briefly. os.replace makes the destination inherit this inode (and mode).
    tmp = f"{path}.{os.getpid()}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write("\n".join(out_lines).rstrip("\n") + "\n")
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def set_setting(key: str, value: str) -> None:
    if key not in _KEYS:
        raise ValueError(f"unknown setting {key!r} (known: {', '.join(sorted(_KEYS))})")
    value = (value or "").strip()
    if not value:
        raise ValueError("value is empty")
    if key in ("PODCLI_CLAUDE_PATH", "PODCLI_CODEX_PATH"):
        from services.claude_suggest import _resolve_cli_path
        resolved = _resolve_cli_path(value) or (value if os.path.isfile(value) else None)
        if not resolved:
            raise ValueError(f"path does not exist: {value}")
        value = resolved
    _write_pairs({key: value})


def unset_setting(key: str) -> None:
    if key not in _KEYS:
        raise ValueError(f"unknown setting {key!r} (known: {', '.join(sorted(_KEYS))})")
    _write_pairs({key: None})


def run_env_action(action: str, key: Optional[str] = None, value: Optional[str] = None) -> dict[str, Any]:
    act = (action or "list").strip().lower()
    if act == "list":
        from services.claude_suggest import get_ai_cli_status
        return {
            "settings": list_settings(),
            "path": os.path.abspath(_env_path()),
            "ai_cli": get_ai_cli_status(),
        }
    if act == "set":
        if not key:
            raise ValueError("key is required")
        set_setting(key, value or "")
        return {"ok": True, "key": key}
    if act == "unset":
        if not key:
            raise ValueError("key is required")
        unset_setting(key)
        return {"ok": True, "key": key}
    raise ValueError(f"unknown env action: {action}")
