import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BACKEND_ROOT = os.path.join(ROOT, "backend")
if BACKEND_ROOT not in sys.path:
    sys.path.insert(0, BACKEND_ROOT)

from services import claude_suggest as cs
from services import content_generator as cg
from services import thumbnail_ai as tai


class AIFallbackTests(unittest.TestCase):
    def test_build_prompt_includes_excluded_ranges(self):
        prompt = cs._build_prompt(
            transcript_text="[0.0s] Test transcript",
            segment_count=1,
            duration_min=100 / 60,
            top_n=8,
            exclude_clips=[
                {"start_second": 120.0, "end_second": 152.0, "title": "Grid connection hot take"},
                {"start_second": 900.0, "end_second": 932.0, "title": "Gigawatts nobody considers"},
            ],
        )

        self.assertIn("ALREADY SELECTED CLIPS", prompt)
        self.assertIn("120.0s to 152.0s", prompt)
        self.assertIn("Grid connection hot take", prompt)
        self.assertIn("search the ENTIRE timeline and diversify the picks", prompt)

    def test_suggest_with_claude_retries_with_codex_after_runtime_failure(self):
        segments = [
            {"start": 0.0, "end": 12.0, "speaker": "SPEAKER_00", "text": "Warmup context."},
            {"start": 12.0, "end": 40.0, "speaker": "SPEAKER_00", "text": "The best grid connection is no grid connection."},
        ]
        progress = []
        codex_payload = json.dumps({
            "clips": [
                {
                    "title": "The best grid connection is no grid connection",
                    "start_second": 12.0,
                    "end_second": 38.0,
                    "segments": [{"start": 12.0, "end": 38.0}],
                    "duration": 26,
                    "content_type": "hot_take",
                    "scores": {"standalone": 5, "hook": 5, "relevance": 4, "quotability": 4},
                    "quote": "The best grid connection is no grid connection",
                    "why": "Strong contrarian line with clean standalone context.",
                }
            ]
        })

        with mock.patch.object(
            cs,
            "_find_ai_cli_candidates",
            return_value=[("/tmp/claude", "claude"), ("/tmp/codex", "codex")],
        ), mock.patch.object(
            cs,
            "_run_ai_command",
            side_effect=[
                subprocess.CompletedProcess(args=["claude"], returncode=1, stdout="", stderr="claude down"),
                subprocess.CompletedProcess(args=["codex"], returncode=0, stdout=codex_payload, stderr=""),
            ],
        ):
            clips = cs.suggest_with_claude(
                segments=segments,
                top_n=1,
                progress_callback=lambda _pct, msg: progress.append(msg),
            )

        self.assertIsNotNone(clips)
        self.assertEqual(len(clips), 1)
        self.assertEqual(clips[0]["_ai_engine"], "codex")
        self.assertIn("Retrying with Codex...", progress)
        self.assertIn("Codex suggested 1 clips", progress)

    def test_suggest_more_with_claude_searches_undercovered_buckets(self):
        segments = []
        for i in range(12):
            start = float(i * 300)
            segments.append({
                "start": start,
                "end": start + 40.0,
                "speaker": "SPEAKER_00",
                "text": f"Segment {i}",
            })

        calls = []

        def fake_suggest(*, segments, top_n, exclude_clips=None, progress_callback=None):
            calls.append({
                "start": segments[0]["start"],
                "end": segments[-1]["end"],
                "top_n": top_n,
                "exclude_len": len(exclude_clips or []),
            })
            first_start = segments[0]["start"]
            return [{
                "title": f"Clip {first_start}",
                "start_second": first_start,
                "end_second": first_start + 28.0,
                "segments": [{"start": first_start, "end": first_start + 28.0}],
                "duration": 28,
            }]

        with mock.patch.object(cs, "suggest_with_claude", side_effect=fake_suggest):
            clips = cs.suggest_more_with_claude(
                segments=segments,
                existing_clips=[
                    {"start_second": 0.0, "end_second": 180.0, "title": "Early clip"},
                    {"start_second": 320.0, "end_second": 500.0, "title": "Another early clip"},
                ],
                top_n=6,
            )

        self.assertIsNotNone(clips)
        self.assertGreaterEqual(len(calls), 2)
        self.assertTrue(all(call["exclude_len"] >= 2 for call in calls))
        bucketed_calls = [call for call in calls if call["end"] - call["start"] < 2000]
        self.assertGreaterEqual(len(bucketed_calls), 2)
        self.assertTrue(all(call["start"] > 500.0 for call in bucketed_calls[:2]))

    def test_suggest_more_with_claude_uses_global_fallback_after_bucket_passes(self):
        segments = []
        for i in range(9):
            start = float(i * 240)
            segments.append({
                "start": start,
                "end": start + 35.0,
                "speaker": "SPEAKER_00",
                "text": f"Segment {i}",
            })

        calls = []

        def fake_suggest(*, segments, top_n, exclude_clips=None, progress_callback=None):
            calls.append({
                "start": segments[0]["start"],
                "end": segments[-1]["end"],
                "top_n": top_n,
            })
            if segments[0]["start"] == 0.0 and segments[-1]["end"] > 1000.0:
                return [{
                    "title": "Global clip",
                    "start_second": 1440.0,
                    "end_second": 1468.0,
                    "segments": [{"start": 1440.0, "end": 1468.0}],
                    "duration": 28,
                }]
            return None

        with mock.patch.object(cs, "suggest_with_claude", side_effect=fake_suggest):
            clips = cs.suggest_more_with_claude(
                segments=segments,
                existing_clips=[],
                top_n=5,
            )

        self.assertIsNotNone(clips)
        self.assertEqual(clips[0]["title"], "Global clip")
        self.assertGreaterEqual(len(calls), 2)
        self.assertEqual(calls[-1]["start"], 0.0)

    def test_suggest_initial_with_claude_uses_bucket_strategy_for_long_episode(self):
        segments = []
        for i in range(20):
            start = float(i * 330)
            segments.append({
                "start": start,
                "end": start + 45.0,
                "speaker": "SPEAKER_00",
                "text": f"Segment {i} with enough text to count as a normal transcript block.",
            })

        calls = []

        def fake_suggest(*, segments, top_n, exclude_clips=None, progress_callback=None, timeout=300, error_sink=None):
            calls.append({
                "start": segments[0]["start"],
                "end": segments[-1]["end"],
                "top_n": top_n,
                "timeout": timeout,
                "exclude_len": len(exclude_clips or []),
            })
            first_start = segments[0]["start"]
            return [{
                "title": f"Clip {first_start}",
                "start_second": first_start,
                "end_second": first_start + 28.0,
                "segments": [{"start": first_start, "end": first_start + 28.0}],
                "duration": 28,
            }]

        with mock.patch.object(cs, "suggest_with_claude", side_effect=fake_suggest):
            clips = cs.suggest_initial_with_claude(
                segments=segments,
                top_n=5,
            )

        self.assertIsNotNone(clips)
        self.assertGreaterEqual(len(calls), 4)
        self.assertTrue(all(call["timeout"] == 90 for call in calls[:4]))
        self.assertTrue(all(call["end"] - call["start"] < 2500 for call in calls[:4]))
        self.assertGreaterEqual(calls[1]["exclude_len"], 1)

    def test_suggest_with_claude_reports_actual_timeout_limit(self):
        segments = [
            {"start": 0.0, "end": 20.0, "speaker": "SPEAKER_00", "text": "Short segment one."},
            {"start": 20.0, "end": 40.0, "speaker": "SPEAKER_00", "text": "Short segment two."},
        ]
        progress = []

        with mock.patch.object(
            cs,
            "_find_ai_cli_candidates",
            return_value=[("/tmp/claude", "claude")],
        ), mock.patch.object(
            cs,
            "_run_ai_command",
            side_effect=subprocess.TimeoutExpired(cmd=["claude"], timeout=90),
        ):
            clips = cs.suggest_with_claude(
                segments=segments,
                top_n=1,
                timeout=90,
                progress_callback=lambda _pct, msg: progress.append(msg),
            )

        self.assertIsNone(clips)
        self.assertIn("Claude timed out (90s limit)", progress)

    def test_generate_clip_content_retries_with_codex(self):
        clip = {
            "title": "Power demand is exploding",
            "start_second": 10.0,
            "end_second": 36.0,
            "content_type": "market_landscape",
        }
        transcript_segments = [
            {"start": 9.0, "speaker": "SPEAKER_00", "text": "Power demand is exploding."},
            {"start": 18.0, "speaker": "SPEAKER_00", "text": "Data centers will need a lot more energy."},
        ]
        progress = []
        codex_text = """TITLES (8 options, 40-60 chars, keyword-first, follow title spec):
1. Power Demand Is Exploding Fast
2. Data Centers Need Much More Energy
TOP PICK: 1 — strongest hook

DESCRIPTION:
Power demand is exploding.
Guest explains why data centers need more energy.

TAGS:
power demand, data centers, energy, ai infrastructure

HASHTAGS:
#power #energy #datacenters #ai #infrastructure"""

        with mock.patch.object(
            cg,
            "_find_ai_cli_candidates",
            return_value=[("/tmp/claude", "claude"), ("/tmp/codex", "codex")],
        ), mock.patch.object(
            cg,
            "_run_ai_command",
            side_effect=[
                subprocess.CompletedProcess(args=["claude"], returncode=1, stdout="", stderr="claude down"),
                subprocess.CompletedProcess(args=["codex"], returncode=0, stdout=codex_text, stderr=""),
            ],
        ):
            result = cg.generate_clip_content(
                clip=clip,
                transcript_segments=transcript_segments,
                progress_callback=lambda _pct, msg: progress.append(msg),
            )

        self.assertIsNotNone(result)
        self.assertEqual(result["engine"], "codex")
        self.assertTrue(result["titles"])
        self.assertIn("Retrying content generation with Codex...", progress)

    def test_thumbnail_layout_retries_with_codex(self):
        codex_layout = """
Some wrapper text
{
  "line1": "POWER DEMAND",
  "line2": "IS EXPLODING",
  "box_y": "78%",
  "photo_object_position": "center 18%",
  "line1_font_size": "96px",
  "line2_font_size": "90px"
}
"""

        with mock.patch.object(
            cs,
            "_find_ai_cli_candidates",
            return_value=[("/tmp/claude", "claude"), ("/tmp/codex", "codex")],
        ), mock.patch.object(
            cs,
            "_run_ai_command",
            side_effect=[
                subprocess.CompletedProcess(args=["claude"], returncode=1, stdout="", stderr="claude down"),
                subprocess.CompletedProcess(args=["codex"], returncode=0, stdout=codex_layout, stderr=""),
            ],
        ):
            layout = tai.ask_claude_for_layout(
                title="Power demand is exploding",
                frame_path="/tmp/frame.png",
                frame_info={"face_x_pct": 50, "face_y_pct": 40, "face_w_pct": 20, "face_h_pct": 25},
                config={"enabled": True},
            )

        self.assertEqual(layout["line1"], "POWER DEMAND")
        self.assertEqual(layout["line2"], "IS EXPLODING")
        self.assertEqual(layout["box_y"], "78%")


