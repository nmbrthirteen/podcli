"""Tests for backend.services.transcript_parser — format detection and parsing."""

import json
import os
import sys
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BACKEND_ROOT = os.path.join(ROOT, "backend")
if BACKEND_ROOT not in sys.path:
    sys.path.insert(0, BACKEND_ROOT)

from services import transcript_parser as tp


class TimestampParsingTests(unittest.TestCase):
    def test_parse_srt_timestamp_full(self):
        self.assertAlmostEqual(tp.parse_srt_timestamp("01:02:03,500"), 3723.5)

    def test_parse_srt_timestamp_uses_comma_separator(self):
        # SRT uses comma for milliseconds; function should accept it
        self.assertAlmostEqual(tp.parse_srt_timestamp("00:00:10,250"), 10.25)

    def test_parse_srt_timestamp_invalid_returns_zero(self):
        self.assertEqual(tp.parse_srt_timestamp("garbage"), 0.0)

    def test_parse_vtt_timestamp_full(self):
        self.assertAlmostEqual(tp.parse_vtt_timestamp("01:02:03.500"), 3723.5)

    def test_parse_vtt_timestamp_short(self):
        # VTT allows MM:SS.mmm
        self.assertAlmostEqual(tp.parse_vtt_timestamp("02:05.500"), 125.5)

    def test_parse_vtt_timestamp_invalid_returns_zero(self):
        self.assertEqual(tp.parse_vtt_timestamp(""), 0.0)

    def test_parse_timestamp_mm_ss(self):
        self.assertEqual(tp.parse_timestamp("02:30"), 150)

    def test_parse_timestamp_hh_mm_ss(self):
        self.assertEqual(tp.parse_timestamp("01:02:30"), 3750)

    def test_parse_timestamp_invalid_returns_zero(self):
        self.assertEqual(tp.parse_timestamp("not a time"), 0.0)


class DetectAndParseTests(unittest.TestCase):
    def test_detects_vtt(self):
        vtt = "WEBVTT\n\n00:00:00.000 --> 00:00:03.000\nHello world\n"
        result = tp.detect_and_parse(vtt)
        self.assertEqual(result.get("format", "vtt"), "vtt")
        self.assertIn("Hello", result["transcript"])

    def test_detects_srt(self):
        srt = (
            "1\n"
            "00:00:00,000 --> 00:00:03,000\n"
            "Hello world\n"
            "\n"
            "2\n"
            "00:00:03,500 --> 00:00:06,000\n"
            "Second line\n"
        )
        result = tp.detect_and_parse(srt)
        self.assertIn("Hello", result["transcript"])
        self.assertIn("Second", result["transcript"])

    def test_detects_json_word_list(self):
        words = [
            {"word": "hello", "start": 0.0, "end": 0.5},
            {"word": "world", "start": 0.5, "end": 1.0},
        ]
        result = tp.detect_and_parse(json.dumps(words))
        self.assertEqual(result["format"], "json")
        self.assertEqual(len(result["words"]), 2)
        # Duration inferred from last word end
        self.assertAlmostEqual(result["duration"], 1.0)

    def test_detects_json_with_words_key(self):
        payload = {
            "words": [{"word": "foo", "start": 0.0, "end": 0.5}],
            "segments": [],
        }
        result = tp.detect_and_parse(json.dumps(payload))
        self.assertEqual(result["format"], "json")
        self.assertEqual(result["words"][0]["word"], "foo")

    def test_detects_speaker_format_as_fallback(self):
        raw = "Alice (00:00)\nHello there\n\nBob (00:05)\nHi back\n"
        result = tp.detect_and_parse(raw)
        # Speaker format parses to words without a "format" field
        self.assertIn("Hello", result["transcript"])
        self.assertIn("Hi", result["transcript"])
        # Each word should have start/end timestamps
        self.assertTrue(all("start" in w and "end" in w for w in result["words"]))

    def test_malformed_json_falls_through_to_speaker(self):
        # Starts with { but is broken JSON → should fall through
        raw = "{Alice (00:00)\nHello world\n"
        result = tp.detect_and_parse(raw)
        # Should not crash; returns some structure
        self.assertIn("words", result)


