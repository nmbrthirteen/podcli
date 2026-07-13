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


class CliTranscriptTests(unittest.TestCase):
    def test_process_passes_json_transcript_face_map_to_clip_generator(self):
        face_map = {
            "is_split_screen": True,
            "is_mixed_layout": True,
            "clusters": [{"center_x": 1148}, {"center_x": 2949}],
        }
        payload = {
            "words": [{"word": "hello", "start": 0.0, "end": 0.5}],
            "segments": [{"start": 0.0, "end": 0.5, "text": "hello"}],
            "face_map": face_map,
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            video_path = os.path.join(temp_dir, "episode.mp4")
            transcript_path = os.path.join(temp_dir, "transcript.json")
            output_dir = os.path.join(temp_dir, "clips")
            with open(video_path, "wb") as video_file:
                video_file.write(b"video")
            with open(transcript_path, "w", encoding="utf-8") as transcript_file:
                json.dump(payload, transcript_file)

            args = argparse.Namespace(
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
            clip = {
                "title": "Test clip",
                "start_second": 0.0,
                "end_second": 0.5,
                "duration": 0.5,
            }
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
                cli_mod.cmd_process(args)

        generate_clip.assert_called_once()
        self.assertEqual(generate_clip.call_args.kwargs["face_map"], face_map)


if __name__ == "__main__":
    unittest.main()
