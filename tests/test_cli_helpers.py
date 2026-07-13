"""Tests for backend.cli helpers: the shared WAV cache and Studio port resolution."""

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


class WebuiPortTests(unittest.TestCase):
    def _port(self, env):
        with mock.patch.dict(os.environ, env, clear=True):
            return cli_mod._webui_port()

    def test_defaults_to_3847(self):
        self.assertEqual(self._port({}), 3847)

    def test_port_is_honored(self):
        self.assertEqual(self._port({"PORT": "4100"}), 4100)

    def test_podcli_port_wins_over_port(self):
        self.assertEqual(self._port({"PODCLI_PORT": "4000", "PORT": "4100"}), 4000)

    def test_invalid_values_fall_back_to_default(self):
        self.assertEqual(self._port({"PODCLI_PORT": "abc"}), 3847)
        self.assertEqual(self._port({"PODCLI_PORT": "0"}), 3847)
        self.assertEqual(self._port({"PODCLI_PORT": "70000"}), 3847)


if __name__ == "__main__":
    unittest.main()
