import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend"))

from utils.text import clean_title, safe_filename, truncate_title  # noqa: E402


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


def test_safe_filename_replaces_spaces():
    assert safe_filename("My great clip") == "My_great_clip"


def test_safe_filename_strips_path_separators():
    assert safe_filename("../../etc/passwd") == "etcpasswd"
    assert safe_filename("a/b\\c") == "abc"


def test_safe_filename_strips_characters_windows_rejects():
    assert safe_filename('what: is "this"? <yes> | no*') == "what_is_this_yes_no"


def test_safe_filename_drops_smart_punctuation():
    assert safe_filename("In the end — your “shit” has to work") == "In_the_end_your_shit_has_to_work"


def test_safe_filename_caps_length():
    assert len(safe_filename("word " * 50)) <= 50


def test_safe_filename_has_no_leading_or_trailing_underscore():
    out = safe_filename("  — hello —  ")
    assert out == "hello"


def test_safe_filename_falls_back_when_nothing_survives():
    assert safe_filename("———") == "clip"
    assert safe_filename("") == "clip"
    assert safe_filename(None) == "clip"


# Windows rejects these stems whatever the extension, so "CON.fcpxml" fails too.
def test_safe_filename_escapes_windows_reserved_device_names():
    assert safe_filename("CON") == "CON_clip"
    assert safe_filename("nul") == "nul_clip"
    assert safe_filename("com1") == "com1_clip"
    assert safe_filename("LPT9") == "LPT9_clip"


def test_safe_filename_leaves_names_merely_containing_reserved_words():
    assert safe_filename("Conference") == "Conference"
    assert safe_filename("CON artists") == "CON_artists"
