import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest import mock


ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BACKEND_ROOT = os.path.join(ROOT, "backend")
if BACKEND_ROOT not in sys.path:
    sys.path.insert(0, BACKEND_ROOT)

from services import clip_generator as cg


class ClipGeneratorTests(unittest.TestCase):
    def setUp(self):
        self._orig_remotion_available = cg._remotion_available
        cg._remotion_available = None

    def tearDown(self):
        cg._remotion_available = self._orig_remotion_available

    def _fake_exists(self, real_exists):
        def _exists(path):
            if path.endswith(os.path.join("remotion", "render.mjs")):
                return True
            if path.endswith(os.path.join("remotion-bundle", "index.html")):
                return True
            return real_exists(path)
        return _exists

    def _fake_popen(self, returncode=0, stdout="", stderr="", timeout=None):
        proc = mock.Mock()
        proc.stdout = io.StringIO(stdout)
        proc.stderr = io.StringIO(stderr)
        proc.returncode = returncode
        if timeout is not None:
            proc.wait.side_effect = [subprocess.TimeoutExpired(cmd=["node"], timeout=timeout), None]
        else:
            proc.wait.return_value = returncode
        return proc

    def test_kept_caption_overlay_path_matches_remotion_contract(self):
        with tempfile.TemporaryDirectory() as td:
            output_path = os.path.join(td, "captioned.mp4")
            expected = cg._kept_caption_overlay_path(output_path)
            self.assertTrue(expected.endswith("_captions.mov"))
            self.assertIn("captioned", expected)

    def _render_args(self, **kwargs):
        """The argv one Remotion render was invoked with."""
        real_exists = os.path.exists

        with tempfile.TemporaryDirectory() as td:
            video_path = os.path.join(td, "video.mp4")
            output_path = os.path.join(td, "captioned.mp4")
            logo_path = os.path.join(td, "logo.png")
            for path in (video_path, logo_path):
                with open(path, "wb"):
                    pass

            with mock.patch.object(cg.os.path, "exists", side_effect=self._fake_exists(real_exists)), \
                 mock.patch.object(cg.shutil, "which", return_value="/usr/bin/node"), \
                 mock.patch("subprocess.Popen", side_effect=lambda *a, **kw: self._fake_popen()) as mock_run:
                cg._render_with_remotion(
                    video_path=video_path,
                    words=[{"word": "hello", "start": 0.0, "end": 0.5}],
                    output_path=output_path,
                    logo_path=logo_path,
                    **kwargs,
                )

            for call in mock_run.call_args_list:
                argv = call.args[0] if call.args else call.kwargs.get("args", [])
                if any(str(part).endswith("render.mjs") for part in argv):
                    return [str(part) for part in argv]
        return []

    def test_logo_reaches_the_renderer_whatever_the_caption_style(self):
        """A logo belongs to the show, not to one caption style.

        `--logo` was accepted, resolved and then dropped for every style except
        branded, so three of the four rendered no watermark at all.
        """
        for style in ("branded", "hormozi", "karaoke", "subtle"):
            argv = self._render_args(caption_style=style)
            self.assertIn("--logo", argv, f"{style} lost the logo")

    def _render_words(self, **kwargs):
        """The words payload one Remotion render was handed.

        Read inside the call: the file is written next to the output and the
        temporary directory holding it is gone by the time the helper returns.
        """
        real_exists = os.path.exists
        seen = {}

        def capture(argv, *a, **kw):
            args = [str(part) for part in argv]
            if "--words" in args:
                with open(args[args.index("--words") + 1]) as fh:
                    seen.update(json.load(fh))
            return self._fake_popen()

        with tempfile.TemporaryDirectory() as td:
            video_path = os.path.join(td, "video.mp4")
            logo_path = os.path.join(td, "logo.png")
            for path in (video_path, logo_path):
                with open(path, "wb"):
                    pass

            with mock.patch.object(cg.os.path, "exists", side_effect=self._fake_exists(real_exists)), \
                 mock.patch.object(cg.shutil, "which", return_value="/usr/bin/node"), \
                 mock.patch("subprocess.Popen", side_effect=capture):
                cg._render_with_remotion(
                    video_path=video_path,
                    words=[{"word": "hello", "start": 0.0, "end": 0.5}],
                    output_path=os.path.join(td, "captioned.mp4"),
                    logo_path=logo_path,
                    **kwargs,
                )
        return seen.get("words")

    def test_captions_off_still_draws_the_rest(self):
        """Captions off is not "draw nothing".

        The logo, the chip, the bar and the cards are drawn by the same pass
        the captions ride on. Turning the words off used to take every one of
        them with it, which is what an uploaded clip asks for by default: the
        clip came back bare and nothing said why.
        """
        argv = self._render_args(caption_style="hormozi", captions=False,
                                  topic={"label": "Fitness"})

        self.assertIn("--logo", argv, "the logo went with the captions")
        self.assertIn("--topic", argv, "the chip went with the captions")

        # The pass runs; it is handed no words to draw.
        self.assertEqual(
            self._render_words(caption_style="hormozi", captions=False), [])

    def test_a_failed_overlay_is_not_delivered_as_a_finished_clip(self):
        """Silence is the failure mode this switch exists to stop.

        Nothing but Remotion draws a chip, a bar or a card, so when it fails
        there is no degraded version to hand back. Returning the bare cut would
        report success for a clip missing everything it was asked for.
        """
        real_exists = os.path.exists

        with tempfile.TemporaryDirectory() as td:
            video_path = os.path.join(td, "video.mp4")
            with open(video_path, "wb"):
                pass

            with mock.patch.object(cg.os.path, "exists",
                                   side_effect=self._fake_exists(real_exists)), \
                 mock.patch.object(cg.shutil, "which", return_value="/usr/bin/node"), \
                 mock.patch("subprocess.Popen",
                            side_effect=lambda *a, **kw: self._fake_popen(returncode=1, stderr="boom")):
                ok, _ = cg._render_with_remotion(
                    video_path=video_path,
                    words=[],
                    caption_style="hormozi",
                    output_path=os.path.join(td, "captioned.mp4"),
                    topic={"label": "Fitness"},
                    captions=False,
                )
        self.assertFalse(ok, "a failed overlay render must not report success")

    def test_captions_on_is_unchanged(self):
        """The ordinary render must not have moved."""
        words = self._render_words(caption_style="hormozi", captions=True)
        self.assertEqual([w["word"] for w in words], ["hello"])

    def test_no_caption_style_gates_the_logo(self):
        """The gate that dropped it lived in the style config, so guard that.

        Every style carried `logo_support`, and only branded said true. A logo
        is the show's; if a per-style opt-out comes back, three quarters of
        renders silently lose their watermark again.
        """
        from config.caption_styles import STYLES

        for name, config in STYLES.items():
            self.assertNotIn(
                "logo_support", config,
                f"{name} decides whether the show's logo is drawn",
            )

    def test_name_card_reaches_the_renderer(self):
        argv = self._render_args(
            caption_style="subtle",
            name_card={"title": "Jamie Gull", "subtitle": "Wave Function Ventures"},
        )
        self.assertIn("--name-card", argv)
        self.assertIn("Jamie Gull", argv)
        self.assertIn("--name-card-sub", argv)

    def test_motion_reaches_the_renderer_as_one_value(self):
        argv = self._render_args(
            caption_style="subtle",
            motion={"captions": {"enter": "pop", "exit": "sink", "duration": 8, "feel": "soft"}},
        )
        self.assertIn("--motion", argv)
        payload = json.loads(argv[argv.index("--motion") + 1])
        self.assertEqual(payload["captions"]["enter"], "pop")

    def test_theme_reaches_the_renderer_as_one_value(self):
        argv = self._render_args(
            caption_style="subtle",
            theme={"pack": "collage", "motion": "stop-motion"},
        )
        self.assertIn("--theme", argv)
        payload = json.loads(argv[argv.index("--theme") + 1])
        self.assertEqual(payload["pack"], "collage")
        self.assertEqual(payload["motion"], "stop-motion")

    def test_no_theme_sends_no_theme_flag(self):
        argv = self._render_args(caption_style="subtle")
        self.assertNotIn("--theme", argv)

    def test_layout_values_reach_remotion_unchanged(self):
        argv = self._render_args(
            caption_style="branded", caption_position="center", caption_font_scale=125,
            logo_position="bottom-right", logo_scale=0.75,
        )
        self.assertEqual(argv[argv.index("--caption-position") + 1], "center")
        self.assertEqual(argv[argv.index("--caption-font-scale") + 1], "125")
        self.assertEqual(argv[argv.index("--logo-position") + 1], "bottom-right")
        self.assertEqual(argv[argv.index("--logo-scale") + 1], "0.75")

    def test_remotion_runtime_failure_does_not_disable_future_clips(self):
        real_exists = os.path.exists

        with tempfile.TemporaryDirectory() as td:
            video_path = os.path.join(td, "video.mp4")
            output_path = os.path.join(td, "captioned.mp4")
            with open(video_path, "wb"):
                pass

            with mock.patch.object(cg.os.path, "exists", side_effect=self._fake_exists(real_exists)), \
                 mock.patch.object(cg.shutil, "which", return_value="/usr/bin/node"), \
                 mock.patch("subprocess.Popen",
                            side_effect=lambda *a, **kw: self._fake_popen(
                                returncode=1, stdout="Error: transient render failure")) as mock_run:
                first = cg._render_with_remotion(
                    video_path=video_path,
                    words=[{"word": "hello", "start": 0.0, "end": 0.5}],
                    caption_style="branded",
                    output_path=output_path,
                )
                second = cg._render_with_remotion(
                    video_path=video_path,
                    words=[{"word": "world", "start": 0.5, "end": 1.0}],
                    caption_style="branded",
                    output_path=output_path,
                )

        self.assertEqual(first, (False, None))
        self.assertEqual(second, (False, None))
        self.assertIsNone(cg._remotion_available)
        self.assertGreaterEqual(mock_run.call_count, 4)

    def test_remotion_timeout_does_not_disable_future_clips(self):
        real_exists = os.path.exists

        with tempfile.TemporaryDirectory() as td:
            video_path = os.path.join(td, "video.mp4")
            output_path = os.path.join(td, "captioned.mp4")
            with open(video_path, "wb"):
                pass

            with mock.patch.object(cg.os.path, "exists", side_effect=self._fake_exists(real_exists)), \
                 mock.patch.object(cg.shutil, "which", return_value="/usr/bin/node"), \
                 mock.patch.object(cg.os, "getpgid", return_value=4242, create=True), \
                 mock.patch.object(cg.os, "killpg", create=True) as mock_killpg, \
                 mock.patch("subprocess.Popen",
                            side_effect=lambda *a, **kw: self._fake_popen(timeout=600)) as mock_run:
                first = cg._render_with_remotion(
                    video_path=video_path,
                    words=[{"word": "hello", "start": 0.0, "end": 0.5}],
                    caption_style="branded",
                    output_path=output_path,
                )
                second = cg._render_with_remotion(
                    video_path=video_path,
                    words=[{"word": "world", "start": 0.5, "end": 1.0}],
                    caption_style="branded",
                    output_path=output_path,
                )

        self.assertEqual(first, (False, None))
        self.assertEqual(second, (False, None))
        self.assertIsNone(cg._remotion_available)
        self.assertGreaterEqual(mock_run.call_count, 4)
        self.assertTrue(mock_killpg.called, "a timed-out render must signal its process group")
        self.assertEqual(mock_killpg.call_args_list[0].args, (4242, cg.signal.SIGTERM))

    def test_remotion_failure_report_takes_bytes_as_well_as_text(self):
        # TimeoutExpired carries what the child had written and does not honour
        # text mode when it does, so the reporter has to take either.
        err = io.StringIO()
        with mock.patch.object(cg.sys, "stderr", err):
            cg._report_remotion_failure("render", b"bundled in 3.1s\n", "no browser found\n")
        printed = err.getvalue()
        self.assertIn("bundled in 3.1s", printed)
        self.assertIn("no browser found", printed)

    def test_remotion_failure_report_survives_undecodable_bytes(self):
        err = io.StringIO()
        with mock.patch.object(cg.sys, "stderr", err):
            cg._report_remotion_failure("render", b"\xff\xfe broke", None)
        self.assertIn("broke", err.getvalue())

    def test_trim_weak_opening_cuts_initial_dead_air(self):
        words = [
            {"word": "We", "start": 3.2, "end": 3.4},
            {"word": "built", "start": 3.4, "end": 3.8},
        ]
        trimmed = cg._trim_weak_opening(words, start_second=0.0, end_second=12.0)
        self.assertEqual(trimmed, 3.0)

    def test_trim_weak_opening_skips_setup_words(self):
        words = [
            {"word": "So", "start": 10.0, "end": 10.2},
            {"word": "yeah", "start": 10.2, "end": 10.35},
            {"word": "we", "start": 10.5, "end": 10.62},
            {"word": "launched", "start": 10.62, "end": 10.95},
        ]
        trimmed = cg._trim_weak_opening(words, start_second=10.0, end_second=24.0)
        self.assertGreaterEqual(trimmed, 10.35)

    def test_trim_weak_opening_preserves_question_hook(self):
        words = [
            {"word": "Why?", "start": 5.0, "end": 5.2},
            {"word": "would", "start": 5.2, "end": 5.35},
            {"word": "anyone", "start": 5.35, "end": 5.6},
        ]
        trimmed = cg._trim_weak_opening(words, start_second=5.0, end_second=20.0)
        self.assertEqual(trimmed, 5.0)

    def test_select_problematic_scene_cuts_flags_tail_and_clusters(self):
        cuts = [5.0, 10.0, 10.7, 18.4]
        flagged = cg._select_problematic_scene_cuts(cuts, duration=20.0)
        self.assertIn(10.0, flagged)
        self.assertIn(10.7, flagged)
        self.assertIn(18.4, flagged)
        self.assertNotIn(5.0, flagged)

    def test_select_problematic_scene_cuts_returns_empty_when_clean(self):
        cuts = [3.0, 9.0, 15.0]
        flagged = cg._select_problematic_scene_cuts(cuts, duration=30.0)
        self.assertEqual(flagged, [])

    def test_auto_fix_transition_jumps_is_bounded_by_max_passes(self):
        with mock.patch.object(cg, "_get_media_duration", return_value=40.0), \
             mock.patch.object(cg, "_detect_scene_cuts", return_value=[10.0, 10.6, 38.8]) as detect_mock, \
             mock.patch.object(cg, "_apply_local_transition_smoothing", return_value=True) as smooth_mock, \
             mock.patch.object(cg.os, "replace") as replace_mock:
            fixed = cg._auto_fix_transition_jumps("/tmp/fake.mp4", max_passes=2)

        self.assertTrue(fixed)
        self.assertEqual(smooth_mock.call_count, 2)
        self.assertEqual(detect_mock.call_count, 2)
        self.assertEqual(replace_mock.call_count, 2)


    def test_auto_fix_transition_jumps_leaves_designed_cuts_sharp(self):
        with mock.patch.object(cg, "_get_media_duration", return_value=40.0), \
             mock.patch.object(cg, "_detect_scene_cuts", return_value=[10.0, 10.6, 38.8]), \
             mock.patch.object(cg, "_apply_local_transition_smoothing", return_value=True) as smooth_mock, \
             mock.patch.object(cg.os, "replace"):
            fixed = cg._auto_fix_transition_jumps(
                "/tmp/fake.mp4", max_passes=1, designed=[10.1, 10.5, 38.9],
            )

        self.assertFalse(fixed)
        smooth_mock.assert_not_called()

    def test_designed_cuts_come_from_card_windows(self):
        cards = [{"kind": "stat", "start": 4, "end": 9.5}, {"kind": "quote", "start": 12.0, "end": True}, "junk"]
        self.assertEqual(cg._designed_cuts(cards), [4.0, 9.5, 12.0])
        self.assertEqual(cg._designed_cuts(None), [])

    def test_designed_cuts_shift_by_the_intro(self):
        cards = [{"kind": "stat", "start": 4, "end": 9.5}]
        self.assertEqual(cg._designed_cuts(cards, offset=2.5), [6.5, 12.0])


