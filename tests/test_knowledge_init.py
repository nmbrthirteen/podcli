import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

TEMPLATE_DIR = os.path.join(ROOT, "backend", "templates", "knowledge")
EXPECTED = [f"{i:02d}" for i in range(14)]


def run_init(home):
    env = dict(os.environ, PODCLI_HOME=home)
    return subprocess.run(
        [sys.executable, os.path.join(ROOT, "backend", "cli.py"), "knowledge", "init"],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )


class KnowledgeInitTests(unittest.TestCase):
    def test_templates_ship_all_fourteen_files(self):
        names = sorted(os.listdir(TEMPLATE_DIR))
        self.assertEqual([n[:2] for n in names], EXPECTED)

    def test_init_creates_templates_and_never_overwrites(self):
        with tempfile.TemporaryDirectory() as home:
            result = run_init(home)
            self.assertEqual(result.returncode, 0, result.stderr)
            kb_dir = os.path.join(home, "knowledge")
            created = sorted(os.listdir(kb_dir))
            self.assertEqual(len(created), 14)

            marker = os.path.join(kb_dir, "01-brand-identity.md")
            with open(marker, "w", encoding="utf-8") as f:
                f.write("user content")
            os.remove(os.path.join(kb_dir, "13-learnings.md"))

            result = run_init(home)
            self.assertEqual(result.returncode, 0, result.stderr)
            with open(marker, encoding="utf-8") as f:
                self.assertEqual(f.read(), "user content")
            self.assertIn("1 created, 13 kept", result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
