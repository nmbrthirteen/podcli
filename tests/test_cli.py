import argparse
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
    )


class CliTranscriptTests(unittest.TestCase):
    def _run_process(self, transcript_text, cached_transcript=None):
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
                transcript_file.write(transcript_text)

            generated = {
                "output_path": os.path.join(output_dir, "test-clip.mp4"),
                "file_size_mb": 1.0,
            }

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
            ):
                cli_mod.cmd_process(_process_args(video_path, transcript_path, output_dir))

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


if __name__ == "__main__":
    unittest.main()
