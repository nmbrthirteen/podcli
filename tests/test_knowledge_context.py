import io
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BACKEND_ROOT = os.path.join(ROOT, "backend")
if BACKEND_ROOT not in sys.path:
    sys.path.insert(0, BACKEND_ROOT)

from services import knowledge_base as kb

TEMPLATE_DIR = os.path.join(BACKEND_ROOT, "templates", "knowledge")

# Every file the clip-scoring and title/description prompts inline.
PROMPT_TEMPLATES = [
    "00-master-instructions.md",
    "01-brand-identity.md",
    "02-voice-and-tone.md",
    "04-shorts-creation-guide.md",
    "05-title-formulas.md",
    "06-descriptions-template.md",
    "07-thumbnail-guide.md",
    "08-topics-themes.md",
    "11-inspiration-channels.md",
    "12-quick-reference.md",
]

FILLED_BRAND_IDENTITY = """# Brand identity

## Show

- Name: Deep Tech Weekly
- One-line positioning: For founders building hard tech, the messy parts nobody posts about.
- Format: Interview, 60 minutes, weekly on Thursday.
- Language: English, some Georgian slang from the hosts.

## Hosts

| Host | Role | Voice in one line |
|------|------|-------------------|
| Nika | Host | Asks the blunt question everyone else skips |
| Sandro | Co-host | Pushes back with numbers |

## Audience

- Who watches: 25-40, founders and engineers, mostly pre-seed to Series A.
- Why they watch: they want the operator detail, not the keynote version.
- Where they watch: YouTube long-form, Shorts, and TikTok.

## Promise

Every episode should leave the viewer with one decision they can make differently on Monday.

## What this show is not

We do not do news recaps and we do not do guest promo hours.
"""

FILLED_QUICK_REFERENCE = """# Quick reference

## Links

- Show: youtube.com/@deeptechweekly
- Website: deeptechweekly.com

## Boilerplate

- Guest intro line: "This week [guest] joins us to break down what actually shipped."
- Sponsor disclosure: "This episode is sponsored by Acme."

## Hashtag sets

- Default: #startups #deeptech #founders
"""


class UnfilledTemplateTests(unittest.TestCase):
    def test_shipped_templates_are_detected_as_unfilled(self):
        for name in PROMPT_TEMPLATES:
            with open(os.path.join(TEMPLATE_DIR, name), encoding="utf-8") as f:
                content = f.read()
            with self.subTest(template=name):
                self.assertTrue(kb.is_unfilled_template(content, name))

    def test_every_shipped_template_is_unfilled_by_identity(self):
        for name in sorted(os.listdir(TEMPLATE_DIR)):
            with open(os.path.join(TEMPLATE_DIR, name), encoding="utf-8") as f:
                content = f.read()
            with self.subTest(template=name):
                self.assertTrue(kb.is_unfilled_template(content, name))

    def test_barely_touched_template_is_still_unfilled(self):
        with open(os.path.join(TEMPLATE_DIR, "01-brand-identity.md"), encoding="utf-8") as f:
            content = f.read().replace("[Show name]", "Deep Tech Weekly")
        self.assertTrue(kb.is_unfilled_template(content, "01-brand-identity.md"))
        self.assertTrue(kb.is_unfilled_template(content))

    def test_filled_files_are_kept(self):
        self.assertFalse(kb.is_unfilled_template(FILLED_BRAND_IDENTITY, "01-brand-identity.md"))
        self.assertFalse(kb.is_unfilled_template(FILLED_QUICK_REFERENCE, "12-quick-reference.md"))

    def test_filled_file_keeps_a_few_legitimate_brackets(self):
        content = FILLED_QUICK_REFERENCE + "\n- Chapter marker: [00:00] intro\n- Guest tag: [guest]\n"
        self.assertFalse(kb.is_unfilled_template(content, "12-quick-reference.md"))

    def test_empty_file_is_unfilled(self):
        self.assertTrue(kb.is_unfilled_template("   \n\n", "01-brand-identity.md"))

    def test_wizard_output_is_not_flagged_as_a_template(self):
        import cli as cli_mod

        answers = {
            "show_name": "Deep Tech Weekly",
            "hosts": "Nika, Sandro",
            "audience": "Founders building hard tech",
            "language": "English",
            "format": "Interview, 60 min, weekly",
            "voice": "blunt, curious, technical",
        }
        self.assertFalse(
            kb.is_unfilled_template(cli_mod._render_brand_identity(answers), "01-brand-identity.md")
        )
        self.assertFalse(
            kb.is_unfilled_template(cli_mod._render_voice_and_tone(answers), "02-voice-and-tone.md")
        )


class LoadContextTests(unittest.TestCase):
    def test_unfilled_templates_never_reach_the_prompt(self):
        with tempfile.TemporaryDirectory() as kb_dir:
            for name in ("01-brand-identity.md", "02-voice-and-tone.md"):
                with open(os.path.join(TEMPLATE_DIR, name), encoding="utf-8") as src:
                    with open(os.path.join(kb_dir, name), "w", encoding="utf-8") as dst:
                        dst.write(src.read())
            context = kb.load_kb_context(
                [("01-brand-identity.md", 4000), ("02-voice-and-tone.md", 4000)], kb_dir
            )
            self.assertEqual(context, "")

    def test_filled_file_is_inlined_and_truncated(self):
        with tempfile.TemporaryDirectory() as kb_dir:
            with open(os.path.join(kb_dir, "01-brand-identity.md"), "w", encoding="utf-8") as f:
                f.write(FILLED_BRAND_IDENTITY)
            context = kb.load_kb_context([("01-brand-identity.md", 4000)], kb_dir)
            self.assertIn("--- 01-brand-identity.md ---", context)
            self.assertIn("Deep Tech Weekly", context)

            short = kb.load_kb_context([("01-brand-identity.md", 20)], kb_dir)
            self.assertLess(len(short), len(context))

    def test_missing_dir_yields_no_context(self):
        self.assertEqual(kb.load_kb_context([("01-brand-identity.md", 100)], "/nope/nowhere"), "")


class EmptinessTests(unittest.TestCase):
    def test_readme_only_counts_as_empty(self):
        with tempfile.TemporaryDirectory() as kb_dir:
            with open(os.path.join(kb_dir, "README.md"), "w", encoding="utf-8") as f:
                f.write("# Knowledge base\n")
            self.assertEqual(kb.kb_files(kb_dir), [])
            self.assertTrue(kb.is_empty(kb_dir))

            with open(os.path.join(kb_dir, "01-brand-identity.md"), "w", encoding="utf-8") as f:
                f.write(FILLED_BRAND_IDENTITY)
            self.assertEqual(kb.kb_files(kb_dir), ["01-brand-identity.md"])
            self.assertFalse(kb.is_empty(kb_dir))

    def test_missing_dir_is_empty(self):
        self.assertTrue(kb.is_empty("/nope/nowhere"))


class WarningTests(unittest.TestCase):
    def setUp(self):
        kb._warned = False

    def tearDown(self):
        kb._warned = False

    def test_warns_once_per_run(self):
        buf = io.StringIO()
        with redirect_stderr(buf):
            kb.warn_missing_context("clip scoring")
            kb.warn_missing_context("clip scoring")
        out = buf.getvalue()
        self.assertEqual(out.count("Warning:"), 1)
        self.assertIn("clip scoring", out)
        self.assertIn("podcli knowledge init", out)


if __name__ == "__main__":
    unittest.main()
