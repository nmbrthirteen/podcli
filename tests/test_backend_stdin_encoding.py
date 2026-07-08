"""The task JSON crosses a Node->Python pipe as UTF-8 and must survive a cp1252 host.

On Windows sys.stdin defaults to the locale encoding. A curly quote (U+201D) encodes
to bytes E2 80 9D, and cp1252 leaves 0x9D undefined, so reading the request raised
UnicodeDecodeError before it was ever parsed. Titles are stored unabbreviated, so
em dashes and smart quotes reach this pipe routinely.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent / "backend"
MAIN = BACKEND / "main.py"

TITLE = "In the end — your “shit” has to work"


def _run_with_locale_encoding(payload: str) -> subprocess.CompletedProcess:
    env = {
        **os.environ,
        "PYTHONIOENCODING": "cp1252",
        "PYTHONUTF8": "0",  # a UTF-8-mode host would mask the bug this guards
    }
    return subprocess.run(
        [sys.executable, str(MAIN)],
        input=payload,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        cwd=str(BACKEND),
        timeout=120,
    )


def test_non_ascii_title_survives_cp1252_stdin():
    payload = json.dumps(
        {"task_id": TITLE, "task_type": "nonexistent_task", "params": {}},
        ensure_ascii=False,
    )
    proc = _run_with_locale_encoding(payload + "\n")

    assert "UnicodeDecodeError" not in proc.stderr
    result = json.loads(proc.stdout.strip().splitlines()[-1])
    assert result["status"] == "error"
    assert result["task_id"] == TITLE
