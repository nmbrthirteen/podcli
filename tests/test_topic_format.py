import contextlib
import importlib
import io
import os
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest import mock

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BACKEND_ROOT = os.path.join(ROOT, "backend")
if BACKEND_ROOT not in sys.path:
    sys.path.insert(0, BACKEND_ROOT)

import cli as cli_mod
from services import clip_generator as cg
from services import media_probe, reel, video_cut
from services.claude_suggest import ClipBounds
from services.formats import FORMATS, get_format
from services.integrations._shared.timeline_ir import CaptionLayer, MediaClip, Project, Short
from services.integrations.davinci_resolve import emitter

resolve_integration = importlib.import_module("services.integrations.davinci_resolve.integration")


class TopicFormatSpecTests(unittest.TestCase):
    def test_topic_is_a_1080p_segment_between_5_and_15_minutes(self):
        spec = get_format("topic")
        self.assertEqual(spec.name, "topic")
        self.assertEqual(spec.dims, (1920, 1080))
        self.assertFalse(spec.reframe)
        self.assertEqual(spec.caption_profile, "lower_third")
        self.assertEqual((spec.dur_min, spec.dur_max), (300, 900))
        self.assertEqual((spec.target_min, spec.target_max), (420, 720))
        self.assertFalse(spec.auto_tighten)

    def test_topic_framing_asks_for_setup_and_payoff(self):
        spec = get_format("topic")
        self.assertIn("topic segment", spec.editor)
        self.assertIn("setup", spec.pacing)
        self.assertIn("payoff", spec.pacing)
        self.assertNotIn("SHORTER IS BETTER", spec.pacing)

    def test_existing_formats_keep_their_bounds(self):
        expected = {
            "vertical": (20, 45, 20, 35),
            "horizontal": (60, 300, 90, 240),
            "square": (20, 45, 20, 35),
        }
        for name, (dmin, dmax, tmin, tmax) in expected.items():
            spec = FORMATS[name]
            self.assertEqual((spec.dur_min, spec.dur_max, spec.target_min, spec.target_max), (dmin, dmax, tmin, tmax))
            self.assertTrue(spec.auto_tighten, name)

    def test_topic_prompt_bounds_come_from_the_spec(self):
        bounds = ClipBounds.of("topic")
        self.assertEqual((bounds.dur_min, bounds.dur_max), (300, 900))
        self.assertEqual((bounds.target_min, bounds.target_max), (420, 720))
        self.assertTrue(bounds.keeps(600))
        self.assertFalse(bounds.keeps(299))
        self.assertFalse(bounds.keeps(901))


class TopicFormatWiringTests(unittest.TestCase):
    def _help(self, *argv):
        out = io.StringIO()
        with mock.patch.object(sys, "argv", ["podcli", *argv]), \
                contextlib.redirect_stdout(out), self.assertRaises(SystemExit) as raised:
            cli_mod.main()
        self.assertEqual(raised.exception.code, 0)
        return out.getvalue()

    def test_process_and_studio_accept_topic(self):
        for command in ("process", "studio"):
            text = " ".join(self._help(command, "--help").split())
            self.assertIn("{vertical,horizontal,square,topic}", text, command)

    def test_cli_choices_are_the_formats_table(self):
        self.assertEqual(list(FORMATS), ["vertical", "horizontal", "square", "topic"])

    def test_unknown_format_still_falls_back_to_vertical(self):
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(get_format("nonsense").name, "vertical")


