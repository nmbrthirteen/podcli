"""Engine selection: native installs auto-use whisper.cpp when openai-whisper
is absent, unless the user explicitly asked for the whisper-py engine."""

import importlib.util
import os
import sys
import tempfile
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BACKEND_ROOT = os.path.join(ROOT, "backend")
if BACKEND_ROOT not in sys.path:
    sys.path.insert(0, BACKEND_ROOT)

import services.transcription as tr


class TranscriptionEngineTests(unittest.TestCase):
    def setUp(self):
        self._orig_wcpp = tr._transcribe_with_whispercpp
        self._orig_aai = tr._transcribe_with_assemblyai
        self._orig_omni = tr._transcribe_with_omnilingual
        self._orig_ready = tr._whispercpp_ready
        tr._transcribe_with_whispercpp = lambda *a, **k: {"engine": "whispercpp"}
        tr._transcribe_with_assemblyai = lambda *a, **k: {"engine": "assemblyai"}
        tr._transcribe_with_omnilingual = lambda *a, **k: {"engine": "omnilingual"}
        tr._whispercpp_ready = lambda size: True
        # Make `import whisper` fail to simulate a native (hermetic) install.
        self._had_whisper = sys.modules.get("whisper", "__absent__")
        sys.modules["whisper"] = None
        self._tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        self._tmp.write(b"x")
        self._tmp.close()
        self._saved_engine = os.environ.pop("PODCLI_ENGINE", None)

    def tearDown(self):
        tr._transcribe_with_whispercpp = self._orig_wcpp
        tr._transcribe_with_assemblyai = self._orig_aai
        tr._transcribe_with_omnilingual = self._orig_omni
        tr._whispercpp_ready = self._orig_ready
        if self._had_whisper == "__absent__":
            sys.modules.pop("whisper", None)
        else:
            sys.modules["whisper"] = self._had_whisper
        os.unlink(self._tmp.name)
        if self._saved_engine is None:
            os.environ.pop("PODCLI_ENGINE", None)
        else:
            os.environ["PODCLI_ENGINE"] = self._saved_engine

    def test_auto_falls_back_to_whispercpp(self):
        os.environ.pop("PODCLI_ENGINE", None)
        result = tr.transcribe_file(self._tmp.name, model_size="base", enable_diarization=False)
        self.assertEqual(result["engine"], "whispercpp")

    def test_explicit_whispercpp_uses_it(self):
        os.environ["PODCLI_ENGINE"] = "whispercpp"
        result = tr.transcribe_file(self._tmp.name, model_size="base", enable_diarization=False)
        self.assertEqual(result["engine"], "whispercpp")

    def test_explicit_assemblyai_uses_it(self):
        os.environ["PODCLI_ENGINE"] = "assemblyai"
        result = tr.transcribe_file(self._tmp.name, model_size="base", enable_diarization=False)
        self.assertEqual(result["engine"], "assemblyai")

    def test_explicit_omnilingual_uses_it(self):
        os.environ["PODCLI_ENGINE"] = "omnilingual"
        result = tr.transcribe_file(self._tmp.name, model_size="base", enable_diarization=False)
        self.assertEqual(result["engine"], "omnilingual")

    def test_omnilingual_skips_diarization_like_whispercpp(self):
        os.environ["PODCLI_ENGINE"] = "omnilingual"
        result = tr.transcribe_file(self._tmp.name, model_size="base")
        self.assertEqual(result["engine"], "omnilingual")
        self.assertEqual(result.get("diarization_warning"), "Speaker detection disabled")

    def test_unset_engine_never_auto_falls_back_to_omnilingual(self):
        # Only whispercpp is an automatic fallback for an unset engine on a
        # native install; omnilingual is explicit-only.
        os.environ.pop("PODCLI_ENGINE", None)
        result = tr.transcribe_file(self._tmp.name, model_size="base", enable_diarization=False)
        self.assertEqual(result["engine"], "whispercpp")

    def test_whispercpp_skips_diarization_by_default(self):
        # Regression: the cpp path must not attempt torch-backed diarization even
        # with the default enable_diarization=True — importing a broken torch in a
        # native runtime hard-crashes the process. Face analysis still runs.
        os.environ["PODCLI_ENGINE"] = "whispercpp"
        result = tr.transcribe_file(self._tmp.name, model_size="base")
        self.assertEqual(result["engine"], "whispercpp")
        self.assertEqual(result.get("diarization_warning"), "Speaker detection disabled")

    def test_explicit_whisper_py_still_errors(self):
        os.environ["PODCLI_ENGINE"] = "whisper-py"
        with self.assertRaises(RuntimeError):
            tr.transcribe_file(self._tmp.name, model_size="base", enable_diarization=False)

    def test_no_fallback_when_whispercpp_unavailable(self):
        os.environ.pop("PODCLI_ENGINE", None)
        tr._whispercpp_ready = lambda size: False
        with self.assertRaises(RuntimeError):
            tr.transcribe_file(self._tmp.name, model_size="base", enable_diarization=False)

    def test_whispercpp_never_marks_diarization_attempted(self):
        # whisper.cpp can never diarize. A cache entry from this engine must
        # say so explicitly, or a reader asking for speaker labels would
        # re-transcribe it forever (every run looks like a retriable miss).
        os.environ["PODCLI_ENGINE"] = "whispercpp"
        result = tr.transcribe_file(self._tmp.name, model_size="base", enable_diarization=True)
        self.assertFalse(result["diarization_attempted"])

    def test_omnilingual_never_marks_diarization_attempted(self):
        os.environ["PODCLI_ENGINE"] = "omnilingual"
        result = tr.transcribe_file(self._tmp.name, model_size="base", enable_diarization=True)
        self.assertFalse(result["diarization_attempted"])

    def test_assemblyai_marks_diarization_attempted_from_its_own_request_flag(self):
        os.environ["PODCLI_ENGINE"] = "assemblyai"
        result = tr.transcribe_file(self._tmp.name, model_size="base", enable_diarization=True)
        self.assertTrue(result["diarization_attempted"])

    def test_assemblyai_without_diarization_is_not_marked_attempted(self):
        os.environ["PODCLI_ENGINE"] = "assemblyai"
        result = tr.transcribe_file(self._tmp.name, model_size="base", enable_diarization=False)
        self.assertFalse(result["diarization_attempted"])


