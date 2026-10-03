"""Opening hooks in the CLI render paths.

The MCP tools and the studio hand a clip's hook to the renderer. The CLI paths
built their clip specs without it, so a hook the AI proposed in `podcli
process` or one asked for on `podcli studio` never reached the render. These
cover each hand-off: the AI parse keeps it, every CLI render call passes it,
and the studio script checks and forwards it.
"""

import ast
import json
import os
import sys
import types
import unittest
from unittest import mock

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BACKEND_ROOT = os.path.join(ROOT, "backend")
if BACKEND_ROOT not in sys.path:
    sys.path.insert(0, BACKEND_ROOT)

import cli as cli_mod
import clip_studio
from services import ai_cli, ai_provider
from services import claude_suggest as cs

from test_studio_format import _run_studio, _studio_args

SEGMENTS = [
    {"start": 10.0, "end": 20.0, "text": "We talked about discipline and habits.", "speaker": "A"},
    {"start": 20.0, "end": 60.0, "text": "The moment I realized failure was the turning point.", "speaker": "B"},
]
BODY = [{"start": 20.0, "end": 58.0}]


class SuggestedHookTests(unittest.TestCase):
    def test_keeps_a_hook_inside_the_clip(self):
        hook = cs._suggested_hook({"hook": {"start": 40.0, "end": 44.5, "mode": "repeat"}}, 20.0, 58.0, BODY)
        self.assertEqual(hook, {"start": 40.0, "end": 44.5, "mode": "repeat"})

    def test_reads_numbers_the_model_wrote_as_strings(self):
        hook = cs._suggested_hook({"hook": {"start": "40.04", "end": "44.5", "mode": "move"}}, 20.0, 58.0, BODY)
        self.assertEqual(hook, {"start": 40.0, "end": 44.5, "mode": "move"})

    def test_drops_a_hook_outside_the_clip(self):
        self.assertIsNone(cs._suggested_hook({"hook": {"start": 5.0, "end": 9.0, "mode": "repeat"}}, 20.0, 58.0, BODY))

    def test_drops_an_unknown_mode(self):
        self.assertIsNone(cs._suggested_hook({"hook": {"start": 40.0, "end": 44.0, "mode": "loop"}}, 20.0, 58.0, BODY))

    def test_drops_a_hook_that_is_not_an_object(self):
        self.assertIsNone(cs._suggested_hook({"hook": "40-44"}, 20.0, 58.0, BODY))
        self.assertIsNone(cs._suggested_hook({}, 20.0, 58.0, BODY))

    def test_both_prompts_offer_the_hook(self):
        prompt = cs._build_prompt("[20.0s] B: hello", 2, 1.0, 3)
        self.assertIn('"hook"', prompt)
        self.assertNotIn("{{", prompt)


class FindMomentsCarriesHookTests(unittest.TestCase):
    def setUp(self):
        self._orig_chain = ai_provider._chain
        self._orig_run = ai_cli._run_ai_command
        payload = {"clips": [{
            "title": "The turning point", "start_second": 20.0, "end_second": 58.0,
            "segments": BODY, "content_type": "guest_story", "quote": "Failure was the turning point",
            "hook": {"start": 50.0, "end": 54.0, "mode": "repeat"},
        }]}
        ai_provider._chain = lambda: [("cli", "/usr/bin/claude", "claude")]
        ai_cli._run_ai_command = lambda **kw: types.SimpleNamespace(
            returncode=0, stdout=json.dumps(payload), stderr="")

    def tearDown(self):
        ai_provider._chain = self._orig_chain
        ai_cli._run_ai_command = self._orig_run

    def test_found_moment_keeps_its_hook(self):
        clips = cs.find_moments_from_text("the turning point", SEGMENTS, [])
        self.assertEqual(clips[0]["hook"], {"start": 50.0, "end": 54.0, "mode": "repeat"})


class ClipHookTests(unittest.TestCase):
    def test_passes_a_hook_that_fits(self):
        clip = {"start_second": 20.0, "end_second": 58.0, "segments": BODY,
                "hook": {"start": 40.0, "end": 44.0, "mode": "move"}}
        self.assertEqual(cli_mod._clip_hook(clip), {"start": 40.0, "end": 44.0, "mode": "move"})

    def test_no_hook_is_none(self):
        self.assertIsNone(cli_mod._clip_hook({"start_second": 20.0, "end_second": 58.0}))

    def test_drops_a_hook_review_moved_out_of_the_clip(self):
        clip = {"start_second": 20.0, "end_second": 30.0, "segments": None,
                "hook": {"start": 40.0, "end": 44.0, "mode": "repeat"}}
        with mock.patch("builtins.print") as printed:
            self.assertIsNone(cli_mod._clip_hook(clip))
        self.assertIn("Opening hook dropped", printed.call_args[0][0])

    def test_every_cli_render_passes_the_hook(self):
        with open(cli_mod.__file__, encoding="utf-8") as f:
            tree = ast.parse(f.read())
        calls = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "generate_clip"
        ]
        self.assertGreaterEqual(len(calls), 5)
        for call in calls:
            self.assertIn("hook", {k.arg for k in call.keywords}, f"generate_clip at line {call.lineno}")


class StudioHookTests(unittest.TestCase):
    HOOK = '{"start": 4.0, "end": 7.5, "mode": "repeat"}'

    def test_studio_hands_the_hook_to_the_script(self):
        cmd = _run_studio(_studio_args(hook=self.HOOK))
        self.assertEqual(cmd[cmd.index("--hook") + 1], self.HOOK)

    def test_no_hook_sends_no_flag(self):
        self.assertNotIn("--hook", _run_studio(_studio_args()))

    def test_script_accepts_a_hook_inside_the_fragment(self):
        self.assertEqual(clip_studio._hook_arg(self.HOOK, 0.0, 10.0),
                         {"start": 4.0, "end": 7.5, "mode": "repeat"})

    def test_script_stops_on_a_hook_outside_the_fragment(self):
        with self.assertRaises(SystemExit) as stop:
            clip_studio._hook_arg(self.HOOK, 5.0, 20.0)
        self.assertIn("--hook", str(stop.exception))

    def test_script_stops_on_malformed_json(self):
        with self.assertRaises(SystemExit):
            clip_studio._hook_arg("{start: 4", 0.0, 10.0)

    def test_no_hook_is_none(self):
        self.assertIsNone(clip_studio._hook_arg(None, 0.0, 10.0))

    def test_fragment_render_passes_the_hook(self):
        hook = {"start": 4.0, "end": 7.5, "mode": "move"}
        with mock.patch("services.clip_generator.generate_clip",
                        return_value={"output_path": "/tmp/x.mp4"}) as gen:
            clip_studio._render_fragment("v.mp4", 0.0, 10.0, [], "hormozi", "center",
                                         "fragment", "/tmp", hook=hook)
        self.assertEqual(gen.call_args.kwargs["hook"], hook)


if __name__ == "__main__":
    unittest.main()
