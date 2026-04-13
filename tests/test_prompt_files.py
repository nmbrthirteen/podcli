"""Tests for backend.utils.prompt_files."""

import os
import sys
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from backend.utils import prompt_files


class PromptFilesTests(unittest.TestCase):
    def test_write_prompt_file_creates_file_in_podcli_tmp(self):
        path = prompt_files.write_prompt_file("hello prompt")
        try:
            self.assertTrue(os.path.exists(path))
            self.assertIn(".podcli/tmp", path.replace(os.sep, "/"))
            with open(path) as f:
                self.assertEqual(f.read(), "hello prompt")
        finally:
            if os.path.exists(path):
                os.unlink(path)

    def test_write_prompt_file_uses_txt_suffix_by_default(self):
        path = prompt_files.write_prompt_file("x")
        try:
            self.assertTrue(path.endswith(".txt"))
        finally:
            os.unlink(path)

    def test_write_prompt_file_honors_custom_suffix(self):
        path = prompt_files.write_prompt_file("x", suffix=".md")
        try:
            self.assertTrue(path.endswith(".md"))
        finally:
            os.unlink(path)

    def test_cleanup_stale_tmp_files_removes_leftovers(self):
        a = prompt_files.write_prompt_file("a")
        b = prompt_files.write_prompt_file("b")
        # Both files exist before cleanup
        self.assertTrue(os.path.exists(a))
        self.assertTrue(os.path.exists(b))

        removed = prompt_files.cleanup_stale_tmp_files()
        self.assertGreaterEqual(removed, 2)
        self.assertFalse(os.path.exists(a))
        self.assertFalse(os.path.exists(b))

    def test_cleanup_stale_is_idempotent(self):
        # Ensure the tmp dir exists then wipe it
        prompt_files.cleanup_stale_tmp_files()
        # Second call on empty dir should not raise
        self.assertEqual(prompt_files.cleanup_stale_tmp_files(), 0)

    def test_write_prompt_file_returns_unique_paths(self):
        a = prompt_files.write_prompt_file("same content")
        b = prompt_files.write_prompt_file("same content")
        try:
            self.assertNotEqual(a, b)
        finally:
            for p in (a, b):
                if os.path.exists(p):
                    os.unlink(p)


if __name__ == "__main__":
    unittest.main()
