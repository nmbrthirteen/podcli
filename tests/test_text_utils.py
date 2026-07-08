import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend"))

from utils.text import clean_title, truncate_title  # noqa: E402


def test_clean_title_keeps_full_text():
    long_title = "There are aerospace companies where for $300 you have the same part"
    assert clean_title(long_title) == long_title


def test_clean_title_collapses_whitespace():
    assert clean_title("  So here   I am,\n24 years old  ") == "So here I am, 24 years old"


def test_clean_title_handles_empty():
    assert clean_title("") == ""
    assert clean_title(None) == ""


def test_truncate_title_leaves_short_titles_alone():
    assert truncate_title("Short title") == "Short title"


def test_truncate_title_never_cuts_mid_word():
    # The reported bug: a raw [:55] slice produced "...you have t".
    title = "There are aerospace companies where for $300 you have the same part"
    out = truncate_title(title)
    assert out == "There are aerospace companies where for $300 you have…"
    assert len(out) <= 56  # 55 chars plus the ellipsis
    assert not out.rstrip("…").endswith(" ")


def test_truncate_title_strips_trailing_punctuation():
    assert truncate_title("So here I am, 24 years old, and they're like: we are, flying") == (
        "So here I am, 24 years old, and they're like: we are…"
    )


def test_truncate_title_hard_cuts_a_single_long_word():
    out = truncate_title("x" * 80)
    assert out == "x" * 55 + "…"
