"""Single, greppable structured log line for backend events.

The render pipeline previously scattered `print(..., file=sys.stderr)` calls
with inconsistent prefixes, which made it hard to answer the most common
question — "why was this clip framed/encoded this way?". Routing those through
`log_event` gives every line a `[category]` prefix and `key=value` fields so the
chosen path is always visible (and easy to grep) without a debugger.
"""

import os
import sys

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
