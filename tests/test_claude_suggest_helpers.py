"""Tests for pure helper functions in backend.services.claude_suggest.

These are deterministic, side-effect-free helpers that back the AI
clip-suggestion pipeline — safe to test without mocking subprocess
or any external tools.
"""

import os
import sys
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BACKEND_ROOT = os.path.join(ROOT, "backend")
if BACKEND_ROOT not in sys.path:
    sys.path.insert(0, BACKEND_ROOT)

from services import claude_suggest as cs


class EngineLabelTests(unittest.TestCase):
    def test_engine_label_known(self):
        self.assertEqual(cs._engine_label("claude"), "Claude")
        self.assertEqual(cs._engine_label("codex"), "Codex")

    def test_engine_label_unknown_returns_fallback(self):
        self.assertEqual(cs._engine_label("mystery"), "AI")


class FormatTimeoutLabelTests(unittest.TestCase):
    def test_seconds_for_sub_minute(self):
        self.assertEqual(cs._format_timeout_label(45), "45s")

    def test_single_minute(self):
        self.assertEqual(cs._format_timeout_label(60), "1 minute")

    def test_multiple_minutes(self):
        self.assertEqual(cs._format_timeout_label(180), "3 minutes")

    def test_non_round_minute_reports_seconds(self):
        self.assertEqual(cs._format_timeout_label(90), "90s")


class BuildTranscriptTextTests(unittest.TestCase):
    def test_empty_segments(self):
        self.assertEqual(cs._build_transcript_text([]), "")

    def test_segments_with_speakers(self):
        segments = [
            {"start": 0.0, "speaker": "Alice", "text": "Hello"},
            {"start": 3.5, "speaker": "Bob", "text": "Hi there"},
        ]
        out = cs._build_transcript_text(segments)
        self.assertIn("[0.0s]", out)
        self.assertIn("[3.5s]", out)
        self.assertIn("[Alice]", out)
        self.assertIn("[Bob]", out)

    def test_segments_without_speakers(self):
        segments = [{"start": 1.5, "text": "Unknown speaker"}]
        out = cs._build_transcript_text(segments)
        self.assertIn("[1.5s]", out)
        self.assertIn("Unknown speaker", out)
        # No speaker label when not provided
        self.assertNotIn("[]", out)

    def test_skips_empty_text(self):
        segments = [
            {"start": 0.0, "text": "kept"},
            {"start": 2.0, "text": "   "},  # whitespace only
            {"start": 4.0, "text": "also kept"},
        ]
        out = cs._build_transcript_text(segments)
        self.assertIn("kept", out)
        self.assertIn("also kept", out)
        self.assertEqual(len(out.split("\n")), 2)


class SegmentsDurationTests(unittest.TestCase):
    def test_empty_returns_zero(self):
        self.assertEqual(cs._segments_duration_seconds([]), 0.0)

    def test_basic_duration(self):
        segments = [{"start": 0, "end": 30}, {"start": 30, "end": 120}]
        self.assertAlmostEqual(cs._segments_duration_seconds(segments), 120.0)

    def test_uses_start_when_end_missing(self):
        segments = [{"start": 5}, {"start": 100}]
        self.assertAlmostEqual(cs._segments_duration_seconds(segments), 95.0)


class ShouldBucketTests(unittest.TestCase):
    def test_short_episode_does_not_bucket(self):
        segments = [{"start": 0, "end": 10, "text": "short"}]
        self.assertFalse(cs._should_bucket_initial_selection(segments))

    def test_long_duration_buckets(self):
        # 50 minutes — over the 45 min threshold
        segments = [{"start": 0, "end": 3000, "text": "x" * 100}]
        self.assertTrue(cs._should_bucket_initial_selection(segments))

    def test_many_segments_buckets(self):
        segments = [{"start": i, "end": i + 1, "text": "hi"} for i in range(200)]
        self.assertTrue(cs._should_bucket_initial_selection(segments))

    def test_dense_text_buckets(self):
        segments = [{"start": 0, "end": 60, "text": "x" * 20000}]
        self.assertTrue(cs._should_bucket_initial_selection(segments))

    def test_empty_does_not_bucket(self):
        self.assertFalse(cs._should_bucket_initial_selection([]))


class DedupeClipsTests(unittest.TestCase):
    def test_dedupes_identical_ranges(self):
        clips = [
            {"start_second": 10, "end_second": 20, "title": "A"},
            {"start_second": 10, "end_second": 20, "title": "B"},
        ]
        out = cs._dedupe_clips_by_range(clips)
        self.assertEqual(len(out), 1)

    def test_preserves_distinct_ranges(self):
        clips = [
            {"start_second": 10, "end_second": 20, "title": "A"},
            {"start_second": 30, "end_second": 40, "title": "B"},
        ]
        self.assertEqual(len(cs._dedupe_clips_by_range(clips)), 2)

    def test_rounds_to_one_decimal(self):
        clips = [
            {"start_second": 10.04, "end_second": 20.01},
            {"start_second": 10.03, "end_second": 20.00},
        ]
        # Both round to (10.0, 20.0) → one duplicate
        self.assertEqual(len(cs._dedupe_clips_by_range(clips)), 1)

    def test_returns_sorted_by_start(self):
        clips = [
            {"start_second": 30, "end_second": 40},
            {"start_second": 10, "end_second": 20},
            {"start_second": 20, "end_second": 30},
        ]
        out = cs._dedupe_clips_by_range(clips)
        starts = [c["start_second"] for c in out]
        self.assertEqual(starts, sorted(starts))


class BucketCoverageTests(unittest.TestCase):
    def test_no_overlap(self):
        clips = [{"start_second": 100, "end_second": 120}]
        self.assertEqual(cs._bucket_coverage_seconds(clips, 0, 50), 0.0)

    def test_full_overlap(self):
        clips = [{"start_second": 10, "end_second": 30}]
        self.assertEqual(cs._bucket_coverage_seconds(clips, 0, 50), 20.0)

    def test_partial_overlap(self):
        clips = [{"start_second": 40, "end_second": 80}]
        # Bucket 0-50 overlaps 40-50 → 10s
        self.assertEqual(cs._bucket_coverage_seconds(clips, 0, 50), 10.0)

    def test_multiple_clips_sum(self):
        clips = [
            {"start_second": 10, "end_second": 20},  # 10s
            {"start_second": 30, "end_second": 45},  # 15s
        ]
        self.assertEqual(cs._bucket_coverage_seconds(clips, 0, 50), 25.0)


class SliceSegmentsTests(unittest.TestCase):
    def test_includes_segments_in_range(self):
        segments = [
            {"start": 0, "end": 10},
            {"start": 20, "end": 30},
            {"start": 40, "end": 50},
        ]
        out = cs._slice_segments_for_range(segments, 15, 35)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["start"], 20)

    def test_includes_partial_overlap(self):
        segments = [{"start": 10, "end": 30}]
        out = cs._slice_segments_for_range(segments, 20, 40)
        self.assertEqual(len(out), 1)

    def test_excludes_out_of_range(self):
        segments = [{"start": 100, "end": 120}]
        self.assertEqual(cs._slice_segments_for_range(segments, 0, 50), [])


if __name__ == "__main__":
    unittest.main()