class AICliDiscoveryTests(unittest.TestCase):
    def test_find_cli_resolves_windows_cmd_shim(self):
        with tempfile.TemporaryDirectory() as tmp:
            shim = os.path.join(tmp, "claude.cmd")
            with open(shim, "w", encoding="utf-8") as fh:
                fh.write("@echo off\n")
            with mock.patch.object(cs.sys, "platform", "win32"):
                found = cs._find_cli("claude", [os.path.join(tmp, "claude")])
            self.assertEqual(found, shim)

    @unittest.skipIf(os.name == "nt", "POSIX executable discovery; Windows uses .cmd/.exe shims")
    def test_find_cli_uses_home_bin(self):
        with tempfile.TemporaryDirectory() as home:
            bin_dir = os.path.join(home, "bin")
            os.makedirs(bin_dir)
            cli = os.path.join(bin_dir, "claude")
            with open(cli, "w", encoding="utf-8") as fh:
                fh.write("#!/bin/sh\n")
            with mock.patch.dict(os.environ, {"HOME": home, "PATH": ""}, clear=False):
                with mock.patch("os.path.expanduser", side_effect=lambda p: p.replace("~", home)):
                    found = cs._find_cli("claude", [])
            self.assertEqual(found, cli)

    @unittest.skipIf(os.name == "nt", "POSIX executable discovery; Windows uses .cmd/.exe shims")
    def test_npmrc_prefix_is_searched(self):
        with tempfile.TemporaryDirectory() as home:
            prefix = os.path.join(home, "npm-prefix")
            bin_dir = os.path.join(prefix, "bin")
            os.makedirs(bin_dir)
            cli = os.path.join(bin_dir, "claude")
            with open(cli, "w", encoding="utf-8") as fh:
                fh.write("#!/bin/sh\n")
            with open(os.path.join(home, ".npmrc"), "w", encoding="utf-8") as fh:
                fh.write(f"prefix={prefix}\n")
            with mock.patch.dict(os.environ, {"HOME": home, "PATH": ""}, clear=False):
                with mock.patch("os.path.expanduser", side_effect=lambda p: p.replace("~", home)):
                    with mock.patch.object(cs, "_package_manager_bin_dirs", return_value=[]):
                        with mock.patch.object(cs, "_shell_lookup", return_value=None):
                            found = cs._find_cli("claude", [])
            self.assertEqual(found, cli)

    def test_parse_shell_lookup_line_handles_type_a(self):
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            path = tmp.name
        try:
            self.assertEqual(cs._parse_shell_lookup_line(f"claude is {path}"), path)
        finally:
            os.remove(path)

    def test_find_cli_uses_legacy_claude_local_path(self):
        with tempfile.TemporaryDirectory() as home:
            legacy_bin = os.path.join(home, ".claude", "local", "bin")
            os.makedirs(legacy_bin)
            cli = os.path.join(legacy_bin, "claude")
            with open(cli, "w", encoding="utf-8") as fh:
                fh.write("#!/bin/sh\n")
            with mock.patch.dict(os.environ, {"HOME": home, "PATH": ""}, clear=False):
                with mock.patch("os.path.expanduser", side_effect=lambda p: p.replace("~", home)):
                    found = cs._find_cli("claude", cs._ai_cli_search_paths("claude"))
            self.assertEqual(found, cli)

    def test_env_override_prefers_podcli_claude_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            cli = os.path.join(tmp, "my-claude")
            with open(cli, "w", encoding="utf-8") as fh:
                fh.write("#!/bin/sh\n")
            with mock.patch.dict(os.environ, {"PODCLI_CLAUDE_PATH": cli, "PATH": ""}, clear=False):
                with mock.patch.object(cs, "_find_cli", return_value=None) as find_mock:
                    candidates = cs._find_ai_cli_candidates()
            find_mock.assert_called_once()
            self.assertEqual(find_mock.call_args.args[0], "codex")
            self.assertEqual(candidates[0], (cli, "claude"))

    def test_configured_path_reads_from_env_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            env_file = os.path.join(tmp, ".env")
            cli = os.path.join(tmp, "custom-claude")
            with open(cli, "w", encoding="utf-8") as fh:
                fh.write("#!/bin/sh\n")
            with open(env_file, "w", encoding="utf-8") as fh:
                fh.write(f"PODCLI_CLAUDE_PATH={cli}\n")
            with mock.patch.dict(os.environ, {"PODCLI_ENV_FILE": env_file, "PATH": ""}, clear=False):
                os.environ.pop("PODCLI_CLAUDE_PATH", None)
                found = cs._configured_cli_path("claude")
            self.assertEqual(found, cli)

    def test_find_cli_falls_back_to_shell_lookup(self):
        with tempfile.TemporaryDirectory() as tmp:
            cli = os.path.join(tmp, "claude")
            with open(cli, "w", encoding="utf-8") as fh:
                fh.write("#!/bin/sh\n")
            with mock.patch.object(cs, "_shell_lookup", return_value=cli):
                with mock.patch("shutil.which", return_value=None):
                    found = cs._find_cli("claude", [])
            self.assertEqual(found, cli)

    def test_get_ai_cli_status_reports_candidates(self):
        with mock.patch.object(
            cs,
            "_find_ai_cli_candidates",
            return_value=[("/tmp/claude", "claude")],
        ), mock.patch.object(cs, "_configured_cli_path", return_value=None):
            status = cs.get_ai_cli_status()
        self.assertTrue(status["available"])
        self.assertEqual(status["candidates"][0]["engine"], "claude")
        with tempfile.TemporaryDirectory() as tmp:
            prompt_file = os.path.join(tmp, "prompt.txt")
            with open(prompt_file, "w", encoding="utf-8") as fh:
                fh.write("find clips")
            cli = os.path.join(tmp, "claude")
            with open(cli, "w", encoding="utf-8") as fh:
                fh.write("#!/bin/sh\n")

            with mock.patch("services.claude_suggest.subprocess.run") as run_mock:
                run_mock.return_value = subprocess.CompletedProcess(
                    args=[cli, "--print", "-p", "-"],
                    returncode=0,
                    stdout="{}",
                    stderr="",
                )
                cs._run_ai_command(
                    cli_path=cli,
                    engine="claude",
                    prompt="find clips",
                    prompt_file=prompt_file,
                    project_dir=tmp,
                    timeout=30,
                )

            args, kwargs = run_mock.call_args
            self.assertEqual(args[0], [cli, "--print", "-p", "-"])
            self.assertIn("stdin", kwargs)
            self.assertFalse(kwargs.get("shell"))


if __name__ == "__main__":
    unittest.main()
