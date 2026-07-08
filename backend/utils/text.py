"""Title helpers shared by the suggestion pipeline and the CLI."""

TITLE_MAX = 55

_TRAILING_PUNCT = " ,;:-–—"


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