class TransitionAutofixGatingTests(unittest.TestCase):
    def test_multi_segment_cut_can_jump(self):
        self.assertTrue(cg._reframe_can_jump(
            reframe=True, crop_strategy="face",
            keep_segments=[{"start": 0, "end": 5}, {"start": 8, "end": 12}],
        ))

    def test_non_reframe_format_cannot_jump(self):
        self.assertFalse(cg._reframe_can_jump(reframe=False, crop_strategy="face"))

    def test_center_crop_cannot_jump(self):
        self.assertFalse(cg._reframe_can_jump(reframe=True, crop_strategy="center"))

    def test_face_follow_can_jump_without_speaker_labels(self):
        # whisper.cpp (the default engine) skips diarization, so the tracker
        # still snaps between faces with every word unlabeled.
        self.assertTrue(cg._reframe_can_jump(reframe=True, crop_strategy="face"))

    def test_speaker_strategies_can_jump(self):
        self.assertTrue(cg._reframe_can_jump(reframe=True, crop_strategy="speaker"))
        self.assertTrue(cg._reframe_can_jump(reframe=True, crop_strategy="speaker-hardcut"))

    def test_manual_crop_jumps_only_with_multiple_keyframes(self):
        self.assertFalse(cg._reframe_can_jump(
            reframe=True, crop_strategy="manual", crop_keyframes=[{"t": 0, "x_pct": 50}],
        ))
        self.assertTrue(cg._reframe_can_jump(
            reframe=True, crop_strategy="manual",
            crop_keyframes=[{"t": 0, "x_pct": 20}, {"t": 3, "x_pct": 80}],
        ))

    def test_default_engine_face_crop_runs_autofix(self):
        env = {k: v for k, v in os.environ.items() if k != "PODCLI_TRANSITION_AUTOFIX_PASSES"}
        with mock.patch.dict(os.environ, env, clear=True):
            passes = cg._transition_autofix_passes(
                cg._reframe_can_jump(reframe=True, crop_strategy="face", crop_keyframes=None)
            )
        self.assertEqual(passes, 2)

    def test_default_passes_gated_by_jump_potential(self):
        env = {k: v for k, v in os.environ.items() if k != "PODCLI_TRANSITION_AUTOFIX_PASSES"}
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(cg._transition_autofix_passes(True), 2)
            self.assertEqual(cg._transition_autofix_passes(False), 0)

    def test_env_override_wins(self):
        with mock.patch.dict(os.environ, {"PODCLI_TRANSITION_AUTOFIX_PASSES": "1"}):
            self.assertEqual(cg._transition_autofix_passes(False), 1)
            self.assertEqual(cg._transition_autofix_passes(True), 1)
        with mock.patch.dict(os.environ, {"PODCLI_TRANSITION_AUTOFIX_PASSES": "0"}):
            self.assertEqual(cg._transition_autofix_passes(True), 0)

    def test_env_override_is_clamped_and_validated(self):
        with mock.patch.dict(os.environ, {"PODCLI_TRANSITION_AUTOFIX_PASSES": "9"}):
            self.assertEqual(cg._transition_autofix_passes(False), 2)
        with mock.patch.dict(os.environ, {"PODCLI_TRANSITION_AUTOFIX_PASSES": "junk"}):
            self.assertEqual(cg._transition_autofix_passes(True), 2)

    def test_preserve_timing_skips_transition_autofix(self):
        with mock.patch.dict(os.environ, {"PODCLI_TRANSITION_AUTOFIX_PASSES": "2"}):
            self.assertEqual(
                cg._render_transition_autofix_passes(
                    preserve_timing=True,
                    reframe=True,
                    crop_strategy="face",
                ),
                0,
            )

    def test_regular_face_crop_still_runs_transition_autofix(self):
        with mock.patch.dict(os.environ, {"PODCLI_TRANSITION_AUTOFIX_PASSES": "2"}):
            self.assertEqual(
                cg._render_transition_autofix_passes(
                    preserve_timing=False,
                    reframe=True,
                    crop_strategy="face",
                ),
                2,
            )


class OutputPathReservationTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="podcli-output-test-")
        cg._reserved_output_paths.clear()
        self.addCleanup(cg._reserved_output_paths.clear)
        self.addCleanup(shutil.rmtree, self.tmpdir, ignore_errors=True)

    def test_duplicate_title_gets_a_suffix(self):
        first = cg._reserve_output_path(self.tmpdir, "same_title_short", ".mp4")
        second = cg._reserve_output_path(self.tmpdir, "same_title_short", ".mp4")
        third = cg._reserve_output_path(self.tmpdir, "same_title_short", ".mp4")
        self.assertEqual(os.path.basename(first), "same_title_short.mp4")
        self.assertEqual(os.path.basename(second), "same_title_short-2.mp4")
        self.assertEqual(os.path.basename(third), "same_title_short-3.mp4")

    def test_concurrent_renders_of_one_title_never_share_a_file(self):
        barrier = threading.Barrier(8)

        def render(i):
            barrier.wait()
            path = cg._reserve_output_path(self.tmpdir, "duplicate_short", ".mp4")
            with open(path, "w", encoding="utf-8") as f:
                f.write(f"clip-{i}")
            return path

        with ThreadPoolExecutor(max_workers=8) as pool:
            paths = list(pool.map(render, range(8)))

        self.assertEqual(len(set(paths)), 8)
        contents = []
        for p in paths:
            self.assertTrue(os.path.exists(p))
            with open(p, encoding="utf-8") as f:
                contents.append(f.read())
        self.assertEqual(len(set(contents)), 8)

    def test_sidecar_paths_inherit_the_unique_stem(self):
        cg._reserve_output_path(self.tmpdir, "same_title_short", ".mp4")
        second = cg._reserve_output_path(self.tmpdir, "same_title_short", ".mp4")
        base, _ = os.path.splitext(second)
        self.assertTrue(base.endswith("same_title_short-2"))


