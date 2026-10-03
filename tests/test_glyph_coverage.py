"""Tests for backend.services.glyph_coverage: pre-render font coverage check."""

import os
import shutil
import sys
import unittest
from unittest import mock

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BACKEND_ROOT = os.path.join(ROOT, "backend")
if BACKEND_ROOT not in sys.path:
    sys.path.insert(0, BACKEND_ROOT)

from services import glyph_coverage as gc


class RemotionCoverageTests(unittest.TestCase):
    """No real fontconfig/Remotion needed: this is a static subset table."""

    def test_latin_text_is_covered(self):
        self.assertIsNone(gc.check_remotion_font_coverage("Hello world"))

    def test_georgian_text_is_covered(self):
        self.assertIsNone(gc.check_remotion_font_coverage("მიშა"))

    def test_cyrillic_text_is_flagged(self):
        warning = gc.check_remotion_font_coverage("Привет")
        self.assertIsNotNone(warning)
        self.assertIn("П", warning)

    def test_empty_text_is_covered(self):
        self.assertIsNone(gc.check_remotion_font_coverage(""))

    def test_whitespace_and_duplicates_are_not_double_reported(self):
        # "мама" repeats letters; the count should reflect unique characters
        # (2: м, а), not every occurrence across the repeated word.
        warning = gc.check_remotion_font_coverage("мама мама")
        self.assertIsNotNone(warning)
        self.assertIn("2 unique character", warning)


class AssCoverageUnitTests(unittest.TestCase):
    """fc-match/fc-query calls mocked to isolate the coverage logic."""

    def test_returns_none_when_font_cannot_be_resolved(self):
        with mock.patch.object(gc, "resolve_ass_font_path", return_value=None):
            self.assertIsNone(gc.check_ass_font_coverage("text", "Nonexistent Font"))

    def test_returns_none_when_charset_cannot_be_read(self):
        with mock.patch.object(gc, "resolve_ass_font_path", return_value="/fake/font.ttf"), \
             mock.patch.object(gc, "_font_charset_ranges", return_value=None):
            self.assertIsNone(gc.check_ass_font_coverage("text", "Some Font"))

    def test_flags_characters_outside_the_charset(self):
        with mock.patch.object(gc, "resolve_ass_font_path", return_value="/fake/font.ttf"), \
             mock.patch.object(gc, "_font_charset_ranges", return_value=[(0x20, 0x7E)]):
            warning = gc.check_ass_font_coverage("hello მიშა", "Some Font")
        self.assertIsNotNone(warning)
        self.assertIn("მ", warning)

    def test_empty_text_returns_none(self):
        self.assertIsNone(gc.check_ass_font_coverage("", "Some Font"))


class DispatchTests(unittest.TestCase):
    def test_use_ass_true_calls_ass_check(self):
        with mock.patch.object(gc, "check_ass_font_coverage", return_value="ass-warning") as ass_mock, \
             mock.patch.object(gc, "check_remotion_font_coverage") as remotion_mock:
            result = gc.check_caption_font_coverage("text", "Font", False, use_ass=True)
        self.assertEqual(result, "ass-warning")
        ass_mock.assert_called_once()
        remotion_mock.assert_not_called()

    def test_use_ass_false_calls_remotion_check(self):
        with mock.patch.object(gc, "check_ass_font_coverage") as ass_mock, \
             mock.patch.object(gc, "check_remotion_font_coverage", return_value="remotion-warning") as remotion_mock:
            result = gc.check_caption_font_coverage("text", "Font", False, use_ass=False)
        self.assertEqual(result, "remotion-warning")
        remotion_mock.assert_called_once()
        ass_mock.assert_not_called()


@unittest.skipUnless(
    shutil.which("fc-match") and shutil.which("fc-query"), "fontconfig not installed"
)
class AssCoverageRealFontconfigTests(unittest.TestCase):
    """Arial has no Georgian glyphs on a stock macOS/Linux fontconfig setup,
    so it's a reliable real-world case for "font resolves fine, coverage
    doesn't"."""

    def test_arial_covers_ascii(self):
        self.assertIsNone(gc.check_ass_font_coverage("Hello world", "Arial"))

    def test_arial_does_not_cover_georgian(self):
        warning = gc.check_ass_font_coverage("hello მიშა", "Arial")
        self.assertIsNotNone(warning)
        self.assertIn("Arial", warning)

    def test_unresolvable_font_name_does_not_raise(self):
        self.assertIsNone(
            gc.check_ass_font_coverage("text", "Definitely Not A Real Font XYZ")
        )


if __name__ == "__main__":
    unittest.main()
