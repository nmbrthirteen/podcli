import json
import os
import sys
import unittest


ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BACKEND_ROOT = os.path.join(ROOT, "backend")
if BACKEND_ROOT not in sys.path:
    sys.path.insert(0, BACKEND_ROOT)

from cli import _parse_json_transcript


class CliTranscriptTests(unittest.TestCase):
    def test_json_transcript_preserves_face_map(self):
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

        words, segments, result = _parse_json_transcript(json.dumps(payload))

        self.assertEqual(words, payload["words"])
        self.assertEqual(segments, payload["segments"])
        self.assertEqual(result["face_map"], face_map)


if __name__ == "__main__":
    unittest.main()
