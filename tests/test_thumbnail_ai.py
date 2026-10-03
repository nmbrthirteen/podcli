"""Tests for grounding thumbnail headline generation in the clip's own content
(payoff, the question it answers, its verbatim opening line) instead of the
title alone."""

import os
import sys
import unittest
from unittest import mock

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BACKEND_ROOT = os.path.join(ROOT, "backend")
if BACKEND_ROOT not in sys.path:
    sys.path.insert(0, BACKEND_ROOT)

from services import thumbnail_ai as ta


class GroundingContextTests(unittest.TestCase):
    def test_empty_without_grounding(self):
        self.assertEqual(ta._grounding_context(None), "")
        self.assertEqual(ta._grounding_context({}), "")

    def test_includes_every_field_given(self):
        text = ta._grounding_context({
            "payoff": "the payoff line",
            "context_line": "the question",
            "preview_text": "the opening line",
        })
        self.assertIn("the payoff line", text)
        self.assertIn("the question", text)
        self.assertIn("the opening line", text)

    def test_omits_fields_not_given(self):
        text = ta._grounding_context({"payoff": "only this"})
        self.assertIn("only this", text)
        self.assertNotIn("Question this clip answers", text)
        self.assertNotIn("verbatim opening line", text)


class GroundingFlowsIntoPromptTests(unittest.TestCase):
    def test_ask_claude_for_layout_includes_grounding_in_prompt(self):
        captured = {}

        def fake_ask(prompt, timeout=30):
            captured["prompt"] = prompt
            return {"line1": "a", "line2": "b"}

        with mock.patch.object(ta, "_ask_ai_for_json", side_effect=fake_ask):
            ta.ask_claude_for_layout(
                "a generic title",
                frame_path="/tmp/frame.png",
                grounding={"payoff": "the clip's actual payoff"},
            )
        self.assertIn("the clip's actual payoff", captured["prompt"])

    def test_generate_headline_variations_includes_grounding_in_prompt(self):
        captured = {}

        def fake_ask(prompt, timeout=30):
            captured["prompt"] = prompt
            return [{"line1": "a", "line2": "b"}]

        with mock.patch.object(ta, "_ask_ai_for_json", side_effect=fake_ask):
            ta.generate_headline_variations(
                "a generic title", 3, grounding={"preview_text": "the real opening line"}
            )
        self.assertIn("the real opening line", captured["prompt"])


if __name__ == "__main__":
    unittest.main()
