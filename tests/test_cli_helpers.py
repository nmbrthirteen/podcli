"""Tests for backend.cli helpers: the shared WAV cache and Studio port resolution."""

import argparse
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

import cli as cli_mod
from services import audio_extract


class SharedWavTests(unittest.TestCase):
    def test_successful_extraction_is_reused(self):
        fd, wav_path = tempfile.mkstemp(suffix=".wav")
        os.close(fd)
        try:
            with mock.patch.object(
                audio_extract, "extract_wav_16k_mono", return_value=wav_path
            ) as extract:
                shared = cli_mod._SharedWav("/video.mp4")
                self.assertEqual(shared.get(), wav_path)
                self.assertEqual(shared.get(), wav_path)
            self.assertEqual(extract.call_count, 1)
        finally:
            if os.path.exists(wav_path):
                os.unlink(wav_path)

    def test_failed_extraction_is_attempted_once(self):
        with mock.patch.object(
            audio_extract, "extract_wav_16k_mono", side_effect=RuntimeError("decode failed")
        ) as extract:
            shared = cli_mod._SharedWav("/video.mp4")
            for _ in range(4):
                self.assertIsNone(shared.get())

        self.assertEqual(extract.call_count, 1)
        self.assertIsInstance(shared.error, RuntimeError)

    def test_empty_result_is_treated_as_failure(self):
        with mock.patch.object(
            audio_extract, "extract_wav_16k_mono", return_value=None
        ) as extract:
            shared = cli_mod._SharedWav("/video.mp4")
            self.assertIsNone(shared.get())
            self.assertIsNone(shared.get())

        self.assertEqual(extract.call_count, 1)


if __name__ == "__main__":
    unittest.main()


class SelectionSignatureTests(unittest.TestCase):
    """The suggestions cache is only allowed to replay picks made under the
    same rules."""

    def _sig(self, config, kb="kb-a"):
        with mock.patch("services.claude_suggest.kb_signature", return_value=kb):
            return cli_mod._selection_signature(config)

    def test_editing_the_knowledge_base_invalidates_an_ai_session(self):
        cfg = {"ai_select": True}
        self.assertNotEqual(self._sig(cfg, kb="kb-a"), self._sig(cfg, kb="kb-b"))

    def test_editing_the_knowledge_base_leaves_a_saliency_session_alone(self):
        cfg = {"ai_select": False}
        self.assertEqual(self._sig(cfg, kb="kb-a"), self._sig(cfg, kb="kb-b"))

    def test_changing_profile_invalidates(self):
        a = self._sig({"ai_select": True, "profile": "party"})
        b = self._sig({"ai_select": True})
        self.assertNotEqual(a, b)

    def test_turning_energy_off_invalidates(self):
        a = self._sig({"ai_select": True, "energy_boost": False})
        b = self._sig({"ai_select": True, "energy_boost": True})
        self.assertNotEqual(a, b)

    def test_an_unreadable_knowledge_base_does_not_raise(self):
        with mock.patch("services.claude_suggest.kb_signature", side_effect=OSError("boom")):
            self.assertIn("kb-unavailable", cli_mod._selection_signature({"ai_select": True}))

    def test_two_failed_lookups_never_compare_equal(self):
        # Otherwise a session saved during one failure replays during the next,
        # after the knowledge base has changed underneath it.
        with mock.patch("services.claude_suggest.kb_signature", side_effect=OSError("boom")):
            a = cli_mod._selection_signature({"ai_select": True})
            b = cli_mod._selection_signature({"ai_select": True})
        self.assertNotEqual(a, b)


