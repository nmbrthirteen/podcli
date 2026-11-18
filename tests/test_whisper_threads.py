import os
import subprocess
import sys
import unittest
from unittest import mock

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BACKEND_ROOT = os.path.join(ROOT, "backend")
if BACKEND_ROOT not in sys.path:
    sys.path.insert(0, BACKEND_ROOT)

from services import transcription as tr


def _sysctl(stdout):
    return subprocess.CompletedProcess(args=[], returncode=0, stdout=stdout)


class WhisperThreadsTests(unittest.TestCase):
    def setUp(self):
        self._saved = os.environ.pop("PODCLI_WHISPER_THREADS", None)

    def tearDown(self):
        os.environ.pop("PODCLI_WHISPER_THREADS", None)
        if self._saved is not None:
            os.environ["PODCLI_WHISPER_THREADS"] = self._saved

    def _on(self, platform_name, machine):
        p1 = mock.patch.object(tr.sys, "platform", platform_name)
        p2 = mock.patch.object(tr.platform, "machine", return_value=machine)
        for p in (p1, p2):
            p.start()
            self.addCleanup(p.stop)

    def test_apple_silicon_uses_the_performance_core_count(self):
        self._on("darwin", "arm64")
        with mock.patch.object(tr.subprocess, "run", return_value=_sysctl("10\n")):
            self.assertEqual(tr._whisper_threads(), 10)

    def test_apple_silicon_falls_back_to_cpu_count_when_sysctl_fails(self):
        self._on("darwin", "arm64")
        with mock.patch.object(tr.subprocess, "run", side_effect=OSError("no sysctl")), \
                mock.patch.object(tr.os, "cpu_count", return_value=8):
            self.assertEqual(tr._whisper_threads(), 8)

    def test_apple_silicon_falls_back_to_4_when_nothing_is_known(self):
        self._on("darwin", "arm64")
        with mock.patch.object(tr.subprocess, "run", return_value=_sysctl("")), \
                mock.patch.object(tr.os, "cpu_count", return_value=None):
            self.assertEqual(tr._whisper_threads(), 4)

    def test_other_platforms_keep_4(self):
        self._on("linux", "x86_64")
        self.assertEqual(tr._whisper_threads(), 4)

    def test_env_override_wins(self):
        self._on("darwin", "arm64")
        os.environ["PODCLI_WHISPER_THREADS"] = "3"
        with mock.patch.object(tr.subprocess, "run", return_value=_sysctl("10\n")):
            self.assertEqual(tr._whisper_threads(), 3)


if __name__ == "__main__":
    unittest.main()
