"""Tests for pure helper functions in backend.services.claude_suggest.

These are deterministic, side-effect-free helpers that back the AI
clip-suggestion pipeline — safe to test without mocking subprocess
or any external tools.
"""

import os
import tempfile
import sys
import unittest
from unittest import mock

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



class ReactionAnchorPromptTests(unittest.TestCase):
    def test_empty_or_none_returns_empty(self):
        self.assertEqual(cs._format_reaction_anchors(None), "")
        self.assertEqual(cs._format_reaction_anchors([]), "")

    def test_anchors_are_sorted_deduped_and_formatted(self):
        block = cs._format_reaction_anchors([120.34, 12.31, 12.34])
        self.assertIn("AUDIENCE REACTION ANCHORS", block)
        self.assertIn("12.3s", block)
        self.assertIn("120.3s", block)
        self.assertEqual(block.count("12.3s, 120.3s"), 1)  # deduped and ascending

    def test_anchor_count_is_capped(self):
        import re
        block = cs._format_reaction_anchors([float(t) for t in range(200)])
        self.assertEqual(len(re.findall(r"\d+\.\ds", block)), cs.MAX_REACTION_ANCHORS)


class BlendSignalScoresTests(unittest.TestCase):
    def _clips(self):
        return [
            {"start_second": 0.0, "end_second": 10.0, "score": 16, "reasons": ["hot_take"]},
            {"start_second": 100.0, "end_second": 120.0, "score": 12, "reasons": []},
        ]

    def _events(self):
        return [
            {"time": 5.0, "laughter": 0.5, "cheering": 0.0, "screaming": 0.0, "speech": 0.2},
        ]

    def test_no_signals_is_a_noop(self):
        clips = self._clips()
        out = cs.blend_signal_scores(clips, energy_data=None, events_data=None)
        self.assertEqual(out[0]["score"], 16)
        self.assertNotIn("signal_boost", out[0])

    def test_reaction_peak_boosts_only_covering_clip(self):
        clips = cs.blend_signal_scores(self._clips(), events_data=self._events())
        self.assertGreater(clips[0]["score"], 16)
        self.assertEqual(clips[1]["score"], 12)
        self.assertNotIn("signal_boost", clips[1])

    def test_ai_score_stays_primary(self):
        # Even a maxed-out reaction+energy signal adds at most 3 points.
        clips = [{"start_second": 0.0, "end_second": 10.0, "score": 12}]
        events = [{"time": 5.0, "laughter": 1.0, "cheering": 0.0, "screaming": 0.0, "speech": 0.0}]
        energy = [{"time": float(t), "rms_db": -30.0} for t in range(0, 60)] + [{"time": 5.0, "rms_db": 0.0}]
        out = cs.blend_signal_scores(clips, energy_data=energy, events_data=events)
        self.assertLessEqual(out[0]["score"], 15.0)

    def test_deterministic(self):
        a = cs.blend_signal_scores(self._clips(), events_data=self._events())
        b = cs.blend_signal_scores(self._clips(), events_data=self._events())
        self.assertEqual(a, b)

    def test_order_preserved(self):
        clips = cs.blend_signal_scores(self._clips(), events_data=self._events())
        self.assertEqual([c["start_second"] for c in clips], [0.0, 100.0])

    def test_strong_laugh_tagged_in_reasons(self):
        clips = cs.blend_signal_scores(self._clips(), events_data=self._events())
        self.assertIn("laughter", clips[0]["reasons"])

    def test_crowd_cheer_is_not_tagged_as_laughter(self):
        events = [{"time": 5.0, "laughter": 0.0, "cheering": 0.5, "screaming": 0.0, "speech": 0.2}]
        clips = cs.blend_signal_scores(self._clips(), events_data=events)
        self.assertIn("cheering", clips[0]["reasons"])
        self.assertNotIn("laughter", clips[0]["reasons"])


if __name__ == "__main__":
    unittest.main()