class SampleModeTests(unittest.TestCase):
    """start_seconds/duration_seconds should transcribe a trimmed window
    instead of the full file, mark the result incomplete, and skip
    diarization/face analysis (the trimmed clip has no video track for face
    analysis to read, and a throwaway sample isn't worth either pass)."""

    def setUp(self):
        self._orig_wcpp = tr._transcribe_with_whispercpp
        self._orig_ready = tr._whispercpp_ready
        self._saved_engine = os.environ.pop("PODCLI_ENGINE", None)
        os.environ["PODCLI_ENGINE"] = "whispercpp"
        self._tmp = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False)
        self._tmp.write(b"not a real video")
        self._tmp.close()
        self.extract_calls = []

        def fake_extract(file_path, wav_path=None, timeout=1800, start_seconds=None, duration_seconds=None):
            self.extract_calls.append(
                {"file_path": file_path, "start_seconds": start_seconds, "duration_seconds": duration_seconds}
            )
            fd, path = tempfile.mkstemp(suffix=".wav")
            os.close(fd)
            return path

        self._fake_extract = fake_extract
        import services.audio_extract as audio_extract

        self._orig_extract = audio_extract.extract_wav_16k_mono
        audio_extract.extract_wav_16k_mono = fake_extract

        def fake_wcpp(*args, **kwargs):
            # The inner call must receive the sample wav as both file_path
            # and wav_path, and diarization must be forced off.
            self.inner_file_path = args[0] if args else kwargs.get("file_path")
            self.inner_wav_path = kwargs.get("wav_path")
            return {"engine": "whispercpp", "words": [], "segments": [], "duration": 40.0}

        tr._transcribe_with_whispercpp = fake_wcpp
        tr._whispercpp_ready = lambda size: True

    def tearDown(self):
        tr._transcribe_with_whispercpp = self._orig_wcpp
        tr._whispercpp_ready = self._orig_ready
        import services.audio_extract as audio_extract

        audio_extract.extract_wav_16k_mono = self._orig_extract
        os.unlink(self._tmp.name)
        if self._saved_engine is None:
            os.environ.pop("PODCLI_ENGINE", None)
        else:
            os.environ["PODCLI_ENGINE"] = self._saved_engine

    def test_sample_window_is_passed_to_extraction(self):
        tr.transcribe_file(
            self._tmp.name, model_size="base", start_seconds=120.0, duration_seconds=40.0,
            enable_diarization=False,
        )
        self.assertEqual(len(self.extract_calls), 1)
        self.assertEqual(self.extract_calls[0]["start_seconds"], 120.0)
        self.assertEqual(self.extract_calls[0]["duration_seconds"], 40.0)

    def test_result_marked_incomplete_with_offset(self):
        result = tr.transcribe_file(
            self._tmp.name, model_size="base", start_seconds=120.0, duration_seconds=40.0,
            enable_diarization=False,
        )
        self.assertFalse(result["complete"])
        self.assertEqual(result["sample_offset_seconds"], 120.0)

    def test_full_run_is_marked_neither_incomplete_nor_offset(self):
        result = tr.transcribe_file(self._tmp.name, model_size="base", enable_diarization=False)
        self.assertNotIn("complete", result)
        self.assertNotIn("sample_offset_seconds", result)

    def test_default_start_is_zero_when_only_duration_given(self):
        result = tr.transcribe_file(
            self._tmp.name, model_size="base", duration_seconds=40.0, enable_diarization=False,
        )
        self.assertEqual(self.extract_calls[0]["start_seconds"], 0.0)
        self.assertEqual(result["sample_offset_seconds"], 0.0)

    def test_duration_seconds_zero_is_a_full_run_not_a_sample(self):
        # Matches src/handlers/transcribe.handler.ts and web-server.ts: a
        # sample is a *positive* window, not merely a present key.
        result = tr.transcribe_file(
            self._tmp.name, model_size="base", duration_seconds=0.0, enable_diarization=False,
        )
        self.assertEqual(self.extract_calls, [])
        self.assertNotIn("complete", result)
        self.assertNotIn("sample_offset_seconds", result)

    def test_start_seconds_zero_alone_is_a_full_run_not_a_sample(self):
        result = tr.transcribe_file(
            self._tmp.name, model_size="base", start_seconds=0.0, enable_diarization=False,
        )
        self.assertEqual(self.extract_calls, [])
        self.assertNotIn("complete", result)


