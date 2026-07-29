"""Single, greppable structured log line for backend events.

The render pipeline previously scattered `print(..., file=sys.stderr)` calls
with inconsistent prefixes, which made it hard to answer the most common
question — "why was this clip framed/encoded this way?". Routing those through
`log_event` gives every line a `[category]` prefix and `key=value` fields so the
chosen path is always visible (and easy to grep) without a debugger.
"""

import os
import sys
import time
from contextlib import contextmanager

_VERBOSE = os.environ.get("PODCLI_LOG_VERBOSE", "").lower() in ("1", "true", "yes")


def log_event(category: str, message: str, *, level: str = "info", **fields) -> None:
    """Emit one structured line: `[category] message k=v k=v`.

    `level="debug"` lines are suppressed unless PODCLI_LOG_VERBOSE is set.
    """
    if level == "debug" and not _VERBOSE:
        return
    parts = [f"[{category}]"]
    if level in ("warn", "error"):
        parts.append(f"{level.upper()}:")
    parts.append(message)
    extras = " ".join(f"{k}={v}" for k, v in fields.items() if v is not None)
    if extras:
        parts.append(extras)
    print(" ".join(parts), file=sys.stderr, flush=True)


def info(category: str, message: str, **fields) -> None:
    log_event(category, message, level="info", **fields)


def warn(category: str, message: str, **fields) -> None:
    log_event(category, message, level="warn", **fields)


def debug(category: str, message: str, **fields) -> None:
    log_event(category, message, level="debug", **fields)


@contextmanager
def timed(category: str, stage: str, **fields):
    """Time a block and emit `[category] timing stage=... ms=...` on exit.

    Debug level, so stage timings stay silent unless PODCLI_LOG_VERBOSE is set.
    Emits on failure too — a stage that blows up after 40s is exactly the one
    worth seeing. Yields a dict the caller can add fields to before the line is
    written, for counts that are only known once the block has run.
    """
    extra: dict = {}
    start = time.perf_counter()
    try:
        yield extra
    finally:
        elapsed_ms = int((time.perf_counter() - start) * 1000)
        log_event(
            category, "timing", level="debug",
            stage=stage, ms=elapsed_ms, **{**fields, **extra},
        )