class ExplicitClipBoundsTests(unittest.TestCase):
    """config is seeded from DEFAULT_PRESET, which carries the vertical numbers.
    Forwarding those unconditionally would make --format horizontal a no-op."""

    def test_untouched_preset_values_do_not_override_the_format(self):
        from presets import DEFAULT_PRESET

        config = {**DEFAULT_PRESET, "format": "horizontal"}
        self.assertEqual(cli_mod._explicit_clip_bounds(config), (None, None))

    def test_a_value_the_user_changed_is_forwarded(self):
        from presets import DEFAULT_PRESET

        config = {**DEFAULT_PRESET, "min_clip_duration": 30}
        self.assertEqual(cli_mod._explicit_clip_bounds(config), (30, None))

    def test_horizontal_selection_uses_the_format_window(self):
        from presets import DEFAULT_PRESET
        from services.claude_suggest import ClipBounds

        config = {**DEFAULT_PRESET, "format": "horizontal"}
        bounds = ClipBounds.of(config["format"], *cli_mod._explicit_clip_bounds(config))
        self.assertEqual((bounds.dur_min, bounds.dur_max), (60, 300))


class JsonObjectArgTests(unittest.TestCase):
    """`--style` is validated up front: malformed input stops the run rather
    than rendering with a theme nobody asked for."""

    def test_missing_value_is_none(self):
        self.assertIsNone(cli_mod._json_object_arg(None, "--style"))

    def test_a_valid_object_passes_through(self):
        parsed = cli_mod._json_object_arg('{"pack": "collage"}', "--style")
        self.assertEqual(parsed, {"pack": "collage"})

    def test_malformed_json_exits(self):
        with self.assertRaises(SystemExit):
            cli_mod._json_object_arg("not json", "--style")

    def test_a_json_array_is_not_an_object(self):
        with self.assertRaises(SystemExit):
            cli_mod._json_object_arg("[1, 2]", "--style")


class AgentOutputHelpersTests(unittest.TestCase):
    def test_no_color_disables_color(self):
        with mock.patch.dict(os.environ, {"NO_COLOR": "1"}, clear=False):
            self.assertFalse(cli_mod._use_color())

    def test_parse_content_text(self):
        raw = (
            "TITLES\n1. First title\n2. Second title\n"
            "DESCRIPTION\nA short blurb about the clip.\n"
            "TAGS\nalpha, beta\n"
            "HASHTAGS\n#one #two\n"
        )
        parsed = cli_mod._parse_content_text(raw)
        self.assertEqual(parsed["titles"], ["First title", "Second title"])
        self.assertIn("blurb", parsed["description"])
        self.assertEqual(parsed["tags"], ["alpha", "beta"])
        self.assertEqual(parsed["hashtags"], ["#one", "#two"])

    def test_want_spinner_off_when_quiet(self):
        with mock.patch.dict(os.environ, {"PODCLI_QUIET": "1"}, clear=False):
            self.assertFalse(cli_mod._want_spinner())

    def test_non_interactive_yes_flag(self):
        ns = argparse.Namespace(yes=True, json=False)
        self.assertTrue(cli_mod._non_interactive(ns))

    def test_emit_process_json_writes_one_object(self):
        import io

        buf = io.StringIO()
        cli_mod._emit_process_json(
            json_out=buf,
            video_path="/ep.mp4",
            output_dir="/out",
            quality="high",
            clips=[{"title": "A", "start_second": 1, "end_second": 12, "score": 9}],
            results=[{"output_path": "/out/a.mp4", "duration": 11, "status": "ok"}],
            interrupted=False,
            transcript={"engine": "whispercpp", "model": "base"},
        )
        payload = json.loads(buf.getvalue())
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["transcript"], {"engine": "whispercpp", "model": "base"})
        self.assertEqual(payload["quality"], "high")
        self.assertEqual(payload["clips"][0]["title"], "A")
        self.assertEqual(payload["clips"][0]["path"], "/out/a.mp4")