class ResolveEngineInfoTests(unittest.TestCase):
    """resolve_engine_info predicts transcribe_file's engine choice so a
    caller can build a matching cache key before deciding to transcribe."""

    def setUp(self):
        self._had_whisper = sys.modules.get("whisper", "__absent__")
        self._orig_ready = tr._whispercpp_ready
        self._saved_engine = os.environ.pop("PODCLI_ENGINE", None)
        # _whisper_py_available is memoized for the process lifetime (the
        # package's installed-ness can't change mid-run), but that means
        # a value cached by one test would leak into the next, which
        # sabotages sys.modules["whisper"] to simulate availability.
        tr._whisper_py_available.cache_clear()

    def tearDown(self):
        if self._had_whisper == "__absent__":
            sys.modules.pop("whisper", None)
        else:
            sys.modules["whisper"] = self._had_whisper
        tr._whispercpp_ready = self._orig_ready
        tr._whisper_py_available.cache_clear()
        if self._saved_engine is None:
            os.environ.pop("PODCLI_ENGINE", None)
        else:
            os.environ["PODCLI_ENGINE"] = self._saved_engine

    def test_explicit_whispercpp_resolves_as_is(self):
        self.assertEqual(tr.resolve_engine_info("whispercpp")["engine"], "whispercpp")

    def test_explicit_assemblyai_resolves_as_is(self):
        self.assertEqual(tr.resolve_engine_info("assemblyai")["engine"], "assemblyai")

    def test_unset_falls_back_to_whispercpp_on_native_install(self):
        sys.modules["whisper"] = None  # simulate "import whisper" failing
        tr._whispercpp_ready = lambda size: True
        self.assertEqual(tr.resolve_engine_info(None)["engine"], "whispercpp")

    def test_unset_stays_whisper_py_when_whisper_importable(self):
        sys.modules.pop("whisper", None)
        try:
            import whisper  # noqa: F401
        except Exception:
            self.skipTest("openai-whisper not installed in this environment")
        self.assertEqual(tr.resolve_engine_info(None)["engine"], "whisper-py")

    def test_explicit_whisper_py_request_never_falls_back(self):
        # Fallback only applies to an unset request; an explicit whisper-py
        # ask should resolve as whisper-py even on a native install, matching
        # transcribe_file which raises instead of silently substituting.
        sys.modules["whisper"] = None
        tr._whispercpp_ready = lambda size: True
        self.assertEqual(tr.resolve_engine_info("whisper-py")["engine"], "whisper-py")


