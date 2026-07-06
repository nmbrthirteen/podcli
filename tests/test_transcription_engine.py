"""Engine selection: native installs auto-use whisper.cpp when openai-whisper
is absent, unless the user explicitly asked for the whisper-py engine."""

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
        self._orig_ready = tr._whispercpp_ready
        tr._transcribe_with_whispercpp = lambda *a, **k: {"engine": "whispercpp"}
        tr._transcribe_with_assemblyai = lambda *a, **k: {"engine": "assemblyai"}
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


if __name__ == "__main__":
    unittest.main()
