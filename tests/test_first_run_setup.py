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
from services import knowledge_base as kb

ANSWERS = {
    "show_name": "Deep Tech Weekly",
    "hosts": "Nika, Sandro",
    "audience": "Founders building hard tech",
    "language": "English",
    "format": "Interview, 60 min, weekly",
    "voice": "blunt, curious, technical",
}


class RenderTests(unittest.TestCase):
    def test_brand_identity_carries_every_answer(self):
        out = cli_mod._render_brand_identity(ANSWERS)
        self.assertIn("- Name: Deep Tech Weekly", out)
        self.assertIn("- Nika", out)
        self.assertIn("- Sandro", out)
        self.assertIn("Founders building hard tech", out)
        self.assertIn("- Language: English", out)
        self.assertIn("- Format: Interview, 60 min, weekly", out)

    def test_voice_file_carries_voice_and_hosts(self):
        out = cli_mod._render_voice_and_tone(ANSWERS)
        self.assertIn("blunt, curious, technical", out)
        self.assertIn("Nika and Sandro", out)
        self.assertIn("Banned words", out)

    def test_missing_hosts_do_not_break_rendering(self):
        answers = dict(ANSWERS, hosts="")
        self.assertIn("Deep Tech Weekly", cli_mod._render_brand_identity(answers))
        self.assertIn("a host", cli_mod._render_voice_and_tone(answers))


class GatingTests(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.TemporaryDirectory()
        self.addCleanup(self.home.cleanup)
        patcher = mock.patch.dict(
            cli_mod.paths,
            {"home": self.home.name, "knowledge": os.path.join(self.home.name, "knowledge")},
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        env = mock.patch.dict(os.environ, {}, clear=False)
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("PODCLI_NO_ONBOARDING", None)

    def test_needs_onboarding_on_a_fresh_tty(self):
        with mock.patch.object(sys.stdin, "isatty", return_value=True), \
                mock.patch.object(sys.stdout, "isatty", return_value=True):
            self.assertTrue(cli_mod._needs_onboarding())

    def test_non_tty_never_onboards(self):
        with mock.patch.object(sys.stdin, "isatty", return_value=False), \
                mock.patch.object(sys.stdout, "isatty", return_value=True):
            self.assertFalse(cli_mod._needs_onboarding())

    def test_marker_stops_the_wizard(self):
        cli_mod._mark_onboarded()
        self.assertTrue(os.path.exists(os.path.join(self.home.name, ".onboarded")))
        with mock.patch.object(sys.stdin, "isatty", return_value=True), \
                mock.patch.object(sys.stdout, "isatty", return_value=True):
            self.assertFalse(cli_mod._needs_onboarding())

    def test_env_opt_out(self):
        with mock.patch.dict(os.environ, {"PODCLI_NO_ONBOARDING": "1"}), \
                mock.patch.object(sys.stdin, "isatty", return_value=True), \
                mock.patch.object(sys.stdout, "isatty", return_value=True):
            self.assertFalse(cli_mod._needs_onboarding())

    def test_existing_knowledge_base_skips_the_wizard_and_marks_it_done(self):
        kb_dir = os.path.join(self.home.name, "knowledge")
        os.makedirs(kb_dir)
        with open(os.path.join(kb_dir, "01-brand-identity.md"), "w", encoding="utf-8") as f:
            f.write("# Brand identity\n\n- Name: Deep Tech Weekly\n")

        with mock.patch.object(cli_mod, "cmd_knowledge") as init:
            cli_mod._first_run_setup()

        init.assert_not_called()
        self.assertTrue(os.path.exists(os.path.join(self.home.name, ".onboarded")))

    def test_readme_only_knowledge_base_still_runs_the_wizard(self):
        kb_dir = os.path.join(self.home.name, "knowledge")
        os.makedirs(kb_dir)
        with open(os.path.join(kb_dir, "README.md"), "w", encoding="utf-8") as f:
            f.write("# Knowledge base\n")
        self.assertTrue(kb.is_empty(kb_dir))


def _answering(confirm, answers, choice):
    """Drive questionary through a wizard run without a terminal."""
    replies = iter(answers)
    return [
        mock.patch("questionary.confirm", return_value=mock.Mock(ask=lambda: confirm)),
        mock.patch("questionary.text", side_effect=lambda *a, **k: mock.Mock(ask=lambda: next(replies))),
        mock.patch("questionary.select", return_value=mock.Mock(ask=lambda: choice)),
    ]


class WizardRunTests(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.TemporaryDirectory()
        self.addCleanup(self.home.cleanup)
        self.kb_dir = os.path.join(self.home.name, "knowledge")
        patcher = mock.patch.dict(
            cli_mod.paths, {"home": self.home.name, "knowledge": self.kb_dir}
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def _run(self, confirm=True, answers=None, choice="later"):
        answers = answers if answers is not None else [
            ANSWERS["show_name"], ANSWERS["hosts"], ANSWERS["audience"],
            ANSWERS["language"], ANSWERS["format"], ANSWERS["voice"],
        ]
        patches = _answering(confirm, answers, choice)
        for p in patches:
            p.start()
        try:
            cli_mod._first_run_setup()
        finally:
            for p in patches:
                p.stop()

    def test_wizard_writes_answers_and_the_remaining_templates(self):
        self._run()

        created = sorted(os.listdir(self.kb_dir))
        self.assertEqual(len(created), 14)

        with open(os.path.join(self.kb_dir, "01-brand-identity.md"), encoding="utf-8") as f:
            brand = f.read()
        self.assertIn("Deep Tech Weekly", brand)
        self.assertFalse(kb.is_unfilled_template(brand, "01-brand-identity.md"))

        context = kb.load_kb_context([("01-brand-identity.md", 4000)], self.kb_dir)
        self.assertIn("Deep Tech Weekly", context)
        self.assertTrue(os.path.exists(os.path.join(self.home.name, ".onboarded")))

    def test_declining_marks_it_done_and_writes_nothing(self):
        self._run(confirm=False)

        self.assertFalse(os.path.isdir(self.kb_dir))
        self.assertTrue(os.path.exists(os.path.join(self.home.name, ".onboarded")))

    def test_empty_show_name_writes_nothing(self):
        self._run(answers=["", "", "", "", "", ""])

        self.assertFalse(os.path.isdir(self.kb_dir))
        self.assertTrue(os.path.exists(os.path.join(self.home.name, ".onboarded")))


if __name__ == "__main__":
    unittest.main()