class OmnilingualReceiptKeyTests(unittest.TestCase):
    """The resumable run directory has to change whenever a resumed
    window's receipt would no longer be valid: a different model file, or
    a change to the window/context constants its timestamps assume."""

    def setUp(self):
        self._model = tempfile.NamedTemporaryFile(suffix=".onnx", delete=False)
        self._model.write(b"fake model bytes")
        self._model.close()
        self._tokens = tempfile.NamedTemporaryFile(suffix=".txt", delete=False)
        self._tokens.write(b"fake tokens")
        self._tokens.close()
        self._media = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False)
        self._media.write(b"fake media")
        self._media.close()

        self._orig_model = tr._omnilingual_model
        self._orig_tokens = tr._omnilingual_tokens
        tr._omnilingual_model = lambda: self._model.name
        tr._omnilingual_tokens = lambda: self._tokens.name

        self.captured = []
        import services.transcription_omnilingual as omni

        self._orig_omni_transcribe = omni.transcribe_file

        def fake_transcribe(*a, run_dir=None, **k):
            self.captured.append(run_dir)
            return {"transcript": "", "segments": [], "words": [], "duration": 0.0, "language": "und"}

        omni.transcribe_file = fake_transcribe
        self._omni = omni

    def tearDown(self):
        tr._omnilingual_model = self._orig_model
        tr._omnilingual_tokens = self._orig_tokens
        self._omni.transcribe_file = self._orig_omni_transcribe
        for f in (self._model, self._tokens, self._media):
            os.unlink(f.name)

    def test_a_different_model_file_gets_its_own_run_dir(self):
        tr._transcribe_with_omnilingual(self._media.name, progress_callback=None)
        first = self.captured[0]

        # Swap in a "different" model: same path, new content/mtime.
        import time as _time
        _time.sleep(0.01)
        with open(self._model.name, "wb") as f:
            f.write(b"a completely different and larger fake model")

        tr._transcribe_with_omnilingual(self._media.name, progress_callback=None)
        second = self.captured[1]

        self.assertNotEqual(first, second)

    def test_the_window_and_context_constants_are_baked_into_the_key(self):
        tr._transcribe_with_omnilingual(self._media.name, progress_callback=None)
        run_dir = self.captured[0]
        self.assertIn(f"w{self._omni.WINDOW_SECONDS}", run_dir)
        self.assertIn(f"c{self._omni.CONTEXT_SECONDS}", run_dir)


