import csv
import importlib.util
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
spec = importlib.util.spec_from_file_location("bench_clips", os.path.join(ROOT, "scripts", "bench_clips.py"))
bench = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bench)



class StageTimesTests(unittest.TestCase):
    def test_each_stage_runs_until_the_next_one_starts(self):
        marks = [("1", 0.0), ("2", 30.0), ("3", 35.0), ("4", 95.0)]
        self.assertEqual(
            bench.stage_times(marks, 215.0),
            {"transcribe": 30.0, "audio": 5.0, "select": 60.0, "render": 120.0},
        )

    def test_a_skipped_stage_reports_zero(self):
        self.assertEqual(
            bench.stage_times([("1", 0.0), ("3", 2.0), ("4", 12.0)], 20.0),
            {"transcribe": 2.0, "audio": 0.0, "select": 10.0, "render": 8.0},
        )


class MainWritesCsvTests(unittest.TestCase):
    def test_writes_one_clip_row_and_one_blank_score_row_per_clip(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        video = os.path.join(tmp, "ep.mp4")
        with open(video, "wb") as f:
            f.write(b"x")
        payload = {
            "transcript": {"engine": "whispercpp", "model": "small"},
            "clips": [
                {"index": 1, "title": "Why we quit", "start_second": 61.2, "end_second": 98.4, "duration": 37.2,
                 "crop_strategy": "speaker", "path": "/out/1.mp4", "status": "ok"},
                {"index": 2, "title": "Broken", "status": "error", "error": "ffmpeg died"},
            ],
        }
        times = {"transcribe": 40.0, "audio": 3.0, "select": 50.0, "render": 20.0}
        with mock.patch.object(bench, "run_format", return_value=(payload, times, 113.0)), \
                mock.patch.object(sys, "argv", ["bench_clips.py", video, tmp, "--formats", "topic"]):
            self.assertEqual(bench.main(), 0)
        with open(os.path.join(tmp, "clips.csv"), encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        self.assertEqual(rows, [{
            "clip_id": "topic-1", "format": "topic", "start": "61.2", "end": "98.4", "duration": "37.2",
            "title": "Why we quit", "crop_strategy": "speaker", "engine": "whispercpp", "model": "small",
            "transcribe_s": "40.0", "audio_s": "3.0", "select_s": "50.0", "render_s": "20.0", "total_s": "113.0",
        }])
        with open(os.path.join(tmp, "scores.csv"), encoding="utf-8") as f:
            scores = list(csv.DictReader(f))
        self.assertEqual(scores[0]["clip_id"], "topic-1")
        self.assertEqual(scores[0]["file"], "/out/1.mp4")
        self.assertEqual(scores[0]["post_as_is"], "")
        self.assertEqual(len(scores), 1)

    def test_exits_with_failure_when_no_clip_rendered(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        video = os.path.join(tmp, "ep.mp4")
        with open(video, "wb") as f:
            f.write(b"x")
        payload = {"clips": [{"index": 1, "status": "error", "error": "ffmpeg died"}]}
        times = {"transcribe": 1.0, "audio": 1.0, "select": 1.0, "render": 1.0}
        with mock.patch.object(bench, "run_format", return_value=(payload, times, 4.0)), \
                mock.patch.object(sys, "argv", ["bench_clips.py", video, tmp, "--formats", "vertical"]):
            self.assertEqual(bench.main(), 1)


if __name__ == "__main__":
    unittest.main()
