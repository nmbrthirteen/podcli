import os
import sys
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BACKEND_ROOT = os.path.join(ROOT, "backend")
if BACKEND_ROOT not in sys.path:
    sys.path.insert(0, BACKEND_ROOT)

from services import transcript_packer as tp


class EngineNamespacedCacheTests(unittest.TestCase):
    def setUp(self):
        self._saved = os.environ.get("PODCLI_ENGINE")

    def tearDown(self):
        if self._saved is None:
            os.environ.pop("PODCLI_ENGINE", None)
        else:
            os.environ["PODCLI_ENGINE"] = self._saved

    def test_whisper_py_keeps_bare_path(self):
        for v in (None, "whisper-py", "WHISPER-PY"):
            if v is None:
                os.environ.pop("PODCLI_ENGINE", None)
            else:
                os.environ["PODCLI_ENGINE"] = v
            self.assertTrue(tp.transcript_json_path("abc123").endswith("abc123.json"))

    def test_whispercpp_is_namespaced(self):
        for v in ("whispercpp", "whisper-cpp", "whisper.cpp", "cpp"):
            os.environ["PODCLI_ENGINE"] = v
            self.assertTrue(
                tp.transcript_json_path("abc123").endswith("abc123-whispercpp.json"),
                f"engine {v!r} not namespaced",
            )

    def test_assemblyai_is_namespaced(self):
        for v in ("assemblyai", "assembly-ai", "aai"):
            os.environ["PODCLI_ENGINE"] = v
            self.assertTrue(
                tp.transcript_json_path("abc123").endswith("abc123-assemblyai.json"),
                f"engine {v!r} not namespaced",
            )

    def test_engines_do_not_collide(self):
        os.environ.pop("PODCLI_ENGINE", None)
        py = tp.transcript_json_path("abc123")
        os.environ["PODCLI_ENGINE"] = "whispercpp"
        cpp = tp.transcript_json_path("abc123")
        os.environ["PODCLI_ENGINE"] = "assemblyai"
        aai = tp.transcript_json_path("abc123")
        self.assertNotEqual(py, cpp)
        self.assertNotEqual(py, aai)
        self.assertNotEqual(cpp, aai)


if __name__ == "__main__":
    unittest.main()