class WhisperPyFallbackSharedPathTests(unittest.TestCase):
    """resolve_engine_info (a cheap cache-key prediction) and transcribe_file
    (about to actually transcribe) make the same fallback decision through
    one shared function, _resolve_whisper_py_fallback, differing only in
    whether they load the model weights to check it."""

    def setUp(self):
        self._orig_wcpp = tr._transcribe_with_whispercpp
        self._orig_ready = tr._whispercpp_ready
        tr._transcribe_with_whispercpp = lambda *a, **k: {"engine": "whispercpp"}
        tr._whispercpp_ready = lambda size: True
        self._saved_engine = os.environ.pop("PODCLI_ENGINE", None)
        self._tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        self._tmp.write(b"x")
        self._tmp.close()

        # Simulate "import whisper" succeeding but whisper.load_model()
        # failing: the one gap resolve_engine_info's cheap check can't see.
        import importlib.machinery

        class _BrokenWhisperModule:
            # find_spec requires a real module to carry __spec__. A bare
            # object stand-in raises ValueError instead of just finding it.
            __spec__ = importlib.machinery.ModuleSpec("whisper", loader=None)

            @staticmethod
            def load_model(size):
                raise RuntimeError("corrupt model weights")

        self._had_whisper = sys.modules.get("whisper", "__absent__")
        sys.modules["whisper"] = _BrokenWhisperModule()
        tr._whisper_py_available.cache_clear()

    def tearDown(self):
        tr._transcribe_with_whispercpp = self._orig_wcpp
        tr._whispercpp_ready = self._orig_ready
        if self._had_whisper == "__absent__":
            sys.modules.pop("whisper", None)
        else:
            sys.modules["whisper"] = self._had_whisper
        tr._whisper_py_available.cache_clear()
        os.unlink(self._tmp.name)
        if self._saved_engine is None:
            os.environ.pop("PODCLI_ENGINE", None)
        else:
            os.environ["PODCLI_ENGINE"] = self._saved_engine

    def test_transcribe_file_falls_back_to_whispercpp_on_a_load_model_failure(self):
        result = tr.transcribe_file(self._tmp.name, model_size="base", enable_diarization=False)
        self.assertEqual(result["engine"], "whispercpp")

    def test_resolve_engine_info_cannot_see_this_failure_by_design(self):
        # Documents the known, acceptable gap: resolve_engine_info only
        # checks the import, so it still predicts whisper-py here even
        # though transcribe_file will fall back, a one-time cache miss,
        # not a wrong cache write (that's keyed by what actually ran).
        self.assertEqual(tr.resolve_engine_info(None, "base")["engine"], "whisper-py")


class WhisperPyAvailableMemoizationTests(unittest.TestCase):
    """resolve_engine_info runs on every transcribe request. The
    availability check behind it has to find_spec (not import, which would
    pull in torch) and compute that only once per process."""

    def setUp(self):
        tr._whisper_py_available.cache_clear()
        self._orig_find_spec = importlib.util.find_spec
        self.calls = 0

        def counting_find_spec(name, *a, **k):
            self.calls += 1
            return self._orig_find_spec(name, *a, **k)

        importlib.util.find_spec = counting_find_spec

    def tearDown(self):
        importlib.util.find_spec = self._orig_find_spec
        tr._whisper_py_available.cache_clear()

    def test_find_spec_runs_at_most_once_across_repeated_calls(self):
        for _ in range(5):
            tr._whisper_py_available()
        self.assertEqual(self.calls, 1)


class WhisperCppModelAliasTests(unittest.TestCase):
    """"large" alone doesn't name a real ggml file upstream (v1/v2/v3/v3-turbo
    are separate downloads); provisioning always fetches large-v3, so the
    model path lookup must resolve the same alias."""

    def setUp(self):
        self._saved = os.environ.pop("PODCLI_WHISPERCPP_MODEL", None)

    def tearDown(self):
        if self._saved is not None:
            os.environ["PODCLI_WHISPERCPP_MODEL"] = self._saved

    def test_large_resolves_to_large_v3(self):
        self.assertTrue(tr._whispercpp_model("large").endswith("ggml-large-v3.bin"))

    def test_medium_is_unaliased(self):
        self.assertTrue(tr._whispercpp_model("medium").endswith("ggml-medium.bin"))


if __name__ == "__main__":
    unittest.main()
