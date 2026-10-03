"""Tests for the performance-learnings digest: median aggregation and the
small-bucket suppression that keeps a single viral or flop clip from reading
as a trend."""

import os
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BACKEND_ROOT = os.path.join(ROOT, "backend")
if BACKEND_ROOT not in sys.path:
    sys.path.insert(0, BACKEND_ROOT)

from services.integrations.youtube import learnings


def _clip(content_type, retention, views=1000, ctr=5.0, caption_style="hormozi", duration=30):
    return {
        "title": f"clip-{content_type}-{retention}",
        "content_type": content_type,
        "caption_style": caption_style,
        "duration": duration,
        "metrics": {"retention": retention, "ctr": ctr, "views": views},
    }


class AggTests(unittest.TestCase):
    def test_uses_median_not_mean(self):
        # One outlier (90) would drag a mean way up; the median should stay
        # anchored to the typical clip.
        clips = [_clip("story", r) for r in [20, 22, 21, 90]]
        out = learnings._agg(clips, lambda c: c["content_type"])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["ret"], 21.5)
        self.assertEqual(out[0]["n"], 4)

    def test_suppresses_buckets_under_minimum_size(self):
        clips = [_clip("story", 20), _clip("story", 22), _clip("story", 24)]
        out = learnings._agg(clips, lambda c: c["content_type"])
        self.assertEqual(out, [])

    def test_keeps_bucket_at_minimum_size(self):
        clips = [_clip("story", r) for r in [10, 20, 30, 40]]
        out = learnings._agg(clips, lambda c: c["content_type"])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["n"], 4)


class WriteLearningsTests(unittest.TestCase):
    def test_small_bucket_is_dropped_from_output(self):
        # "video" has only 3 clips (below MIN_BUCKET_SIZE) and must not show
        # up in the rendered "By moment type" section.
        clips = [_clip("story", r) for r in [10, 20, 30, 40]] + [
            _clip("video", r) for r in [60, 70, 80]
        ]
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(learnings, "load_clips_history", return_value=clips), mock.patch.object(
                learnings, "paths", {"knowledge": tmp}
            ):
                path = learnings.write_learnings(min_clips=3)
            self.assertIsNotNone(path)
            with open(path, encoding="utf-8") as f:
                content = f.read()
            self.assertIn("story", content)
            self.assertNotIn("video:", content)


if __name__ == "__main__":
    unittest.main()
