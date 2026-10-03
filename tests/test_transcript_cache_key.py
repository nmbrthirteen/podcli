"""Tests for the Python port of src/services/transcript-cache.ts's keySuffix
(cache_key_suffix), and the readers/writers built on it: this is what keeps a
transcript written by one side of the TS/Python boundary visible to the
other, for any (engine, model, language) combo, not just the base/auto
default the two sides already agreed on before model/language were tracked.
"""

import json
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BACKEND_ROOT = os.path.join(ROOT, "backend")
if BACKEND_ROOT not in sys.path:
    sys.path.insert(0, BACKEND_ROOT)

from services import transcript_packer as tp


class CacheKeySuffixTests(unittest.TestCase):
    def setUp(self):
        self._saved = os.environ.get("PODCLI_ENGINE")
        os.environ.pop("PODCLI_ENGINE", None)

    def tearDown(self):
        if self._saved is None:
            os.environ.pop("PODCLI_ENGINE", None)
        else:
            os.environ["PODCLI_ENGINE"] = self._saved

    def test_base_model_and_auto_language_contribute_nothing(self):
        self.assertEqual(tp.cache_key_suffix("whisper-py", "base", "auto"), "")
        self.assertEqual(tp.cache_key_suffix("whisper-py", None, None), "")

    def test_non_base_model_gets_its_own_suffix(self):
        self.assertEqual(tp.cache_key_suffix("whisper-py", "small", None), "-msmall")

    def test_non_auto_language_gets_its_own_suffix(self):
        self.assertEqual(tp.cache_key_suffix("whisper-py", None, "ka"), "-lka")

    def test_engine_model_and_language_combine_in_order(self):
        self.assertEqual(
            tp.cache_key_suffix("whispercpp", "small", "ka"), "-whispercpp-msmall-lka"
        )

    def test_model_is_lowercased(self):
        self.assertEqual(tp.cache_key_suffix(None, "SMALL", None), "-msmall")

    def test_sanitize_language_strips_everything_outside_a_z0_9_dash(self):
        self.assertEqual(tp.sanitize_language("../../etc"), "etc")
        self.assertEqual(tp.sanitize_language("KA"), "ka")
        self.assertEqual(tp.sanitize_language(None), "")


class FindCachedTranscriptPathTests(unittest.TestCase):
    """The scanning fallback for a reader that only has a video path, not
    model/language, e.g. manage_reel or _cached_face_map."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._orig_dir = tp._transcripts_cache_dir
        tp._transcripts_cache_dir = lambda: self._tmp

    def tearDown(self):
        tp._transcripts_cache_dir = self._orig_dir
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _touch(self, name, mtime=None):
        path = os.path.join(self._tmp, name)
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"marker": name}, f)
        if mtime is not None:
            os.utime(path, (mtime, mtime))
        return path

    def test_prefers_the_engine_only_key_when_present(self):
        self._touch("abc123.json")
        self._touch("abc123-msmall-lka.json")
        found = tp.find_cached_transcript_path("abc123", engine=None)
        self.assertEqual(os.path.basename(found), "abc123.json")

    def test_falls_back_to_the_most_recently_modified_match(self):
        self._touch("abc123-msmall.json", mtime=1000)
        self._touch("abc123-mmedium-lka.json", mtime=2000)
        found = tp.find_cached_transcript_path("abc123", engine=None)
        self.assertEqual(os.path.basename(found), "abc123-mmedium-lka.json")

    def test_never_crosses_into_a_different_engines_files(self):
        # Only a whispercpp entry exists; a whisper-py (engine-only) lookup
        # must not adopt it.
        self._touch("abc123-whispercpp.json")
        found = tp.find_cached_transcript_path("abc123", engine=None)
        self.assertIsNone(found)

    def test_matches_only_this_hash_not_a_hash_with_extra_suffix(self):
        self._touch("abc123extra-msmall.json")
        found = tp.find_cached_transcript_path("abc123", engine=None)
        self.assertIsNone(found)

    def test_no_matches_returns_none(self):
        self.assertIsNone(tp.find_cached_transcript_path("nope", engine=None))


class LoadSaveCachedTranscriptModelLanguageTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._orig_dir = tp._transcripts_cache_dir
        tp._transcripts_cache_dir = lambda: self._tmp
        self._video = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False)
        self._video.write(b"fake video bytes for hashing")
        self._video.close()

    def tearDown(self):
        tp._transcripts_cache_dir = self._orig_dir
        shutil.rmtree(self._tmp, ignore_errors=True)
        os.unlink(self._video.name)

    def test_a_non_base_model_write_is_invisible_to_a_bare_read(self):
        # Before this fix, Python's engine-only key meant a whisper-py
        # "small" model write landed under the same bare filename as "base",
        # colliding instead of getting its own key.
        tp.save_cached_transcript_for_video(
            self._video.name, {"words": ["small"]}, engine="whisper-py", model="small"
        )
        tp.save_cached_transcript_for_video(
            self._video.name, {"words": ["base"]}, engine="whisper-py", model="base"
        )
        small = tp.load_cached_transcript_for_video(
            self._video.name, engine="whisper-py", model="small"
        )
        base = tp.load_cached_transcript_for_video(
            self._video.name, engine="whisper-py", model="base"
        )
        self.assertEqual(small["words"], ["small"])
        self.assertEqual(base["words"], ["base"])

    def test_a_request_for_an_unwritten_combo_misses_rather_than_substituting(self):
        tp.save_cached_transcript_for_video(
            self._video.name, {"words": ["small-ka"]}, engine="whisper-py", model="small", language="ka"
        )
        self.assertIsNone(
            tp.load_cached_transcript_for_video(
                self._video.name, engine="whisper-py", model="medium", language="fr"
            )
        )

    def test_unknown_model_language_reader_finds_the_transcript_the_session_used(self):
        tp.save_cached_transcript_for_video(
            self._video.name, {"words": ["small-ka"]}, engine="whisper-py", model="small", language="ka"
        )
        found = tp.load_cached_transcript_for_video(self._video.name, engine="whisper-py")
        self.assertEqual(found["words"], ["small-ka"])

    def test_save_writes_atomically_leaving_no_tmp_file_behind(self):
        path = tp.save_cached_transcript_for_video(self._video.name, {"words": []})
        self.assertTrue(os.path.exists(path))
        leftovers = [f for f in os.listdir(self._tmp) if f.endswith(".tmp")]
        self.assertEqual(leftovers, [])


if __name__ == "__main__":
    unittest.main()
