import inspect
import io
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from unittest import mock

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BACKEND_ROOT = os.path.join(ROOT, "backend")
if BACKEND_ROOT not in sys.path:
    sys.path.insert(0, BACKEND_ROOT)

import presets
import services.transcription as tr
import services.transcript_packer as tp
from services import transcription_whispercpp as wcpp


class DefaultModelTests(unittest.TestCase):
    def test_the_default_preset_uses_small(self):
        self.assertEqual(presets.DEFAULT_PRESET["whisper_model"], "small")

    def test_the_transcription_entry_points_default_to_small(self):
        for fn in (tr.transcribe_file, tr._transcribe_file_inner, tr.resolve_engine_info):
            self.assertEqual(inspect.signature(fn).parameters["model_size"].default, "small", fn.__name__)

    def test_small_keys_the_cache_apart_from_base(self):
        self.assertEqual(tp.cache_key_suffix("whispercpp", "small", None), "-whispercpp-msmall")
        self.assertEqual(tp.cache_key_suffix("whispercpp", "base", None), "-whispercpp")


class WhispercppModelFallbackTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.models = os.path.join(self._tmp.name, "models")
        os.makedirs(self.models)
        self.cli = os.path.join(self._tmp.name, "whisper-cli")
        self._touch(self.cli)
        env = mock.patch.dict(
            os.environ,
            {"PODCLI_HOME": self._tmp.name, "PODCLI_WHISPER_CLI": self.cli},
        )
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("PODCLI_WHISPERCPP_MODEL", None)
        os.environ.pop("PODCLI_ENGINE", None)
        self._had_whisper = sys.modules.get("whisper", "__absent__")
        sys.modules["whisper"] = None
        tr._whisper_py_available.cache_clear()
        self.addCleanup(self._restore_whisper)

    def _restore_whisper(self):
        if self._had_whisper == "__absent__":
            sys.modules.pop("whisper", None)
        else:
            sys.modules["whisper"] = self._had_whisper
        tr._whisper_py_available.cache_clear()

    def _touch(self, path):
        with open(path, "wb") as f:
            f.write(b"x")

    def _install(self, *sizes):
        for size in sizes:
            self._touch(os.path.join(self.models, f"ggml-{size}.bin"))

    def test_small_is_used_when_installed(self):
        self._install("small", "base")
        self.assertEqual(tr.resolve_whispercpp_model_size("small"), "small")

    def test_small_falls_back_to_base_when_only_base_is_installed(self):
        self._install("base")
        self.assertEqual(tr.resolve_whispercpp_model_size("small"), "base")

    def test_small_stays_small_when_nothing_is_installed(self):
        self.assertEqual(tr.resolve_whispercpp_model_size("small"), "small")

    def test_other_sizes_never_fall_back(self):
        self._install("base")
        self.assertEqual(tr.resolve_whispercpp_model_size("medium"), "medium")
        self.assertEqual(tr.resolve_whispercpp_model_size("tiny"), "tiny")

    def test_an_explicit_model_path_disables_the_fallback(self):
        self._install("base")
        explicit = os.path.join(self._tmp.name, "custom.bin")
        self._touch(explicit)
        os.environ["PODCLI_WHISPERCPP_MODEL"] = explicit
        self.assertEqual(tr.resolve_whispercpp_model_size("small"), "small")

    def test_the_cache_key_prediction_names_the_model_the_run_uses(self):
        self._install("base")
        info = tr.resolve_engine_info(None, "small")
        self.assertEqual(info, {"engine": "whispercpp", "model_size": "base"})
        self.assertEqual(
            tp.cache_key_suffix(info["engine"], info["model_size"], None), "-whispercpp"
        )

    def test_the_prediction_keeps_small_once_it_is_installed(self):
        self._install("base", "small")
        info = tr.resolve_engine_info(None, "small")
        self.assertEqual(info, {"engine": "whispercpp", "model_size": "small"})
        self.assertEqual(
            tp.cache_key_suffix(info["engine"], info["model_size"], None), "-whispercpp-msmall"
        )

    def test_an_explicit_whispercpp_request_resolves_the_model_too(self):
        self._install("base")
        info = tr.resolve_engine_info("whispercpp", "small")
        self.assertEqual(info, {"engine": "whispercpp", "model_size": "base"})

    def test_other_engines_report_the_requested_model_untouched(self):
        self._install("base")
        self.assertEqual(
            tr.resolve_engine_info("assemblyai", "small"),
            {"engine": "assemblyai", "model_size": "small"},
        )

    def test_the_run_uses_base_and_prints_one_line_about_it(self):
        self._install("base")
        seen = {}

        def fake_transcribe(file_path, **kwargs):
            seen.update(kwargs)
            return {"words": []}

        buf = io.StringIO()
        with mock.patch.object(wcpp, "transcribe_file", side_effect=fake_transcribe), redirect_stderr(buf):
            tr._transcribe_with_whispercpp("episode.wav", "small", None, None)

        self.assertEqual(os.path.basename(seen["model_path"]), "ggml-base.bin")
        lines = [line for line in buf.getvalue().splitlines() if line.strip()]
        self.assertEqual(len(lines), 1)
        self.assertIn("'small' is not installed, using 'base'", lines[0])

    def test_the_run_is_silent_when_small_is_installed(self):
        self._install("small", "base")
        seen = {}

        def fake_transcribe(file_path, **kwargs):
            seen.update(kwargs)
            return {"words": []}

        buf = io.StringIO()
        with mock.patch.object(wcpp, "transcribe_file", side_effect=fake_transcribe), redirect_stderr(buf):
            tr._transcribe_with_whispercpp("episode.wav", "small", None, None)

        self.assertEqual(os.path.basename(seen["model_path"]), "ggml-small.bin")
        self.assertEqual(buf.getvalue(), "")

    def test_the_transcript_records_the_model_that_actually_ran(self):
        self._install("base")
        with tempfile.NamedTemporaryFile(suffix=".wav") as media, \
                mock.patch.object(tr, "_transcribe_with_whispercpp", return_value={}), \
                mock.patch.object(tr, "_attach_speakers_and_faces", side_effect=lambda fp, base, *a, **k: base):
            result = tr.transcribe_file(media.name, engine="whispercpp", enable_diarization=False)

        self.assertEqual(result["engine"], "whispercpp")
        self.assertEqual(result["model_size"], "base")


if __name__ == "__main__":
    unittest.main()
