import os
import sys

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BACKEND_ROOT = os.path.join(ROOT, "backend")
if BACKEND_ROOT not in sys.path:
    sys.path.insert(0, BACKEND_ROOT)

from services.silence_removal import (
    _map_range,
    plan_silence_removal,
    probabilities_to_speech_segments,
    remap_timed_items,
    remap_transcript,
)


def test_plan_preserves_short_pauses_and_removes_long_ones():
    plan = plan_silence_removal(
        12.0,
        [{"start": 1.0, "end": 3.0}, {"start": 3.4, "end": 5.0}, {"start": 7.0, "end": 10.0}],
        [],
        min_silence_seconds=0.65,
        padding_seconds=0.1,
    )

    assert plan["cut_count"] == 3
    assert plan["removed_ranges"] == [
        {"start": 0.0, "end": 0.9},
        {"start": 5.1, "end": 6.9},
        {"start": 10.1, "end": 12.0},
    ]
    # 400 ms pause between the first two speech ranges stays intact.
    assert plan["keep_segments"][0] == {"start": 0.9, "end": 5.1}


def test_transcript_words_protect_speech_missed_by_vad():
    plan = plan_silence_removal(
        6.0,
        [],
        [{"word": "quiet", "start": 2.0, "end": 2.5}],
        min_silence_seconds=0.5,
        padding_seconds=0.1,
    )

    assert plan["keep_segments"] == [{"start": 1.9, "end": 2.6}]
    assert plan["cut_count"] == 2


def test_remap_transcript_closes_removed_gaps():
    transcript = {
        "words": [
            {"word": "one", "start": 1.0, "end": 1.4},
            {"word": "two", "start": 5.0, "end": 5.4},
        ],
        "segments": [{"text": "one two", "start": 1.0, "end": 5.4}],
    }
    remapped = remap_transcript(
        transcript,
        [{"start": 0.5, "end": 2.0}, {"start": 4.5, "end": 6.0}],
    )

    assert remapped["words"][0]["start"] == 0.5
    assert remapped["words"][1]["start"] == 2.0
    assert remapped["segments"][0] == {"text": "one two", "start": 0.5, "end": 2.4}
    assert remapped["duration"] == 3.0


def test_zero_duration_word_inside_a_kept_range_is_kept_as_a_point():
    keep_segments = [{"start": 0.5, "end": 2.0}, {"start": 4.5, "end": 6.0}]
    words = [{"word": "", "start": 1.2, "end": 1.2}]

    remapped = remap_timed_items(words, keep_segments, assign_by_midpoint=True)

    assert len(remapped) == 1
    assert remapped[0]["start"] == remapped[0]["end"] == 0.7


def test_short_whole_word_inside_a_kept_range_survives_float_rounding():
    # 10.50 - 10.49 is 0.00999... in floating point, under the 10ms floor,
    # but the word kept its whole length, so no cut clipped it.
    keep_segments = [{"start": 0.0, "end": 44.66}, {"start": 45.04, "end": 60.0}]
    words = [{"word": "in", "start": 10.49, "end": 10.5}, {"word": "me", "start": 50.57, "end": 50.58}]

    remapped = remap_timed_items(words, keep_segments, assign_by_midpoint=True)

    assert [w["word"] for w in remapped] == ["in", "me"]


def test_word_clipped_to_a_sliver_by_a_cut_is_dropped():
    keep_segments = [{"start": 0.0, "end": 1.005}, {"start": 3.0, "end": 4.0}]
    words = [{"word": "gone", "start": 1.0, "end": 1.5}]

    assert remap_timed_items(words, keep_segments) == []


def test_zero_duration_word_in_a_removed_range_is_dropped():
    keep_segments = [{"start": 0.5, "end": 2.0}, {"start": 4.5, "end": 6.0}]
    words = [{"word": "", "start": 3.0, "end": 3.0}]

    assert remap_timed_items(words, keep_segments, assign_by_midpoint=True) == []


def test_word_straddling_a_removed_gap_with_no_owning_side_is_dropped():
    # Word runs 1.8-2.2 across a removed gap from 1.9-4.0. Its midpoint (2.0)
    # falls inside that gap, so neither side owns it; it's dropped rather
    # than stitched across the cut.
    keep_segments = [{"start": 0.0, "end": 1.9}, {"start": 4.0, "end": 6.0}]
    mapped = _map_range(1.8, 2.2, keep_segments, assign_by_midpoint=True)
    assert mapped is None


def test_word_straddling_a_cut_is_not_stitched_across_it():
    keep_segments = [{"start": 0.0, "end": 2.0}, {"start": 2.0, "end": 4.0}]
    # Midpoint 2.05 belongs to the second segment; the old stitching behavior
    # would have spanned from the first segment's overlap through the
    # second's, inflating the word's output duration across the cut.
    mapped = _map_range(1.9, 2.2, keep_segments, assign_by_midpoint=True)
    assert mapped == (2.0, 2.2)


def test_segments_still_stitch_across_a_removed_gap():
    # Sentence-level segments intentionally keep the old stitching behavior
    # (see test_remap_transcript_closes_removed_gaps), only words are
    # reassigned by midpoint.
    keep_segments = [{"start": 0.5, "end": 2.0}, {"start": 4.5, "end": 6.0}]
    mapped = _map_range(1.0, 5.4, keep_segments)
    assert mapped == pytest.approx((0.5, 2.4))


def test_probability_hysteresis_ignores_short_noise():
    probabilities = [0.0] * 5 + [0.8] * 12 + [0.0] * 8 + [0.9] * 2 + [0.0] * 8
    speech = probabilities_to_speech_segments(
        probabilities,
        len(probabilities) * 512,
        min_speech_ms=250,
        min_silence_ms=100,
    )

    assert len(speech) == 1
    assert speech[0]["end"] > speech[0]["start"]
