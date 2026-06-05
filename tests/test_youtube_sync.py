"""Tests for the YouTube CSV sync attribution and token-state helpers."""

import json
import os
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BACKEND_ROOT = os.path.join(ROOT, "backend")
if BACKEND_ROOT not in sys.path:
    sys.path.insert(0, BACKEND_ROOT)

from services.integrations.youtube import sync as yt_sync
from services.integrations.youtube import client as yt_client


class CsvSyncTests(unittest.TestCase):
    def setUp(self):
        self.entries = [
            {"id": "a", "title": "Why intelligence is commoditized", "duration": 30},
            {"id": "b", "title": "GPU cooling deep dive", "duration": 42},
        ]
        self.saved = None

        def fake_save(entries):
            self.saved = entries
            return "clips.json"

        self._patches = [
            mock.patch.object(yt_sync, "load_clips_history", return_value=self.entries),
            mock.patch.object(yt_sync, "save_clips_history", side_effect=fake_save),
            mock.patch.object(yt_sync, "_refresh_learnings"),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()

    def _csv_rows(self, rows):
        return mock.patch.object(yt_client, "parse_analytics_csv", return_value=rows)

    def test_csv_sync_reports_each_match_with_score(self):
        rows = [
            {"title": "Why intelligence is commoditized", "views": 1000, "retention": 55.0},
            {"title": "GPU cooling deep dive", "views": 500, "retention": 40.0},
        ]
        with self._csv_rows(rows):
            res = yt_sync.sync_from_csv("x.csv")
        self.assertEqual(res["matched"], 2)
        self.assertEqual(len(res["links"]), 2)
        for link in res["links"]:
            self.assertIn("clip_title", link)
            self.assertIn("row_title", link)
            self.assertEqual(link["score"], 1.0)  # exact title match

    def test_csv_sync_leaves_unmatched_below_threshold(self):
        rows = [{"title": "totally unrelated topic about cooking", "views": 9}]
        with self._csv_rows(rows):
            res = yt_sync.sync_from_csv("x.csv", threshold=0.6)
        self.assertEqual(res["matched"], 0)
        self.assertEqual(len(res["unmatched"]), 2)
        self.assertIsNone(self.saved)  # nothing saved when nothing matched

    def test_csv_sync_writes_metrics_onto_matched_clip(self):
        rows = [{"title": "GPU cooling deep dive", "views": 500, "retention": 40.0, "ctr": 5.0}]
        with self._csv_rows(rows):
            yt_sync.sync_from_csv("x.csv")
        matched = next(c for c in self.saved if c["id"] == "b")
        self.assertEqual(matched["metrics"]["views"], 500)
        self.assertIn("fetched_at", matched["metrics"])


class TokenStateTests(unittest.TestCase):
    def test_is_authorized_false_when_missing(self):
        with mock.patch.object(yt_client, "_TOKEN_PATH", "/no/such/token.json"):
            self.assertFalse(yt_client.is_authorized())

    def test_is_authorized_false_for_corrupt_token(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            f.write("{not valid json")
            path = f.name
        try:
            with mock.patch.object(yt_client, "_TOKEN_PATH", path):
                self.assertFalse(yt_client.is_authorized())
        finally:
            os.unlink(path)

    def test_is_authorized_true_with_refresh_token(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump({"refresh_token": "1//abc", "token": "ya29"}, f)
            path = f.name
        try:
            with mock.patch.object(yt_client, "_TOKEN_PATH", path):
                self.assertTrue(yt_client.is_authorized())
        finally:
            os.unlink(path)


if __name__ == "__main__":
    unittest.main()
