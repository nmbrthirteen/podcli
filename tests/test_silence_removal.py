import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BACKEND_ROOT = os.path.join(ROOT, "backend")
if BACKEND_ROOT not in sys.path:
    sys.path.insert(0, BACKEND_ROOT)

from services.silence_removal import (
    plan_silence_removal,
    probabilities_to_speech_segments,
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