class RenderTimeoutScalingTests(unittest.TestCase):
    def test_ffmpeg_timeout_keeps_current_values_up_to_five_minutes(self):
        self.assertEqual(media_probe.ffmpeg_timeout(), 300)
        self.assertEqual(media_probe.ffmpeg_timeout(40), 920)
        self.assertEqual(media_probe.ffmpeg_timeout(300), 1800)

    def test_ffmpeg_timeout_grows_for_topic_length_clips(self):
        self.assertEqual(media_probe.ffmpeg_timeout(600), 2400)
        self.assertEqual(media_probe.ffmpeg_timeout(900), 3600)
        self.assertEqual(media_probe.ffmpeg_timeout(10_000), 7200)

    def test_remotion_timeout_floor_and_scale(self):
        self.assertEqual(media_probe.remotion_timeout(), 6300)
        self.assertEqual(media_probe.remotion_timeout(60), 6300)
        self.assertEqual(media_probe.remotion_timeout(900), 11700)

    def test_encode_duration_prefers_explicit_length(self):
        cmd = ["ffmpeg", "-y", "-ss", "10", "-i", "in.mp4", "-t", "900"]
        self.assertEqual(media_probe._encode_duration(cmd), 900.0)

    def test_encode_duration_probes_the_input_otherwise(self):
        with mock.patch.object(media_probe, "get_media_duration_seconds", return_value=840.0) as probe:
            self.assertEqual(media_probe._encode_duration(["ffmpeg", "-y", "-i", "in.mp4", "-vf", "x"]), 840.0)
        probe.assert_called_once_with("in.mp4")

    def test_encode_duration_is_unknown_when_probe_finds_nothing(self):
        with mock.patch.object(media_probe, "get_media_duration_seconds", return_value=0.0):
            self.assertIsNone(media_probe._encode_duration(["ffmpeg", "-y", "-i", "in.mp4"]))

    def test_fallback_runner_scales_a_fit_encode_by_input_length(self):
        done = mock.Mock(returncode=0, stderr="")
        with mock.patch.object(media_probe, "get_media_duration_seconds", return_value=900.0), \
                mock.patch.object(media_probe, "proc_run", return_value=done) as run:
            media_probe.run_ffmpeg_with_fallback(
                ["ffmpeg", "-y", "-i", "in.mp4", "-vf", "scale=1920:1080"], ["-c:a", "aac"], "out.mp4",
            )
        self.assertEqual(run.call_args.kwargs["timeout"], 3600)

    def test_fallback_runner_keeps_the_floor_for_short_clips(self):
        done = mock.Mock(returncode=0, stderr="")
        with mock.patch.object(media_probe, "get_media_duration_seconds", return_value=0.0), \
                mock.patch.object(media_probe, "proc_run", return_value=done) as run:
            media_probe.run_ffmpeg_with_fallback(["ffmpeg", "-y", "-i", "in.mp4"], [], "out.mp4")
        self.assertEqual(run.call_args.kwargs["timeout"], 300)

    def test_cut_segment_timeout_follows_the_cut_length(self):
        done = mock.Mock(returncode=0, stderr="")
        with mock.patch.object(video_cut, "proc_run", return_value=done) as run:
            video_cut.cut_segment("in.mp4", "out.mp4", 100.0, 1000.0)
            self.assertEqual(run.call_args.kwargs["timeout"], 3600)
            video_cut.cut_segment("in.mp4", "out.mp4", 0.0, 30.0)
            self.assertEqual(run.call_args.kwargs["timeout"], 720)

    def test_multi_segment_concat_timeout_follows_summed_parts(self):
        done = mock.Mock(returncode=0, stderr="")
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(video_cut, "cut_segment"), \
                mock.patch.object(video_cut, "probe_duration", side_effect=[450.0, 450.0]), \
                mock.patch.object(video_cut, "proc_run", return_value=done) as run:
            video_cut.cut_multi_segment(
                "in.mp4", os.path.join(tmp, "out.mp4"),
                [{"start": 0.0, "end": 450.0}, {"start": 500.0, "end": 950.0}],
            )
        self.assertEqual(run.call_args.kwargs["timeout"], 3600)

    def test_reel_cut_scales_with_the_moment(self):
        moment = reel.Moment(start=100.0, end=1000.0)
        done = mock.Mock(returncode=0, stderr="")
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(reel, "proc_run", return_value=done) as run:
            reel._cut("in.mp4", tmp, 1, moment, "topic")
            self.assertEqual(run.call_args.kwargs["timeout"], 3600)
            reel._cut("in.mp4", tmp, 2, reel.Moment(start=0.0, end=20.0), "horizontal")
            self.assertEqual(run.call_args.kwargs["timeout"], 520)

    def test_reel_concat_scales_and_keeps_the_reencode_floor(self):
        session = reel.ReelSession(
            session_id="topic-timeouts", source="in.mp4", profile="auto", out_dir="", format="topic",
            moments=[reel.Moment(start=0.0, end=900.0, dirty=False)],
        )
        calls = []

        def fake_run(cmd, timeout, check):
            calls.append(timeout)
            return mock.Mock(returncode=1 if len(calls) == 1 else 0, stderr="")

        with tempfile.TemporaryDirectory() as tmp:
            session.out_dir = tmp
            os.makedirs(os.path.join(tmp, "clips"))
            Path(tmp, "clips", "clip_01.mp4").write_bytes(b"x")
            with mock.patch.object(reel, "proc_run", side_effect=fake_run), \
                    mock.patch.object(reel.ReelSession, "save", return_value=""):
                reel.build_reel(session)
        self.assertEqual(calls, [3600, 3600])

        calls.clear()
        short = reel.ReelSession(
            session_id="short-timeouts", source="in.mp4", profile="auto", out_dir="", format="horizontal",
            moments=[reel.Moment(start=0.0, end=20.0, dirty=False)],
        )
        with tempfile.TemporaryDirectory() as tmp:
            short.out_dir = tmp
            os.makedirs(os.path.join(tmp, "clips"))
            Path(tmp, "clips", "clip_01.mp4").write_bytes(b"x")
            with mock.patch.object(reel, "proc_run", side_effect=fake_run), \
                    mock.patch.object(reel.ReelSession, "save", return_value=""):
                reel.build_reel(short)
        self.assertEqual(calls, [520, 900])

    def test_remotion_call_gets_the_clip_duration(self):
        captured = {}

        def fake_stream(cmd, timeout, cwd, env):
            captured["timeout"] = timeout
            return mock.Mock(returncode=1, stdout="", stderr="")

        with tempfile.TemporaryDirectory() as tmp:
            bundle = os.path.join(tmp, "remotion", ".bundle-cache", "remotion-bundle")
            os.makedirs(bundle)
            Path(bundle, "index.html").write_text("x")
            Path(tmp, "remotion", "render.mjs").write_text("x")
            with mock.patch.dict(cg.paths, {"project_root": tmp}), \
                    mock.patch.object(cg.shutil, "which", return_value="/usr/bin/node"), \
                    mock.patch.object(cg, "_run_streaming", side_effect=fake_stream), \
                    mock.patch.object(cg, "_report_remotion_failure"), \
                    mock.patch.object(cg, "_remotion_available", None):
                cg._render_with_remotion(
                    "in.mp4", [{"word": "a", "start": 0.0, "end": 0.5}], "branded",
                    os.path.join(tmp, "out.mp4"), duration=900.0,
                )
        self.assertEqual(captured["timeout"], 11700)