class ExistingShortsTests(unittest.TestCase):
    def _write(self, body):
        path = os.path.join(self.tmp.name, "03-episodes-database.md")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(body)
        return path

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_the_shipped_template_yields_nothing(self):
        shipped = os.path.join(
            ROOT, "backend", "templates", "knowledge", "03-episodes-database.md"
        )
        self.assertEqual(cs._load_existing_shorts(shipped), [])

    def test_titles_come_out_of_the_shipped_table_shape(self):
        path = self._write(
            "| # | Moment (timestamps) | Title | Platform | Status |\n"
            "|---|---------------------|-------|----------|--------|\n"
            "| 1 | [00:14:20-00:15:05] | The pricing mistake | YouTube Shorts | published |\n"
            "| 2 | [00:31:02-00:31:40] | Why we fired our best engineer | TikTok | published |\n"
        )
        self.assertEqual(
            cs._load_existing_shorts(path),
            ["The pricing mistake", "Why we fired our best engineer"],
        )

    def test_the_older_numbered_shape_still_reads(self):
        path = self._write("1. An older entry — hot take\n2. Another one — story\n")
        self.assertEqual(cs._load_existing_shorts(path), ["An older entry", "Another one"])

    def test_a_dropped_moment_stays_available(self):
        path = self._write(
            "| # | Moment | Title | Platform | Status |\n"
            "|---|--------|-------|----------|--------|\n"
            "| 1 | [00:01-00:30] | Shipped one | YouTube Shorts | published |\n"
            "| 2 | [00:40-01:10] | Considered and cut | - | dropped |\n"
            "| 3 | [02:00-02:30] | Queued one | TikTok | scheduled |\n"
        )
        self.assertEqual(cs._load_existing_shorts(path), ["Shipped one", "Queued one"])

    def test_unfilled_cells_are_not_titles(self):
        path = self._write(
            "| # | Moment | Title | Platform | Status |\n"
            "|---|--------|-------|----------|--------|\n"
            "| 1 | [00:00-00:30] | [published title] | [YouTube Shorts] | [published] |\n"
        )
        self.assertEqual(cs._load_existing_shorts(path), [])

    def test_published_titles_reach_the_prompt_one_per_line(self):
        with mock.patch.object(
            cs, "_load_existing_shorts", return_value=["First short", "Second short"]
        ):
            prompt = cs._build_prompt(
                transcript_text="[0.0s] hello",
                segment_count=1,
                duration_min=1.0,
                top_n=3,
            )
        self.assertIn("ALREADY PUBLISHED", prompt)
        self.assertIn("\n- First short\n- Second short", prompt)

    def test_no_published_titles_means_no_heading(self):
        with mock.patch.object(cs, "_load_existing_shorts", return_value=[]):
            prompt = cs._build_prompt(
                transcript_text="[0.0s] hello",
                segment_count=1,
                duration_min=1.0,
                top_n=3,
            )
        self.assertNotIn("ALREADY PUBLISHED", prompt)


class CaptionStyleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def _guide(self, line):
        with open(
            os.path.join(self.tmp.name, "04-shorts-creation-guide.md"), "w", encoding="utf-8"
        ) as fh:
            fh.write("# Guide\n\n" + line + "\n")
        return self.tmp.name

    def test_an_unfilled_placeholder_falls_back_to_the_preset(self):
        kb = self._guide("- Caption style: [branded / hormozi / karaoke / subtle]")
        self.assertEqual(cs._preferred_caption_style(kb), "branded")

    def test_a_chosen_style_wins(self):
        kb = self._guide("- Caption style: karaoke")
        self.assertEqual(cs._preferred_caption_style(kb), "karaoke")

    def test_an_unknown_style_falls_back_rather_than_shipping_garbage(self):
        kb = self._guide("- Caption style: neon")
        self.assertEqual(cs._preferred_caption_style(kb), "branded")

    def test_a_missing_knowledge_base_falls_back(self):
        self.assertEqual(cs._preferred_caption_style("/nonexistent"), "branded")


class ClipBoundsTests(unittest.TestCase):
    def test_vertical_is_the_shorts_window(self):
        b = cs.ClipBounds.of("vertical")
        self.assertEqual((b.dur_min, b.dur_max), (20, 45))
        self.assertIn("SHORTER IS BETTER", b.pacing)

    def test_horizontal_is_minutes_not_seconds(self):
        b = cs.ClipBounds.of("horizontal")
        self.assertEqual((b.dur_min, b.dur_max), (60, 300))
        self.assertEqual((b.target_min, b.target_max), (90, 240))
        self.assertNotIn("SHORTER IS BETTER", b.pacing)

    def test_an_explicit_override_narrows_the_format(self):
        b = cs.ClipBounds.of("horizontal", 90, 120)
        self.assertEqual((b.dur_min, b.dur_max), (90, 120))
        self.assertTrue(b.dur_min <= b.target_min <= b.target_max <= b.dur_max)

    def test_an_inverted_override_falls_back_to_the_format(self):
        b = cs.ClipBounds.of("vertical", 90, 30)
        self.assertEqual((b.dur_min, b.dur_max), (20, 45))

    def test_an_unknown_format_falls_back_rather_than_raising(self):
        b = cs.ClipBounds.of("hexagonal")
        self.assertEqual((b.dur_min, b.dur_max), (20, 45))

    def test_keeps_is_inclusive_at_both_ends(self):
        b = cs.ClipBounds.of("vertical")
        self.assertTrue(b.keeps(20))
        self.assertTrue(b.keeps(45))
        self.assertFalse(b.keeps(19.9))
        self.assertFalse(b.keeps(45.1))

    def test_a_horizontal_length_survives_the_filter_that_used_to_drop_it(self):
        self.assertFalse(cs.ClipBounds.of("vertical").keeps(120))
        self.assertTrue(cs.ClipBounds.of("horizontal").keeps(120))