class DiarizationNoticeTests(unittest.TestCase):
    TOKEN_WARNING = (
        "HF_TOKEN not set, so speakers are not detected. Fix: podcli env set HF_TOKEN hf_...\n"
        "\n"
        "  Setup (one-time):\n"
        "  1. Create a token at https://huggingface.co/settings/tokens"
    )

    def _printed(self, result, no_speakers=False, env=None):
        import io
        from contextlib import redirect_stdout

        buf = io.StringIO()
        with mock.patch.dict(os.environ, env or {}), redirect_stdout(buf):
            if not env or "NO_COLOR" not in env:
                os.environ.pop("NO_COLOR", None)
            cli_mod._print_diarization_notice(result, no_speakers)
        return buf.getvalue()

    def test_a_failure_warning_prints_on_its_own_line_with_the_fix(self):
        out = self._printed({"engine": "whisper-py", "diarization_warning": self.TOKEN_WARNING})
        lines = out.splitlines()
        self.assertEqual(lines[0].strip(), "\u26a0 HF_TOKEN not set, so speakers are not detected. Fix: podcli env set HF_TOKEN hf_...")
        self.assertIn("Setup (one-time):", out)
        self.assertNotIn("\n\n", out)

    def test_the_warning_is_yellow_when_color_is_on(self):
        out = self._printed(
            {"engine": "whisper-py", "diarization_warning": "Speaker detection failed: boom"},
            env={"FORCE_COLOR": "1"},
        )
        self.assertTrue(out.startswith("\033[38;2;250;204;21m"))
        self.assertIn("Speaker detection failed: boom", out)
        self.assertTrue(out.rstrip("\n").endswith("\033[0m"))

    def test_no_color_prints_plain_text(self):
        out = self._printed(
            {"engine": "whisper-py", "diarization_warning": "Speaker detection failed: boom"},
            env={"NO_COLOR": "1", "FORCE_COLOR": "1"},
        )
        self.assertNotIn("\033", out)
        self.assertIn("Speaker detection failed: boom", out)

    def test_whispercpp_prints_one_line_about_the_engine_and_face_tracking(self):
        out = self._printed({"engine": "whispercpp", "diarization_warning": "Speaker detection disabled"})
        self.assertEqual(len(out.splitlines()), 1)
        self.assertIn("whisper-py", out)
        self.assertIn("HF_TOKEN", out)
        self.assertIn("face tracking", out)

    def test_whispercpp_stays_quiet_when_the_user_passed_no_speakers(self):
        out = self._printed({"engine": "whispercpp", "diarization_warning": "Speaker detection disabled"}, no_speakers=True)
        self.assertEqual(out, "")

    def test_a_failure_stays_quiet_when_the_user_passed_no_speakers(self):
        out = self._printed({"engine": "whisper-py", "diarization_warning": "Speaker detection failed: boom"}, no_speakers=True)
        self.assertEqual(out, "")

    def test_the_plain_disabled_marker_is_not_a_warning(self):
        self.assertEqual(self._printed({"engine": "assemblyai", "diarization_warning": "Speaker detection disabled"}), "")

    def test_no_warning_prints_nothing(self):
        self.assertEqual(self._printed({"engine": "whisper-py"}), "")


class CropStrategySummaryTests(unittest.TestCase):
    def _summary(self, results, requested="face", env=None):
        with mock.patch.dict(os.environ, env or {}):
            if not env or "NO_COLOR" not in env:
                os.environ.pop("NO_COLOR", None)
            return cli_mod._crop_strategy_summary(results, requested)

    def test_counts_each_strategy_most_used_first(self):
        results = [
            {"crop_strategy_used": "speaker"},
            {"crop_strategy_used": "center"},
            {"crop_strategy_used": "speaker"},
            {"crop_strategy_used": "face"},
            {"crop_strategy_used": "speaker"},
        ]
        self.assertEqual(self._summary(results), "Crop used: 3 speaker, 1 center, 1 face")

    def test_failed_and_unreported_clips_are_skipped(self):
        results = [{"status": "error", "error": "boom"}, {"output_path": "a.mp4"}, {"crop_strategy_used": "face"}]
        self.assertEqual(self._summary(results), "Crop used: 1 face")

    def test_nothing_reported_means_no_line(self):
        self.assertIsNone(self._summary([{"status": "error"}]))
        self.assertIsNone(self._summary([]))

    def test_center_fallback_is_yellow_when_another_strategy_was_asked(self):
        line = self._summary([{"crop_strategy_used": "center"}], requested="face", env={"FORCE_COLOR": "1"})
        self.assertTrue(line.startswith("\033[38;2;250;204;21m"))

    def test_center_is_plain_when_center_was_asked(self):
        line = self._summary([{"crop_strategy_used": "center"}], requested="center", env={"FORCE_COLOR": "1"})
        self.assertEqual(line, "Crop used: 1 center")