class AutoFillerCutTests(unittest.TestCase):
    class _Stop(Exception):
        pass

    def _words(self):
        return [
            {"word": f"w{i}", "start": float(i), "end": i + 0.5, "speaker": None}
            for i in range(40)
        ]

    def _tight_calls(self, fmt, clean_fillers, keep_segments=None):
        with tempfile.NamedTemporaryFile(suffix=".mp4") as src, \
                mock.patch.object(cg, "probe_has_audio_stream", return_value=True), \
                mock.patch.object(cg, "_get_media_duration", return_value=2000.0), \
                mock.patch("services.audiogram.is_audio_only", return_value=False), \
                mock.patch.object(cg, "_build_tight_segments",
                                  return_value=[{"start": 0, "end": 10}, {"start": 12, "end": 40}]) as tight, \
                mock.patch.object(cg.tempfile, "mkdtemp", side_effect=self._Stop), \
                self.assertRaises(self._Stop):
            cg.generate_clip(
                video_path=src.name,
                start_second=0.0,
                end_second=40.0,
                format=fmt,
                transcript_words=self._words(),
                clean_fillers=clean_fillers,
                keep_segments=keep_segments,
                keep_caption_overlay=True,
            )
        return tight.call_count

    def test_topic_skips_auto_cutting_by_default(self):
        self.assertEqual(self._tight_calls("topic", None), 0)
        self.assertEqual(self._tight_calls("topic", None, [{"start": 0.0, "end": 40.0}]), 0)

    def test_topic_cuts_when_asked_explicitly(self):
        self.assertEqual(self._tight_calls("topic", True), 1)
        self.assertEqual(self._tight_calls("topic", True, [{"start": 0.0, "end": 40.0}]), 1)

    def test_topic_stays_uncut_when_turned_off(self):
        self.assertEqual(self._tight_calls("topic", False), 0)

    def test_other_formats_still_cut_by_default(self):
        for fmt in ("vertical", "horizontal", "square"):
            self.assertEqual(self._tight_calls(fmt, None), 1, fmt)
            self.assertEqual(self._tight_calls(fmt, False), 0, fmt)


def _clip(frames, fps=30.0, name="cut.mp4"):
    return MediaClip(path=Path(f"/media/{name}"), fps=fps, duration_frames=frames, width=1920, height=1080,
                     has_audio=True, audio_channels=2)


def _emit(project):
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "topic.fcpxml"
        emitter.emit(project, out)
        return ET.parse(out).getroot()


