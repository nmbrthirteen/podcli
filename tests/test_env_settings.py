"""env_settings: read/set/unset secrets in the global .env, preserving other lines."""

import os
import sys
import tempfile
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BACKEND_ROOT = os.path.join(ROOT, "backend")
if BACKEND_ROOT not in sys.path:
    sys.path.insert(0, BACKEND_ROOT)

from services import env_settings


class EnvSettingsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.env = os.path.join(self.tmp, ".env")
        self._saved = os.environ.get("PODCLI_ENV_FILE")
        os.environ["PODCLI_ENV_FILE"] = self.env

    def tearDown(self):
        import shutil
        if self._saved is None:
            os.environ.pop("PODCLI_ENV_FILE", None)
        else:
            os.environ["PODCLI_ENV_FILE"] = self._saved
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_list_includes_ai_cli_paths(self):
        keys = [s["key"] for s in env_settings.run_env_action("list")["settings"]]
        self.assertIn("HF_TOKEN", keys)
        self.assertIn("PODCLI_CLAUDE_PATH", keys)
        self.assertIn("PODCLI_CODEX_PATH", keys)

    def test_set_claude_path_persists(self):
        import tempfile
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            cli = tmp.name
        try:
            env_settings.set_setting("PODCLI_CLAUDE_PATH", cli)
            s = next(x for x in env_settings.list_settings() if x["key"] == "PODCLI_CLAUDE_PATH")
            self.assertTrue(s["set"])
            self.assertEqual(s["preview"], cli)
            self.assertIn(f"PODCLI_CLAUDE_PATH={cli}", open(self.env).read())
        finally:
            os.remove(cli)

    def test_set_claude_path_rejects_missing_file(self):
        with self.assertRaises(ValueError):
            env_settings.set_setting("PODCLI_CLAUDE_PATH", "/no/such/claude")

    def test_list_includes_ai_cli_status(self):
        data = env_settings.run_env_action("list")
        self.assertIn("ai_cli", data)
        self.assertIn("available", data["ai_cli"])

    def test_list_unset(self):
        s = env_settings.run_env_action("list")["settings"]
        self.assertEqual(s[0]["key"], "HF_TOKEN")
        self.assertFalse(s[0]["set"])

    def test_set_masks_and_persists(self):
        env_settings.set_setting("HF_TOKEN", "hf_abcd1234567890")
        s = env_settings.list_settings()[0]
        self.assertTrue(s["set"])
        self.assertNotIn("1234567890", s["preview"])  # masked
        self.assertIn("HF_TOKEN=hf_abcd1234567890", open(self.env).read())

    def test_set_preserves_other_lines(self):
        with open(self.env, "w") as f:
            f.write("EXISTING=keep\n# comment\n")
        env_settings.set_setting("HF_TOKEN", "hf_x")
        body = open(self.env).read()
        self.assertIn("EXISTING=keep", body)
        self.assertIn("# comment", body)
        self.assertIn("HF_TOKEN=hf_x", body)

    def test_unset_removes(self):
        env_settings.set_setting("HF_TOKEN", "hf_x")
        env_settings.unset_setting("HF_TOKEN")
        self.assertNotIn("HF_TOKEN", open(self.env).read())

    def test_unknown_key_rejected(self):
        with self.assertRaises(ValueError):
            env_settings.set_setting("BOGUS_KEY", "x")

    def test_mode_is_600(self):
        env_settings.set_setting("HF_TOKEN", "hf_x")
        self.assertEqual(oct(os.stat(self.env).st_mode & 0o777), "0o600")


if __name__ == "__main__":
    unittest.main()
