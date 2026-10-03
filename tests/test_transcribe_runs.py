"""Tests for backend.services.transcribe_runs: per-run receipts keyed by
{file fingerprint, engine, model, language} so a restarted job can resume."""

import os
import sys
import tempfile
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BACKEND_ROOT = os.path.join(ROOT, "backend")
if BACKEND_ROOT not in sys.path:
    sys.path.insert(0, BACKEND_ROOT)

from services import transcribe_runs as runs


class FingerprintFileTests(unittest.TestCase):
    def test_same_content_same_fingerprint(self):
        with tempfile.NamedTemporaryFile(delete=False) as a, tempfile.NamedTemporaryFile(delete=False) as b:
            a.write(b"identical bytes")
            b.write(b"identical bytes")
        try:
            self.assertEqual(runs.fingerprint_file(a.name), runs.fingerprint_file(b.name))
        finally:
            os.unlink(a.name)
            os.unlink(b.name)

    def test_different_content_different_fingerprint(self):
        with tempfile.NamedTemporaryFile(delete=False) as a, tempfile.NamedTemporaryFile(delete=False) as b:
            a.write(b"content A")
            b.write(b"content B")
        try:
            self.assertNotEqual(runs.fingerprint_file(a.name), runs.fingerprint_file(b.name))
        finally:
            os.unlink(a.name)
            os.unlink(b.name)


class RunKeyTests(unittest.TestCase):
    def test_different_engine_or_model_or_language_gives_different_key(self):
        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.write(b"same file")
        try:
            base = runs.run_key(f.name, "assemblyai", "base", "en")
            self.assertNotEqual(base, runs.run_key(f.name, "omnilingual", "base", "en"))
            self.assertNotEqual(base, runs.run_key(f.name, "assemblyai", "small", "en"))
            self.assertNotEqual(base, runs.run_key(f.name, "assemblyai", "base", "ka"))
        finally:
            os.unlink(f.name)

    def test_unsafe_characters_are_sanitized(self):
        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.write(b"x")
        try:
            key = runs.run_key(f.name, "assemblyai/v2", "base model", "en US")
            self.assertNotIn("/", key)
            self.assertNotIn(" ", key)
        finally:
            os.unlink(f.name)


class ReceiptRoundTripTests(unittest.TestCase):
    def test_write_then_read_round_trips(self):
        with tempfile.TemporaryDirectory() as tmp:
            runs.write_receipt(tmp, "r.json", {"transcript_id": "abc123"})
            self.assertEqual(runs.read_receipt(tmp, "r.json"), {"transcript_id": "abc123"})

    def test_missing_receipt_is_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(runs.read_receipt(tmp, "missing.json"))

    def test_corrupt_receipt_is_none_not_a_crash(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "bad.json")
            with open(path, "w", encoding="utf-8") as f:
                f.write("not json")
            self.assertIsNone(runs.read_receipt(tmp, "bad.json"))

    def test_write_is_atomic_no_tmp_file_left_behind(self):
        with tempfile.TemporaryDirectory() as tmp:
            runs.write_receipt(tmp, "r.json", {"a": 1})
            self.assertEqual(os.listdir(tmp), ["r.json"])

    def test_clear_run_removes_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = os.path.join(tmp, "a-run")
            runs.write_receipt(run_dir, "r.json", {"a": 1})
            self.assertTrue(os.path.isdir(run_dir))
            runs.clear_run(run_dir)
            self.assertFalse(os.path.exists(run_dir))

    def test_clear_run_on_missing_directory_does_not_raise(self):
        runs.clear_run("/nonexistent/path/for/sure")


if __name__ == "__main__":
    unittest.main()