class TopicFcpxmlExportTests(unittest.TestCase):
    def test_900_second_short_at_30_fps(self):
        project = Project(name="Topics", fps=30.0, width=1920, height=1080, shorts=[
            Short(title="Pricing", source=_clip(27000),
                  captions=CaptionLayer(path=Path("/media/c.mov"), fps=30.0, duration_frames=27000)),
        ])
        root = _emit(project)

        fmt = root.find("./resources/format")
        self.assertEqual(fmt.get("frameDuration"), "1/30s")
        self.assertEqual((fmt.get("width"), fmt.get("height")), ("1920", "1080"))
        self.assertEqual(fmt.get("name"), "FFVideoFormat1920x1080p30")

        assets = {a.get("name"): a for a in root.iterfind("./resources/asset")}
        self.assertEqual(assets["Pricing: source"].get("duration"), "900s")
        self.assertEqual(assets["Pricing: captions"].get("duration"), "900s")

        compound = root.find("./resources/media/sequence")
        self.assertEqual(compound.get("duration"), "900s")
        self.assertEqual(compound.get("tcFormat"), "NDF")
        main = compound.find("./spine/asset-clip")
        self.assertEqual((main.get("offset"), main.get("start"), main.get("duration")), ("0s", "0s", "900s"))
        self.assertEqual(main.find("video").get("duration"), "900s")

        library = root.find("./library/event/project/sequence")
        self.assertEqual(library.get("duration"), "900s")
        ref = library.find("./spine/ref-clip")
        self.assertEqual((ref.get("offset"), ref.get("duration")), ("0s", "900s"))

    def test_caption_layer_is_clamped_or_kept_against_a_900_second_source(self):
        longer = Short(title="Long", source=_clip(27000),
                       captions=CaptionLayer(path=Path("/media/c.mov"), fps=30.0, duration_frames=27030))
        shorter = Short(title="Short", source=_clip(27000),
                        captions=CaptionLayer(path=Path("/media/d.mov"), fps=30.0, duration_frames=26985))
        root = _emit(Project(name="Layers", fps=30.0, width=1920, height=1080, shorts=[longer, shorter]))

        layers = {v.get("name"): v.get("duration") for v in root.iter("video") if v.get("lane")}
        self.assertEqual(layers["Long captions"], "900s")
        self.assertEqual(layers["Short captions"], "1799/2s")

    def test_900_second_short_at_29_97_fps(self):
        frames = round(900 * 30000 / 1001)
        self.assertEqual(frames, 26973)
        root = _emit(Project(name="NTSC", fps=29.97, width=1920, height=1080,
                             shorts=[Short(title="Topic", source=_clip(frames, fps=29.97))]))

        self.assertEqual(root.find("./resources/format").get("frameDuration"), "1001/30000s")
        asset = root.find("./resources/asset")
        self.assertEqual(asset.get("duration"), "8999991/10000s")
        compound = root.find("./resources/media/sequence")
        self.assertEqual(compound.get("duration"), "8999991/10000s")
        self.assertEqual(compound.get("tcFormat"), "DF")
        library = root.find("./library/event/project/sequence")
        self.assertEqual(library.get("duration"), "8999991/10000s")

    def test_topic_and_short_share_one_timeline(self):
        root = _emit(Project(name="Mixed", fps=30.0, width=1920, height=1080, shorts=[
            Short(title="Topic", source=_clip(27000)),
            Short(title="Teaser", source=_clip(1350, name="teaser.mp4")),
        ]))

        library = root.find("./library/event/project/sequence")
        self.assertEqual(library.get("duration"), "945s")
        refs = [(r.get("name"), r.get("offset"), r.get("duration")) for r in library.iter("ref-clip")]
        self.assertEqual(refs, [("Topic", "0s", "900s"), ("Teaser", "900s", "45s")])

    def test_resolve_tool_exports_a_900_second_clip(self):
        probe = {"fps": 30.0, "duration_frames": 27000, "width": 1920, "height": 1080,
                 "has_audio": True, "audio_channels": 2}
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(resolve_integration, "probe_media", return_value=probe):
            out = Path(tmp) / "nested" / "topic.fcpxml"
            result = resolve_integration.integration._handle_export({
                "project_name": "Topics", "output_path": str(out), "width": 1920, "height": 1080,
                "shorts": [{"title": "Pricing", "source_path": "/media/pricing.mp4"}],
            })
            root = ET.parse(out).getroot()

        self.assertEqual(result["format"], "1920x1080@30.0fps")
        self.assertEqual(result["shorts_count"], 1)
        self.assertEqual(root.find("./library/event/project/sequence").get("duration"), "900s")


if __name__ == "__main__":
    unittest.main()
