"""Tests for backend.main batch clip rendering — worker pool sizing, result
ordering, and clip_complete events for both outcomes."""

import os
import sys
import unittest
from unittest import mock

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BACKEND_ROOT = os.path.join(ROOT, "backend")
if BACKEND_ROOT not in sys.path:
    sys.path.insert(0, BACKEND_ROOT)

import main as backend_main


def _clean_env():
    return {k: v for k, v in os.environ.items() if k != "PODCLI_RENDER_CONCURRENCY"}


class RenderConcurrencyTests(unittest.TestCase):
    def test_env_override_wins(self):
        with mock.patch.dict(os.environ, {"PODCLI_RENDER_CONCURRENCY": "4"}):
            self.assertEqual(backend_main._render_concurrency(), 4)

    def test_env_override_floors_at_one(self):
        with mock.patch.dict(os.environ, {"PODCLI_RENDER_CONCURRENCY": "0"}):
            self.assertEqual(backend_main._render_concurrency(), 1)

    def test_invalid_env_falls_back_to_cpu_rule(self):
        with mock.patch.dict(os.environ, {"PODCLI_RENDER_CONCURRENCY": "junk"}), \
             mock.patch.object(backend_main.os, "cpu_count", return_value=16):
            self.assertEqual(backend_main._render_concurrency(), 2)

    def test_two_workers_on_big_machines(self):
        with mock.patch.dict(os.environ, _clean_env(), clear=True), \
             mock.patch.object(backend_main.os, "cpu_count", return_value=8):
            self.assertEqual(backend_main._render_concurrency(), 2)

    def test_one_worker_on_small_machines(self):
        with mock.patch.dict(os.environ, _clean_env(), clear=True), \
             mock.patch.object(backend_main.os, "cpu_count", return_value=4):
            self.assertEqual(backend_main._render_concurrency(), 1)


class HandleBatchClipsTests(unittest.TestCase):
    def _run_batch(self, concurrency, fail_index=None, n=4):
        clips = [
            {"start_second": float(i * 100), "end_second": float(i * 100 + 30), "title": f"clip_{i}"}
            for i in range(n)
        ]
        params = {"video_path": "/video.mp4", "clips": clips}

        def fake_generate_clip(**kwargs):
            title = kwargs["title"]
            idx = int(title.rsplit("_", 1)[1])
            if fail_index is not None and idx == fail_index:
                raise RuntimeError("render exploded")
            return {
                "output_path": f"/out/{title}.mp4",
                "duration": 30.0,
                "file_size_mb": 1.0,
                "title": title,
            }

        progress_events = []
        results_holder = {}

        def capture_result(task_id, status, data=None, error=None):
            results_holder["status"] = status
            results_holder["data"] = data

        with mock.patch.dict(os.environ, {"PODCLI_RENDER_CONCURRENCY": str(concurrency)}), \
             mock.patch("services.clip_generator.generate_clip", side_effect=fake_generate_clip), \
             mock.patch.object(backend_main, "emit_progress",
                               side_effect=lambda *a, **kw: progress_events.append((a, kw))), \
             mock.patch.object(backend_main, "emit_result", side_effect=capture_result):
            backend_main.handle_batch_clips("task-1", params)

        return results_holder, progress_events

    def test_results_preserve_clip_order(self):
        holder, _ = self._run_batch(concurrency=2)
        rows = holder["data"]["results"]
        self.assertEqual([r["clip_index"] for r in rows], [0, 1, 2, 3])
        self.assertEqual(holder["data"]["successful_clips"], 4)

    def test_sequential_path_matches(self):
        holder, _ = self._run_batch(concurrency=1)
        rows = holder["data"]["results"]
        self.assertEqual([r["clip_index"] for r in rows], [0, 1, 2, 3])

    def test_failed_clip_recorded_in_place(self):
        holder, _ = self._run_batch(concurrency=2, fail_index=2)
        rows = holder["data"]["results"]
        self.assertEqual(rows[2]["status"], "error")
        self.assertIn("render exploded", rows[2]["error"])
        self.assertEqual(rows[2]["start_second"], 200.0)
        self.assertEqual(holder["data"]["successful_clips"], 3)

    def test_clip_complete_emitted_for_success_and_failure(self):
        _, events = self._run_batch(concurrency=2, fail_index=1)
        complete = [kw["clip_result"] for a, kw in events if a[1] == "clip_complete"]
        self.assertEqual(len(complete), 4)
        by_index = {r["clip_index"]: r for r in complete}
        self.assertEqual(by_index[1]["status"], "error")
        self.assertEqual(by_index[0]["status"], "success")


if __name__ == "__main__":
    unittest.main()