class BoundRangeToSourceTests(unittest.TestCase):
    """A requested range past the end of the source must be clamped before
    it reaches ffmpeg's -ss/-t cut, which would otherwise silently stop at
    EOF and under-deliver instead of failing."""

    def test_unknown_source_duration_leaves_range_untouched(self):
        end, segs = cg._bound_range_to_source(10.0, 999.0, None, source_duration=0.0)
        self.assertEqual(end, 999.0)
        self.assertIsNone(segs)

    def test_end_second_clamped_to_source_duration(self):
        end, segs = cg._bound_range_to_source(5.0, 120.0, None, source_duration=42.0)
        self.assertEqual(end, 42.0)
        self.assertIsNone(segs)

    def test_end_second_within_source_is_unchanged(self):
        end, _ = cg._bound_range_to_source(5.0, 20.0, None, source_duration=42.0)
        self.assertEqual(end, 20.0)

    def test_start_at_or_past_source_duration_raises(self):
        with self.assertRaises(ValueError):
            cg._bound_range_to_source(42.0, 50.0, None, source_duration=42.0)
        with self.assertRaises(ValueError):
            cg._bound_range_to_source(50.0, 60.0, None, source_duration=42.0)

    def test_keep_segments_clamped_and_out_of_range_ones_dropped(self):
        segs_in = [
            {"start": 5.0, "end": 10.0},
            {"start": 35.0, "end": 60.0},  # end runs past source
            {"start": 50.0, "end": 55.0},  # starts past source entirely
        ]
        end, segs = cg._bound_range_to_source(5.0, 60.0, segs_in, source_duration=42.0)
        self.assertEqual(end, 42.0)
        self.assertEqual(segs, [
            {"start": 5.0, "end": 10.0},
            {"start": 35.0, "end": 42.0},
        ])

    def test_all_keep_segments_dropped_falls_back_to_none(self):
        segs_in = [{"start": 50.0, "end": 55.0}]
        _, segs = cg._bound_range_to_source(5.0, 60.0, segs_in, source_duration=42.0)
        self.assertIsNone(segs)


