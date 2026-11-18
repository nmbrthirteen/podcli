import argparse
import contextlib
import io
import json
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


FACE_MAP = {
    "is_split_screen": True,
    "is_mixed_layout": True,
    "clusters": [{"center_x": 1148}, {"center_x": 2949}],
}

SRT = """1
00:00:00,000 --> 00:00:04,000
Hello and welcome to the show.

2
00:00:04,500 --> 00:00:08,000
Today we talk about split screens.
"""


def _process_args(video_path, transcript_path, output_dir):
    return argparse.Namespace(
        video=video_path,
        transcript=transcript_path,
        top=1,
        output=output_dir,
        preset=None,
        engine=None,
        assemblyai_api_key=None,
        fast=False,
        thumbnails=False,
        caption_style=None,
        crop=None,
        format=None,
        profile=None,
        logo=None,
        outro=None,
        no_outro=True,
        intro=None,
        time_adjust=None,
        no_energy=True,
        no_speakers=False,
        no_cache=True,
        no_resume=True,
        quality=None,
        allow_ass_fallback=False,
        review_each=False,
        post_review=False,
        json=False,
        yes=False,
        quiet=False,
    )


class CliTranscriptTests(unittest.TestCase):
    def _run_process(
        self,
        transcript_text,
        cached_transcript=None,
        use_cache=False,
        generated_extra=None,
        no_speakers=False,
    ):
        """Run cmd_process with --transcript and return the generate_clip mock."""
        clip = {
            "title": "Test clip",
            "start_second": 0.0,
            "end_second": 0.5,
            "duration": 0.5,
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            video_path = os.path.join(temp_dir, "episode.mp4")
            transcript_path = os.path.join(temp_dir, "transcript")
            output_dir = os.path.join(temp_dir, "clips")
            with open(video_path, "wb") as video_file:
                video_file.write(b"video")
            with open(transcript_path, "w", encoding="utf-8") as transcript_file:
                transcript_file.write(transcript_text or "")

            generated = {
                "output_path": os.path.join(output_dir, "test-clip.mp4"),
                "file_size_mb": 1.0,
                **(generated_extra or {}),
            }
            args = _process_args(video_path, None if use_cache else transcript_path, output_dir)
            args.no_cache = not use_cache
            args.no_speakers = no_speakers
            out = io.StringIO()

            with (
                mock.patch.object(cli_mod, "kb_is_empty", return_value=False),
                mock.patch(
                    "services.encoder.get_encoder_info",
                    return_value={"best": "mock", "system": "test"},
                ),
                mock.patch("services.asset_store.default_intro", return_value=None),
                mock.patch("services.corrections.apply_corrections"),
                mock.patch(
                    "services.claude_suggest._find_ai_cli",
                    return_value=(None, None),
                ),
                mock.patch("services.ai_provider.status", return_value={"providers": []}),
                mock.patch(
                    "services.transcript_packer.load_cached_transcript_for_video",
                    return_value=cached_transcript,
                ),
                mock.patch.object(cli_mod, "_suggest_clips", return_value=[clip]),
                mock.patch.object(cli_mod, "_review_clips", return_value=[clip]),
                mock.patch.object(
                    cli_mod, "_should_enter_post_render_loop", return_value=False
                ),
                mock.patch(
                    "services.clip_generator.generate_clip",
                    return_value=generated,
                ) as generate_clip,
                mock.patch.dict(os.environ),
                contextlib.redirect_stdout(out),
            ):
                os.environ.pop("NO_COLOR", None)
                os.environ.pop("PODCLI_ENGINE", None)
                cli_mod.cmd_process(args)

        self.stdout = out.getvalue()
        generate_clip.assert_called_once()
        return generate_clip

    def test_json_transcript_face_map_reaches_clip_generator(self):
        payload = {
            "words": [{"word": "hello", "start": 0.0, "end": 0.5}],
            "segments": [{"start": 0.0, "end": 0.5, "text": "hello"}],
            "face_map": FACE_MAP,
        }

        generate_clip = self._run_process(json.dumps(payload))

        self.assertEqual(generate_clip.call_args.kwargs["face_map"], FACE_MAP)

    def test_srt_transcript_recovers_cached_face_map(self):
        generate_clip = self._run_process(
            SRT, cached_transcript={"words": [], "segments": [], "face_map": FACE_MAP}
        )

        self.assertEqual(generate_clip.call_args.kwargs["face_map"], FACE_MAP)

    def test_srt_transcript_without_cache_degrades_to_no_face_map(self):
        generate_clip = self._run_process(SRT, cached_transcript=None)

        self.assertIsNone(generate_clip.call_args.kwargs["face_map"])

    def test_json_transcript_face_map_wins_over_cache(self):
        payload = {
            "words": [{"word": "hello", "start": 0.0, "end": 0.5}],
            "segments": [{"start": 0.0, "end": 0.5, "text": "hello"}],
            "face_map": FACE_MAP,
        }
        stale = {"words": [], "segments": [], "face_map": {"is_split_screen": False}}

        generate_clip = self._run_process(json.dumps(payload), cached_transcript=stale)

        self.assertEqual(generate_clip.call_args.kwargs["face_map"], FACE_MAP)

    def _cached_whispercpp(self):
        return {
            "engine": "whispercpp",
            "words": [{"word": "hello", "start": 0.0, "end": 0.5, "speaker": None}],
            "segments": [{"start": 0.0, "end": 0.5, "text": "hello"}],
            "diarization_warning": "Speaker detection disabled",
        }

    def test_whispercpp_run_says_speakers_need_whisper_py_and_a_token(self):
        self._run_process(None, cached_transcript=self._cached_whispercpp(), use_cache=True)

        note = "Speaker detection needs the whisper-py engine plus HF_TOKEN. Crop will use face tracking."
        self.assertEqual(self.stdout.count(note), 1)
        self.assertLess(self.stdout.index("1 segments, 1 words"), self.stdout.index(note))

    def test_whispercpp_run_with_no_speakers_prints_no_note(self):
        self._run_process(
            None, cached_transcript=self._cached_whispercpp(), use_cache=True, no_speakers=True
        )

        self.assertNotIn("whisper-py engine", self.stdout)

    def test_a_diarization_failure_prints_once_with_the_fix(self):
        cached = self._cached_whispercpp()
        cached["engine"] = "whisper-py"
        cached["diarization_warning"] = (
            "HF_TOKEN not set, so speakers are not detected. Fix: podcli env set HF_TOKEN hf_..."
        )

        self._run_process(None, cached_transcript=cached, use_cache=True)

        self.assertEqual(self.stdout.count("podcli env set HF_TOKEN hf_..."), 1)

    def test_summary_reports_the_crop_strategy_each_clip_used(self):
        self._run_process(
            SRT, generated_extra={"crop_strategy_used": "speaker"}
        )

        self.assertIn("Crop used: 1 speaker", self.stdout)


if __name__ == "__main__":
    unittest.main()
