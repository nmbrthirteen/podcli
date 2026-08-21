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

from services import ai_cli as ai
from services import ai_provider as ap
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
            ap,
            "_chain",
            return_value=[("cli", "/tmp/claude", "claude"), ("cli", "/tmp/codex", "codex")],
        ), mock.patch.object(
            ai,
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

        def fake_suggest(*, segments, top_n, exclude_clips=None, progress_callback=None, timeout=300, error_sink=None, reaction_times=None):
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
            ap,
            "_chain",
            return_value=[("cli", "/tmp/claude", "claude")],
        ), mock.patch.object(
            ai,
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
            ap,
            "_chain",
            return_value=[("cli", "/tmp/claude", "claude"), ("cli", "/tmp/codex", "codex")],
        ), mock.patch.object(
            ai,
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
            ap,
            "_chain",
            return_value=[("cli", "/tmp/claude", "claude"), ("cli", "/tmp/codex", "codex")],
        ), mock.patch.object(
            ai,
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
            with mock.patch.object(ai.sys, "platform", "win32"):
                found = ai._find_cli("claude", [os.path.join(tmp, "claude")])
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
                    found = ai._find_cli("claude", [])
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
                    with mock.patch.object(ai, "_package_manager_bin_dirs", return_value=[]):
                        with mock.patch.object(ai, "_shell_lookup", return_value=None):
                            found = ai._find_cli("claude", [])
            self.assertEqual(found, cli)

    def test_parse_shell_lookup_line_handles_type_a(self):
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            path = tmp.name
        try:
            self.assertEqual(ai._parse_shell_lookup_line(f"claude is {path}"), path)
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
                    found = ai._find_cli("claude", ai._ai_cli_search_paths("claude"))
            self.assertEqual(found, cli)

    def test_env_override_prefers_podcli_claude_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            cli = os.path.join(tmp, "my-claude")
            with open(cli, "w", encoding="utf-8") as fh:
                fh.write("#!/bin/sh\n")
            with mock.patch.dict(os.environ, {"PODCLI_CLAUDE_PATH": cli, "PATH": ""}, clear=False):
                with mock.patch.object(ai, "_find_cli", return_value=None) as find_mock:
                    candidates = ai._find_ai_cli_candidates()
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
                found = ai._configured_cli_path("claude")
            self.assertEqual(found, cli)

    def test_find_cli_falls_back_to_shell_lookup(self):
        with tempfile.TemporaryDirectory() as tmp:
            cli = os.path.join(tmp, "claude")
            with open(cli, "w", encoding="utf-8") as fh:
                fh.write("#!/bin/sh\n")
            with mock.patch.object(ai, "_shell_lookup", return_value=cli):
                with mock.patch("shutil.which", return_value=None):
                    found = ai._find_cli("claude", [])
            self.assertEqual(found, cli)

    def test_get_ai_cli_status_reports_candidates(self):
        with mock.patch.object(
            ai,
            "_find_ai_cli_candidates",
            return_value=[("/tmp/claude", "claude")],
        ), mock.patch.object(ai, "_configured_cli_path", return_value=None):
            status = ai.get_ai_cli_status()
        self.assertTrue(status["available"])
        self.assertEqual(status["candidates"][0]["engine"], "claude")
        with tempfile.TemporaryDirectory() as tmp:
            prompt_file = os.path.join(tmp, "prompt.txt")
            with open(prompt_file, "w", encoding="utf-8") as fh:
                fh.write("find clips")
            cli = os.path.join(tmp, "claude")
            with open(cli, "w", encoding="utf-8") as fh:
                fh.write("#!/bin/sh\n")

            with mock.patch("services.ai_cli.subprocess.run") as run_mock:
                run_mock.return_value = subprocess.CompletedProcess(
                    args=[cli, "--print", "-p", "-"],
                    returncode=0,
                    stdout="{}",
                    stderr="",
                )
                ai._run_ai_command(
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


class RankClipsTests(unittest.TestCase):
    def _pool(self):
        return [
            {"title": "a", "start_second": 30, "end_second": 60, "duration": 30, "score": 18,
             "quote": "q", "why": "w", "content_type": "hot_take"},
            {"title": "b", "start_second": 90, "end_second": 120, "duration": 30, "score": 17,
             "quote": "q", "why": "w", "content_type": "guest_story"},
            {"title": "c", "start_second": 150, "end_second": 180, "duration": 30, "score": 17,
             "quote": "q", "why": "w", "content_type": "hot_take"},
            {"title": "d", "start_second": 210, "end_second": 240, "duration": 30, "score": 16,
             "quote": "q", "why": "w", "content_type": "hot_take"},
        ]

    def _run(self, stdout, clips=None, top_n=2):
        pool = clips if clips is not None else self._pool()
        with mock.patch.object(cs.podcli_cloud, "prompt_block", return_value=""), \
             mock.patch.object(ap, "_chain", return_value=[("cli", "/tmp/claude", "claude")]), \
             mock.patch.object(
                 ai,
                 "_run_ai_command",
                 return_value=subprocess.CompletedProcess(
                     args=["claude"], returncode=0, stdout=stdout, stderr=""
                 ),
             ):
            return cs.rank_clips_with_ai(pool, top_n), pool

    def test_ranking_decides_the_cut_that_tied_scores_cannot(self):
        ranked, _ = self._run(json.dumps({
            "ranked": [{"id": 2}, {"id": 0}, {"id": 3}, {"id": 1}],
        }))
        selected = cs._select_top_by_score(ranked, 2)
        self.assertEqual([c["title"] for c in selected], ["a", "c"])

    def test_selection_is_returned_in_timeline_order(self):
        ranked, _ = self._run(json.dumps({"ranked": [{"id": 3}, {"id": 1}, {"id": 0}, {"id": 2}]}))
        selected = cs._select_top_by_score(ranked, 2)
        self.assertEqual([c["start_second"] for c in selected], [90, 210])

    def test_a_partial_ranking_is_discarded_whole(self):
        narrated = []
        pool = self._pool()
        with mock.patch.object(cs.podcli_cloud, "prompt_block", return_value=""), \
             mock.patch.object(ap, "_chain", return_value=[("cli", "/tmp/claude", "claude")]), \
             mock.patch.object(
                 ai,
                 "_run_ai_command",
                 return_value=subprocess.CompletedProcess(
                     args=["claude"], returncode=0,
                     stdout=json.dumps({"ranked": [{"id": 2}, {"id": 0}]}), stderr="",
                 ),
             ):
            cs.rank_clips_with_ai(pool, 2, progress_callback=lambda _p, m: narrated.append(m))
        self.assertTrue(all("rank" not in c for c in pool))
        self.assertEqual([c["title"] for c in cs._select_top_by_score(pool, 2)], ["a", "b"])
        self.assertTrue(any("Ranking unavailable" in m for m in narrated))

    def test_an_unreadable_answer_leaves_score_ordering_alone(self):
        pool = self._pool()
        self._run("not json at all", clips=pool)
        self.assertTrue(all("rank" not in c for c in pool))

    def test_no_ai_available_is_not_an_error(self):
        pool = self._pool()
        with mock.patch.object(cs.podcli_cloud, "prompt_block", return_value=""), \
             mock.patch.object(ap, "_chain", return_value=[]):
            out = cs.rank_clips_with_ai(pool, 2)
        self.assertIs(out, pool)
        self.assertTrue(all("rank" not in c for c in pool))

    def test_a_pool_with_nothing_to_drop_makes_no_call(self):
        pool = self._pool()[:3]
        with mock.patch.object(ap, "_chain") as chain:
            cs.rank_clips_with_ai(pool, 2)
        chain.assert_not_called()

    def test_the_pass_is_metered_under_its_own_purpose(self):
        seen = {}
        original = ap.generate_json

        def capture(prompt, **kwargs):
            seen.update(kwargs)
            seen["prompt"] = prompt
            return original(prompt, **kwargs)

        with mock.patch.object(cs.podcli_cloud, "prompt_block", return_value="RETAINS BEST: hot takes"), \
             mock.patch.object(ap, "generate_json", capture), \
             mock.patch.object(ap, "_chain", return_value=[("cli", "/tmp/claude", "claude")]), \
             mock.patch.object(
                 ai,
                 "_run_ai_command",
                 return_value=subprocess.CompletedProcess(
                     args=["claude"], returncode=0,
                     stdout=json.dumps({"ranked": [{"id": i} for i in range(4)]}), stderr="",
                 ),
             ):
            cs.rank_clips_with_ai(self._pool(), 2)

        self.assertEqual(seen["purpose"], "rank_moments")
        self.assertNotIn("stable_prefix", seen)
        self.assertIn("RETAINS BEST: hot takes", seen["prompt"])


class WorkspaceLearningsTests(unittest.TestCase):
    def _prompt_sent_to_cli(self, learned):
        sent = {}

        def capture(**kwargs):
            with open(kwargs["prompt_file"], encoding="utf-8") as fh:
                sent["prompt"] = fh.read()
            return subprocess.CompletedProcess(
                args=["claude"], returncode=0,
                stdout=json.dumps({"clips": [{
                    "title": "A moment",
                    "start_second": 10.0,
                    "end_second": 40.0,
                    "segments": [{"start": 10.0, "end": 40.0}],
                    "scores": {"standalone": 5, "hook": 5, "relevance": 4, "quotability": 4},
                    "quote": "q", "why": "w",
                }]}),
                stderr="",
            )

        segments = [
            {"start": float(i * 5), "end": float(i * 5 + 5), "text": f"line {i}", "speaker": "A"}
            for i in range(12)
        ]
        with mock.patch.object(cs.podcli_cloud, "prompt_block", return_value=learned), \
             mock.patch.object(ap, "_chain", return_value=[("cli", "/tmp/claude", "claude")]), \
             mock.patch.object(ai, "_run_ai_command", side_effect=capture):
            cs.suggest_with_claude(segments=segments, top_n=1)
        return sent.get("prompt", "")

    def test_a_local_cli_receives_the_workspace_learnings(self):
        prompt = self._prompt_sent_to_cli("WHAT WORKS ON THIS CHANNEL: founder stories.")
        self.assertIn("WHAT WORKS ON THIS CHANNEL: founder stories.", prompt)

    def test_the_free_path_prompt_is_untouched(self):
        prompt = self._prompt_sent_to_cli("")
        self.assertFalse(prompt.startswith("\n"))
        self.assertTrue(prompt.startswith("You are a viral clip editor"))