@unittest.skipUnless(
    shutil.which("ffmpeg") and shutil.which("ffprobe"), "ffmpeg/ffprobe not installed"
)
class SidecarsAndCleanVariantTests(unittest.TestCase):
    """Real render, forced onto the ASS path (center crop, no Remotion/node
    dependency) so it stays fast and deterministic. Covers the sidecar and
    clean-variant wiring added to generate_clip, which nothing else here
    exercises end to end."""

    @classmethod
    def setUpClass(cls):
        cls.tmpdir = tempfile.mkdtemp(prefix="podcli-sidecar-test-")
        cls.src = os.path.join(cls.tmpdir, "src.mp4")
        subprocess.run(
            [
                "ffmpeg", "-y", "-loglevel", "error",
                "-f", "lavfi", "-i", "testsrc=size=320x240:rate=25:duration=3",
                "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
                cls.src,
            ],
            check=True, capture_output=True,
        )

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmpdir, ignore_errors=True)

    def test_writes_sidecars_and_clean_variant(self):
        from services import clip_generator as cg

        words = [
            {"word": "hello", "start": 0.2, "end": 0.6, "speaker": None},
            {"word": "world", "start": 0.7, "end": 1.1, "speaker": None},
            {"word": "testing", "start": 1.3, "end": 1.8, "speaker": None},
        ]
        out_dir = os.path.join(self.tmpdir, "out")
        result = cg.generate_clip(
            video_path=self.src,
            start_second=0,
            end_second=2.5,
            caption_style="subtle",
            crop_strategy="center",
            transcript_words=words,
            title="sidecar_test",
            output_dir=out_dir,
            # Burned captions aren't the point of this test (that's covered
            # by caption_renderer's own tests), turning them off here keeps
            # this test to the render pipeline's font/compositor dependencies
            # out of the way of the sidecar/clean-variant wiring under test.
            # Sidecars otherwise follow `captions`, so request them
            # explicitly for this clean (caption-free) export.
            captions=False,
            write_subtitles=True,
            clean_fillers=False,
            write_clean_variant=True,
        )

        self.assertTrue(os.path.exists(result["output_path"]))

        self.assertIn("srt_path", result)
        self.assertTrue(os.path.exists(result["srt_path"]))
        with open(result["srt_path"], encoding="utf-8") as f:
            srt_text = f.read()
        self.assertIn("hello world testing", srt_text)

        self.assertIn("vtt_path", result)
        self.assertTrue(os.path.exists(result["vtt_path"]))
        self.assertTrue(open(result["vtt_path"], encoding="utf-8").read().startswith("WEBVTT"))

        self.assertIn("clean_output_path", result)
        clean_path = result["clean_output_path"]
        self.assertTrue(os.path.exists(clean_path))
        self.assertNotEqual(clean_path, result["output_path"])

        # Clean variant: same audio presence/rough duration, no caption burn.
        self.assertTrue(cg.probe_has_audio_stream(clean_path))
        main_duration = cg._get_media_duration(result["output_path"])
        clean_duration = cg._get_media_duration(clean_path)
        self.assertAlmostEqual(main_duration, clean_duration, delta=0.5)

    def test_duration_is_content_length_not_the_file_with_intro_included(self):
        from services import clip_generator as cg

        intro_path = os.path.join(self.tmpdir, "intro.mp4")
        subprocess.run(
            [
                "ffmpeg", "-y", "-loglevel", "error",
                "-f", "lavfi", "-i", "testsrc=size=320x240:rate=25:duration=1",
                "-f", "lavfi", "-i", "sine=frequency=330:duration=1",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
                intro_path,
            ],
            check=True, capture_output=True,
        )

        out_dir = os.path.join(self.tmpdir, "out_with_intro")
        result = cg.generate_clip(
            video_path=self.src,
            start_second=0,
            end_second=2.5,
            caption_style="subtle",
            crop_strategy="center",
            title="intro_duration_test",
            output_dir=out_dir,
            captions=False,
            clean_fillers=False,
            intro_path=intro_path,
        )
        # "duration" is what clip_history and its learnings have always
        # recorded: the content's own length, not however long the delivered
        # file plays once an intro is prepended.
        self.assertAlmostEqual(result["duration"], 2.5, delta=0.3)
        self.assertIn("output_duration", result)
        self.assertAlmostEqual(result["output_duration"], 3.5, delta=0.3)
        self.assertGreater(result["output_duration"], result["duration"])

    def test_video_only_source_does_not_require_an_audio_stream(self):
        from services import clip_generator as cg

        silent_src = os.path.join(self.tmpdir, "silent_src.mp4")
        subprocess.run(
            [
                "ffmpeg", "-y", "-loglevel", "error",
                "-f", "lavfi", "-i", "testsrc=size=320x240:rate=25:duration=3",
                "-c:v", "libx264", "-pix_fmt", "yuv420p",
                silent_src,
            ],
            check=True, capture_output=True,
        )
        self.assertFalse(cg.probe_has_audio_stream(silent_src))

        out_dir = os.path.join(self.tmpdir, "out_silent")
        result = cg.generate_clip(
            video_path=silent_src,
            start_second=0,
            end_second=2.5,
            caption_style="subtle",
            crop_strategy="center",
            title="silent_source_test",
            output_dir=out_dir,
            captions=False,
            clean_fillers=False,
        )
        self.assertTrue(os.path.exists(result["output_path"]))

    def test_no_sidecars_when_captions_off_and_not_explicitly_requested(self):
        from services import clip_generator as cg

        words = [{"word": "hello", "start": 0.2, "end": 0.6, "speaker": None}]
        out_dir = os.path.join(self.tmpdir, "out_no_sidecars")
        result = cg.generate_clip(
            video_path=self.src,
            start_second=0,
            end_second=2.5,
            caption_style="subtle",
            crop_strategy="center",
            transcript_words=words,
            title="no_sidecar_test",
            output_dir=out_dir,
            captions=False,
            clean_fillers=False,
        )
        self.assertTrue(os.path.exists(result["output_path"]))
        self.assertNotIn("srt_path", result)
        self.assertNotIn("vtt_path", result)
        output_base, _ = os.path.splitext(result["output_path"])
        self.assertFalse(os.path.exists(f"{output_base}.srt"))
        self.assertFalse(os.path.exists(f"{output_base}.vtt"))

    def test_rerender_clears_stale_sidecars_and_clean_variant(self):
        from services import clip_generator as cg

        words = [
            {"word": "hello", "start": 0.2, "end": 0.6, "speaker": None},
            {"word": "world", "start": 0.7, "end": 1.1, "speaker": None},
        ]
        out_dir = os.path.join(self.tmpdir, "out_rerender")
        first = cg.generate_clip(
            video_path=self.src,
            start_second=0,
            end_second=2.5,
            caption_style="subtle",
            crop_strategy="center",
            transcript_words=words,
            title="rerender_test",
            output_dir=out_dir,
            captions=False,
            write_subtitles=True,
            clean_fillers=False,
            write_clean_variant=True,
        )
        output_base, _ = os.path.splitext(first["output_path"])
        srt_path = f"{output_base}.srt"
        vtt_path = f"{output_base}.vtt"
        clean_path = f"{output_base}_clean.mp4"
        self.assertTrue(os.path.exists(srt_path))
        self.assertTrue(os.path.exists(vtt_path))
        self.assertTrue(os.path.exists(clean_path))

        # Re-render the same clip (same title/output_dir, so the same
        # output path) without subtitles or a clean variant this time.
        # Without cleanup, the previous render's .srt/.vtt/_clean.mp4
        # would sit next to the new clip describing stale content.
        cg._reserved_output_paths.discard(first["output_path"])
        second = cg.generate_clip(
            video_path=self.src,
            start_second=0,
            end_second=2.5,
            caption_style="subtle",
            crop_strategy="center",
            transcript_words=words,
            title="rerender_test",
            output_dir=out_dir,
            captions=False,
            clean_fillers=False,
            write_clean_variant=False,
        )
        self.assertEqual(second["output_path"], first["output_path"])
        self.assertFalse(os.path.exists(srt_path))
        self.assertFalse(os.path.exists(vtt_path))
        self.assertFalse(os.path.exists(clean_path))


if __name__ == "__main__":
    unittest.main()