class FormatFramingTests(unittest.TestCase):
    def test_the_prompt_asks_for_the_format_it_will_render(self):
        prompt = cs._build_prompt(
            transcript_text="[0.0s] hello",
            segment_count=1,
            duration_min=90.0,
            top_n=3,
            bounds=cs.ClipBounds.of("horizontal"),
        )
        self.assertIn("60", prompt)
        self.assertIn("90-240 seconds", prompt)
        self.assertNotIn("SHORTER IS BETTER", prompt)
        self.assertNotIn("TikTok editor", prompt)

    def test_the_vertical_prompt_is_unchanged_in_substance(self):
        prompt = cs._build_prompt(
            transcript_text="[0.0s] hello",
            segment_count=1,
            duration_min=30.0,
            top_n=3,
        )
        self.assertIn("20-35 seconds", prompt)
        self.assertIn("SHORTER IS BETTER", prompt)
        self.assertIn("TikTok", prompt)


class TotalScoreTests(unittest.TestCase):
    """One malformed field used to raise inside the normalization loop and take
    down the whole suggestion run rather than costing a single clip."""

    def test_the_normal_case_still_sums(self):
        clip = {"scores": {"standalone": 4, "hook": 5, "relevance": 4, "quotability": 3}}
        self.assertEqual(cs._total_score(clip), 16)

    def test_a_string_score_does_not_raise(self):
        clip = {"scores": {"standalone": 4, "hook": "5"}}
        self.assertEqual(cs._total_score(clip), 9)

    def test_an_unparseable_score_is_skipped_not_fatal(self):
        clip = {"scores": {"standalone": 4, "hook": "very good"}}
        self.assertEqual(cs._total_score(clip), 4)

    def test_all_scores_unusable_falls_back_to_total_score(self):
        clip = {"scores": {"hook": None}, "total_score": 12}
        self.assertEqual(cs._total_score(clip), 12)

    def test_a_missing_or_junk_total_score_is_zero(self):
        self.assertEqual(cs._total_score({}), 0.0)
        self.assertEqual(cs._total_score({"total_score": "nope"}), 0.0)

    def test_a_non_dict_scores_field_does_not_raise(self):
        self.assertEqual(cs._total_score({"scores": [4, 5], "total_score": 9}), 9)


class CodexTranscriptTests(unittest.TestCase):
    """Codex takes its prompt as an argv argument and silently truncates it.
    Cutting the tail is what clusters every clip in the opening minutes."""

    def _transcript(self, n=4000):
        return "\n".join(f"[{i * 3.0:.1f}s] line {i}" for i in range(n))

    def test_a_short_transcript_is_untouched(self):
        text = self._transcript(10)
        self.assertEqual(cs._sample_transcript(text), text)

    def test_a_long_transcript_is_thinned_not_truncated(self):
        text = self._transcript()
        out = cs._sample_transcript(text)
        self.assertLess(len(out), len(text))
        self.assertIn("line 0", out)
        # The last line is the half a tail truncation would have thrown away.
        self.assertIn(f"line {3999}", out)

    def test_only_codex_is_adapted(self):
        text = self._transcript()
        prompt = "RULES\n\n" + text
        adapt = cs._codex_adapter(text)
        self.assertEqual(adapt("claude", prompt), prompt)
        self.assertNotEqual(adapt("codex", prompt), prompt)

    def test_the_adapted_prompt_keeps_the_rules_and_says_it_sampled(self):
        text = self._transcript()
        prompt = "RULES THAT MUST SURVIVE\n\n" + text
        out = cs._codex_adapter(text)("codex", prompt)
        self.assertIn("RULES THAT MUST SURVIVE", out)
        self.assertIn("sampled across the full episode", out)
        self.assertLess(len(out), len(prompt))
