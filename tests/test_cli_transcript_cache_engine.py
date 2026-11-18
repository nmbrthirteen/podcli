import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BACKEND_ROOT = os.path.join(ROOT, "backend")
if BACKEND_ROOT not in sys.path:
    sys.path.insert(0, BACKEND_ROOT)

import cli
from services import transcript_packer as tp
from services import transcription


class NativeInstallCacheRoundTripTests(unittest.TestCase):
    def setUp(self):
        self._saved_engine = os.environ.pop("PODCLI_ENGINE", None)
        self._tmp = tempfile.mkdtemp()
        self._orig_dir = tp._transcripts_cache_dir
        tp._transcripts_cache_dir = lambda: os.path.join(self._tmp, "transcripts")
        self.video = os.path.join(self._tmp, "episode.mp4")
        with open(self.video, "wb") as f:
            f.write(b"not really a video" * 64)
        patches = [
            mock.patch.object(transcription, "_whisper_py_available", return_value=False),
            mock.patch.object(transcription, "_whispercpp_ready", return_value=True),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def tearDown(self):
        tp._transcripts_cache_dir = self._orig_dir
        shutil.rmtree(self._tmp, ignore_errors=True)
        os.environ.pop("PODCLI_ENGINE", None)
        if self._saved_engine is not None:
            os.environ["PODCLI_ENGINE"] = self._saved_engine

    def _transcribed(self):
        return {"engine": "whispercpp", "words": [{"word": "hi", "start": 0.0, "end": 0.2}], "segments": [{"text": "hi"}]}

    def test_second_run_hits_the_whispercpp_transcript_the_first_run_saved(self):
        for model in ("base", "small"):
            with self.subTest(model=model):
                tp.save_cached_transcript_for_video(self.video, self._transcribed(), engine="whispercpp", model=model, language=None)
                hit = tp.load_cached_transcript_for_video(
                    self.video, engine=cli._transcript_cache_engine(model), model=model, language=None
                )
                self.assertIsNotNone(hit)

    def test_engine_agnostic_lookup_finds_the_whispercpp_transcript(self):
        tp.save_cached_transcript_for_video(self.video, self._transcribed(), engine="whispercpp", model="base")
        self.assertEqual(cli._cached_transcript(self.video).get("engine"), "whispercpp")

    def test_explicit_engine_is_kept(self):
        os.environ["PODCLI_ENGINE"] = "assemblyai"
        self.assertEqual(cli._transcript_cache_engine("base"), "assemblyai")


if __name__ == "__main__":
    unittest.main()
