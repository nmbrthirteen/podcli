"""Hardened subprocess helpers.

Wraps subprocess.run() with mandatory timeouts, structured logging, and a
typed error surface so hot paths (ffmpeg/ffprobe/whisper/etc.) can't hang
the server or swallow failures silently.
"""

from __future__ import annotations

import logging
import subprocess
import time
from typing import Sequence

log = logging.getLogger("podcli.proc")


class ProcError(RuntimeError):
    """Raised when a wrapped subprocess fails or times out."""

    def __init__(self, cmd: Sequence[str], returncode: int, stderr: str, duration: float):
        self.cmd = list(cmd)
        self.returncode = returncode
        self.stderr = stderr
        self.duration = duration
        tool = cmd[0] if cmd else "?"
        super().__init__(
            f"{tool} failed (rc={returncode}, {duration:.2f}s): {stderr.strip()[:400]}"
        )


def run(
    cmd: Sequence[str],
    *,
    timeout: float,
    check: bool = True,
    input_text: str | None = None,
    cwd: str | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run a command with a mandatory timeout and structured logging.

    - Captures stdout/stderr as text.
    - Logs start, duration, and failure with the tool name + return code.
    - Raises ProcError on non-zero exit (when check=True) or timeout.
    """
    if not cmd:
        raise ValueError("proc.run: cmd must be non-empty")
    tool = cmd[0]
    t0 = time.monotonic()
    log.debug("proc.start tool=%s argc=%d timeout=%.0fs", tool, len(cmd), timeout)
    try:
        result = subprocess.run(
            list(cmd),
            capture_output=True,
            text=True,
            timeout=timeout,
            input=input_text,
            cwd=cwd,
        )
    except subprocess.TimeoutExpired as exc:
        duration = time.monotonic() - t0
        stderr = (exc.stderr or b"").decode("utf-8", "replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
        log.error("proc.timeout tool=%s duration=%.2fs", tool, duration)
        raise ProcError(cmd, -1, f"timeout after {timeout:.0f}s: {stderr}", duration) from exc

    duration = time.monotonic() - t0
    if result.returncode != 0:
        log.warning(
            "proc.fail tool=%s rc=%d duration=%.2fs stderr=%s",
            tool,
            result.returncode,
            duration,
            (result.stderr or "")[-400:].strip(),
        )
        if check:
            raise ProcError(cmd, result.returncode, result.stderr or "", duration)
    else:
        log.debug("proc.ok tool=%s duration=%.2fs", tool, duration)
    return result
