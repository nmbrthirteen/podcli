"""Title helpers shared by the suggestion pipeline, the CLI, and exporters."""

TITLE_MAX = 55
FILENAME_MAX = 50

_TRAILING_PUNCT = " ,;:-–—"

# Windows refuses these as a filename stem whatever the extension, so "CON.fcpxml"
# fails just as "CON" does.
_WINDOWS_RESERVED = frozenset(
    ["CON", "PRN", "AUX", "NUL"]
    + [f"COM{i}" for i in range(1, 10)]
    + [f"LPT{i}" for i in range(1, 10)]
)


def clean_title(text: str) -> str:
    """Normalize whitespace, keeping the title intact.

    Stored titles are never shortened: the UI wraps them and the terminal
    truncates them, so a cap here would lose text that both could have shown.
    """
    return " ".join((text or "").split())


def truncate_title(text: str, limit: int = TITLE_MAX) -> str:
    """Trim to limit characters on a word boundary, for fixed-width output.

    A raw slice ends titles mid-word ("...on an automot"), which reads as a
    rendering fault rather than an abbreviation. A single long word still gets a
    hard cut, since there is no boundary to fall back to.
    """
    collapsed = clean_title(text)
    if len(collapsed) <= limit:
        return collapsed
    head = collapsed[:limit]
    if " " in head:
        head = head.rsplit(" ", 1)[0]
    return head.rstrip(_TRAILING_PUNCT) + "…"


def safe_filename(text: str, limit: int = FILENAME_MAX, fallback: str = "clip") -> str:
    """Reduce a title to a filename stem: alphanumerics, hyphen, underscore.

    Interpolating a title straight into a path breaks on separators, on characters
    Windows rejects (``:*?"<>|``), and on titles long enough to blow the path limit.
    Punctuation is dropped rather than substituted, so "Q&A" stays "QA" instead of
    growing separators. Whitespace is re-collapsed afterwards, since dropping a
    padded em dash otherwise leaves a gap that becomes "__". A title made entirely
    of punctuation reduces to nothing, hence the fallback.
    """
    kept = "".join(c for c in clean_title(text) if c.isalnum() or c in "-_ ")
    stem = "_".join(kept.split())[:limit].strip("_")
    if not stem:
        return fallback
    if stem.upper() in _WINDOWS_RESERVED:
        return f"{stem}_{fallback}"
    return stem