class SpeakerTranscriptTests(unittest.TestCase):
    def test_basic_two_speakers(self):
        raw = (
            "Alice (00:00)\n"
            "Hello there\n"
            "\n"
            "Bob (00:05)\n"
            "Hi back\n"
        )
        result = tp.parse_speaker_transcript(raw, total_duration=10.0)
        self.assertGreater(len(result["words"]), 0)
        # First words start near 0
        self.assertLess(result["words"][0]["start"], 1.0)
        # All words within duration
        self.assertLessEqual(result["words"][-1]["end"], 10.0 + 0.01)

    def test_time_adjust_offsets_timestamps(self):
        raw = "Alice (00:10)\nHello world\n"
        base = tp.parse_speaker_transcript(raw, total_duration=30.0)
        adjusted = tp.parse_speaker_transcript(raw, total_duration=30.0, time_adjust=-2.0)
        # Adjusted start = base start - 2 (but clamped to >= 0)
        self.assertAlmostEqual(
            adjusted["words"][0]["start"],
            max(0.0, base["words"][0]["start"] - 2.0),
            places=1,
        )

    def test_hh_mm_ss_headers(self):
        raw = "Alice (01:00:00)\nStart of hour two\n"
        result = tp.parse_speaker_transcript(raw, total_duration=7200.0)
        # First word start near 3600s
        self.assertGreaterEqual(result["words"][0]["start"], 3600.0 - 1.0)

    def test_language_defaults_to_undetermined_not_english(self):
        raw = "Alice (00:00)\nHello\n"
        result = tp.parse_speaker_transcript(raw, total_duration=5.0)
        self.assertEqual(result["language"], "und")

    def test_language_passthrough(self):
        raw = "Alice (00:00)\nHello\n"
        result = tp.parse_speaker_transcript(raw, total_duration=5.0, language="ka")
        self.assertEqual(result["language"], "ka")

    def test_detect_and_parse_threads_language_to_each_format(self):
        speaker_raw = "Alice (00:00)\nHello\n"
        srt_raw = "1\n00:00:00,000 --> 00:00:01,000\nHello\n"
        vtt_raw = "WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nHello\n"
        for raw in (speaker_raw, srt_raw, vtt_raw):
            with self.subTest(raw=raw):
                result = tp.detect_and_parse(raw, total_duration=5.0, language="ka")
                self.assertEqual(result["language"], "ka")

    def test_speaker_segments_apply_time_adjust_like_words_and_segments(self):
        # Regression: speaker_segments previously used the raw block
        # start/end, ignoring time_adjust, while words and segments applied
        # it, so the three arrays drifted out of sync for any non-zero adjust.
        raw = "Alice (00:10)\nHello there\n"
        result = tp.parse_speaker_transcript(raw, total_duration=30.0, time_adjust=-2.0)
        seg = result["speaker_segments"][0]
        self.assertAlmostEqual(seg["start"], result["segments"][0]["start"], places=3)
        self.assertAlmostEqual(seg["end"], result["segments"][0]["end"], places=3)
        self.assertAlmostEqual(seg["start"], 8.0, places=3)


class CaptionMarkupStrippingTests(unittest.TestCase):
    """SRT/VTT override tags like {\\an8} aren't spoken text and must not
    end up imported as literal words in the transcript."""

    def test_parse_srt_strips_ass_override_blocks(self):
        raw = (
            "1\n"
            "00:00:01,000 --> 00:00:04,000\n"
            "{\\an8}Hello this is text\n"
        )
        result = tp.parse_srt(raw)
        self.assertNotIn("error", result)
        self.assertEqual(result["segments"][0]["text"], "Hello this is text")
        self.assertNotIn("an8", result["transcript"])
        self.assertNotIn("{", result["transcript"])

    def test_parse_srt_strips_html_tags(self):
        raw = (
            "1\n"
            "00:00:01,000 --> 00:00:04,000\n"
            "<b>Hello</b> <i>world</i>\n"
        )
        result = tp.parse_srt(raw)
        self.assertEqual(result["segments"][0]["text"], "Hello world")

    def test_parse_srt_strips_override_block_in_trailing_block(self):
        # No trailing blank line, so this exercises the "last block" path.
        raw = (
            "1\n"
            "00:00:01,000 --> 00:00:04,000\n"
            "{\\an8}No trailing blank line"
        )
        result = tp.parse_srt(raw)
        self.assertEqual(result["segments"][0]["text"], "No trailing blank line")

    def test_parse_vtt_strips_ass_override_blocks(self):
        raw = (
            "WEBVTT\n\n"
            "00:00:01.000 --> 00:00:04.000\n"
            "{\\an8}Hello this is text\n"
        )
        result = tp.parse_vtt(raw)
        self.assertNotIn("error", result)
        self.assertEqual(result["segments"][0]["text"], "Hello this is text")

    def test_parse_vtt_strips_override_block_in_trailing_block(self):
        raw = (
            "WEBVTT\n\n"
            "00:00:01.000 --> 00:00:04.000\n"
            "{\\an8}No trailing blank line"
        )
        result = tp.parse_vtt(raw)
        self.assertEqual(result["segments"][0]["text"], "No trailing blank line")


if __name__ == "__main__":
    unittest.main()
