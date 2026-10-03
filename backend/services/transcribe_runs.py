"""Per-run receipts for long transcriptions, keyed by {file fingerprint,
engine, model, language}, so a restarted job can resume instead of starting
over: re-uploading a file to AssemblyAI or re-decoding chunks already done.

A receipt is just a JSON file written atomically (temp + rename) under a
directory named for the run's key. Nothing here assumes what's inside a
receipt; each engine decides its own shape (e.g. AssemblyAI stores
transcript_id, the omnilingual chunker stores a chunk's words/segments).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from typing import Any, Optional

_SAFE_KEY = re.compile(r"[^a-zA-Z0-9_.-]+")


def fingerprint_file(file_path: str) -> str:
    """Matches transcript_packer.compute_cache_hash: sha256 of the first
    10MB + size, 16 hex chars. Same file -> same fingerprint regardless of
    which engine/run reads it."""
    size = os.path.getsize(file_path)
    h = hashlib.sha256()
    remaining = 10 * 1024 * 1024
    with open(file_path, "rb") as f:
        while remaining > 0:
            chunk = f.read(min(1 << 20, remaining))
            if not chunk:
                break
            h.update(chunk)
            remaining -= len(chunk)
    h.update(f"size:{size}".encode())
    return h.hexdigest()[:16]


def run_key(file_path: str, engine: str, model_size: Optional[str], language: Optional[str]) -> str:
    fp = fingerprint_file(file_path)
    model_part = _SAFE_KEY.sub("-", model_size or "default")
    lang_part = _SAFE_KEY.sub("-", (language or "auto").lower())
    engine_part = _SAFE_KEY.sub("-", engine)
    return f"{fp}-{engine_part}-{model_part}-{lang_part}"


def run_dir(cache_root: str, file_path: str, engine: str, model_size: Optional[str], language: Optional[str]) -> str:
    return os.path.join(cache_root, "transcribe_runs", run_key(file_path, engine, model_size, language))


def read_receipt(directory: str, name: str) -> Optional[dict]:
    path = os.path.join(directory, name)
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def write_receipt(directory: str, name: str, data: Any) -> None:
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, name)
    tmp_path = f"{path}.{os.getpid()}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f)
    os.replace(tmp_path, path)


def clear_run(directory: str) -> None:
    """Remove every receipt for a run (e.g. once the final transcript is
    assembled, or to force a clean restart)."""
    if not os.path.isdir(directory):
        return
    for name in os.listdir(directory):
        try:
            os.unlink(os.path.join(directory, name))
        except OSError:
            pass
    try:
        os.rmdir(directory)
    except OSError:
        pass
