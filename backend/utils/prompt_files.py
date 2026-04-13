"""Safe tempfile helpers for AI CLI prompt handoff.

The Claude/Codex CLIs need a file they can read for the prompt. Writing
these to the repo root (as several call sites used to do via
`tempfile.NamedTemporaryFile(dir=project_dir)`) litters the working
directory with `tmp*.txt` files whenever a process crashes before the
cleanup block runs — which is how we accumulated 150+ stale files.

Route them through `.podcli/tmp/` instead:
- Gitignored by the existing `.podcli/` rule, so no risk of accidental
  commits.
- Easy to wipe as a single directory on a cold start.
- Still a real filesystem path the AI CLIs can read.
"""

from __future__ import annotations

import os
import tempfile


def _tmp_dir() -> str:
    base = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "..",
        "..",
        ".podcli",
        "tmp",
    )
    os.makedirs(base, exist_ok=True)
    return base


def write_prompt_file(prompt: str, suffix: str = ".txt") -> str:
    """Write a prompt to `.podcli/tmp/` and return the absolute path.

    Caller is responsible for deleting the file when done (wrap in
    try/finally). The `.podcli/tmp/` directory stays around across
    runs; only the file is per-call.
    """
    with tempfile.NamedTemporaryFile(
        mode="w",
        suffix=suffix,
        delete=False,
        dir=_tmp_dir(),
    ) as f:
        f.write(prompt)
        return f.name


def cleanup_stale_tmp_files() -> int:
    """Remove any leftover prompt files from previous crashed runs.

    Safe to call at process startup. Returns the count removed.
    """
    tmp = _tmp_dir()
    removed = 0
    try:
        for name in os.listdir(tmp):
            full = os.path.join(tmp, name)
            if os.path.isfile(full):
                try:
                    os.unlink(full)
                    removed += 1
                except OSError:
                    pass
    except FileNotFoundError:
        pass
    return removed
