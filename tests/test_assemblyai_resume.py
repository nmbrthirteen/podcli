"""AssemblyAI: a restarted job must resume by polling the transcript it
already started, not re-upload the file and start a second paid job."""

import hashlib
import os
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BACKEND_ROOT = os.path.join(ROOT, "backend")
if BACKEND_ROOT not in sys.path:
    sys.path.insert(0, BACKEND_ROOT)

import services.transcription as tr
from services import transcribe_runs

_TEST_KEY_HASH = hashlib.sha256(b"test-key").hexdigest()[:8]
_MODEL_PART = f"diar0-spk0-us-{_TEST_KEY_HASH}"


class AssemblyAIResumeTests(unittest.TestCase):
    def setUp(self):
        self._tmp_cache = tempfile.mkdtemp()
        self._orig_run_dir = transcribe_runs.run_dir
        # Route every receipt into a throwaway directory for this test
        # instead of the real cache.
        transcribe_runs.run_dir = lambda cache_root, *a, **k: self._orig_run_dir(
            self._tmp_cache, *a, **k
        )
        self._tmp_file = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
        self._tmp_file.write(b"fake audio")
        self._tmp_file.close()
        self._saved_key = os.environ.get("ASSEMBLYAI_API_KEY")
        os.environ["ASSEMBLYAI_API_KEY"] = "test-key"

    def tearDown(self):
        transcribe_runs.run_dir = self._orig_run_dir
        os.unlink(self._tmp_file.name)
        import shutil
        shutil.rmtree(self._tmp_cache, ignore_errors=True)
        if self._saved_key is None:
            os.environ.pop("ASSEMBLYAI_API_KEY", None)
        else:
            os.environ["ASSEMBLYAI_API_KEY"] = self._saved_key

    def _completed_response(self):
        return {
            "status": "completed",
            "text": "hello world",
            "words": [],
            "audio_duration": 1.0,
            "language_code": "en",
        }

    def test_first_call_uploads_and_writes_a_receipt_before_polling(self):
        with mock.patch.object(tr, "_assemblyai_upload", return_value="https://upload/x") as upload, \
             mock.patch.object(
                 tr, "_assemblyai_json_request",
                 side_effect=[{"id": "tid-1"}, self._completed_response()],
             ) as req:
            result = tr._transcribe_with_assemblyai(
                self._tmp_file.name, language=None, enable_diarization=False,
                num_speakers=None, progress_callback=None,
            )
        upload.assert_called_once()
        self.assertEqual(result["transcript"], "hello world")
        # POST to create + GET to poll
        self.assertEqual(req.call_count, 2)
        self.assertEqual(req.call_args_list[0][0][0], "POST")

    def test_restarted_run_polls_the_saved_transcript_id_without_reuploading(self):
        directory = transcribe_runs.run_dir(
            "unused", self._tmp_file.name, "assemblyai", model_size=_MODEL_PART, language=None,
        )
        transcribe_runs.write_receipt(directory, "assemblyai.json", {"transcript_id": "tid-existing"})

        with mock.patch.object(tr, "_assemblyai_upload") as upload, \
             mock.patch.object(
                 tr, "_assemblyai_json_request", return_value=self._completed_response(),
             ) as req:
            result = tr._transcribe_with_assemblyai(
                self._tmp_file.name, language=None, enable_diarization=False,
                num_speakers=None, progress_callback=None,
            )
        upload.assert_not_called()
        self.assertEqual(result["transcript"], "hello world")
        # Only the poll GET, no POST to create a new transcript.
        self.assertEqual(req.call_count, 1)
        polled_url = req.call_args_list[0][0][1]
        self.assertIn("tid-existing", polled_url)

    def test_receipt_is_cleared_once_completed(self):
        directory = transcribe_runs.run_dir(
            "unused", self._tmp_file.name, "assemblyai", model_size=_MODEL_PART, language=None,
        )
        with mock.patch.object(tr, "_assemblyai_upload", return_value="https://upload/x"), \
             mock.patch.object(
                 tr, "_assemblyai_json_request",
                 side_effect=[{"id": "tid-2"}, self._completed_response()],
             ):
            tr._transcribe_with_assemblyai(
                self._tmp_file.name, language=None, enable_diarization=False,
                num_speakers=None, progress_callback=None,
            )
        self.assertIsNone(transcribe_runs.read_receipt(directory, "assemblyai.json"))

    def test_a_different_file_never_adopts_anothers_receipt(self):
        other = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
        other.write(b"different fake audio, different fingerprint")
        other.close()
        try:
            directory = transcribe_runs.run_dir(
                "unused", self._tmp_file.name, "assemblyai", model_size=_MODEL_PART, language=None,
            )
            transcribe_runs.write_receipt(directory, "assemblyai.json", {"transcript_id": "tid-not-mine"})

            with mock.patch.object(tr, "_assemblyai_upload", return_value="https://upload/x") as upload, \
                 mock.patch.object(
                     tr, "_assemblyai_json_request",
                     side_effect=[{"id": "tid-mine"}, self._completed_response()],
                 ):
                tr._transcribe_with_assemblyai(
                    other.name, language=None, enable_diarization=False,
                    num_speakers=None, progress_callback=None,
                )
            upload.assert_called_once()
        finally:
            os.unlink(other.name)

    def test_a_4xx_on_the_resume_get_clears_the_receipt_and_reuploads(self):
        directory = transcribe_runs.run_dir(
            "unused", self._tmp_file.name, "assemblyai", model_size=_MODEL_PART, language=None,
        )
        transcribe_runs.write_receipt(directory, "assemblyai.json", {"transcript_id": "tid-gone"})
        http_error = tr.AssemblyAIHTTPError("gone", status=404)

        with mock.patch.object(tr, "_assemblyai_upload", return_value="https://upload/x") as upload, \
             mock.patch.object(
                 tr, "_assemblyai_json_request",
                 side_effect=[http_error, {"id": "tid-fresh"}, self._completed_response()],
             ) as req:
            result = tr._transcribe_with_assemblyai(
                self._tmp_file.name, language=None, enable_diarization=False,
                num_speakers=None, progress_callback=None,
            )
        # The stale receipt's GET fails once, then a fresh upload + create +
        # poll succeeds, never retrying the dead transcript_id forever.
        upload.assert_called_once()
        self.assertEqual(result["transcript"], "hello world")
        self.assertEqual(req.call_args_list[0][0][0], "GET")
        self.assertIn("tid-gone", req.call_args_list[0][0][1])
        self.assertEqual(req.call_args_list[1][0][0], "POST")
        new_receipt = transcribe_runs.read_receipt(directory, "assemblyai.json")
        self.assertIsNone(new_receipt)  # cleared on completion, same as any other run

    def test_a_5xx_on_the_resume_get_does_not_clear_the_receipt(self):
        # A transient server error is retried by _assemblyai_json_request
        # itself; if it still fails after retries, the receipt must survive
        # so a later run can resume rather than paying for a new upload.
        directory = transcribe_runs.run_dir(
            "unused", self._tmp_file.name, "assemblyai", model_size=_MODEL_PART, language=None,
        )
        transcribe_runs.write_receipt(directory, "assemblyai.json", {"transcript_id": "tid-existing"})
        http_error = tr.AssemblyAIHTTPError("server error", status=503)

        with mock.patch.object(tr, "_assemblyai_upload") as upload, \
             mock.patch.object(tr, "_assemblyai_json_request", side_effect=http_error):
            with self.assertRaises(tr.AssemblyAIHTTPError):
                tr._transcribe_with_assemblyai(
                    self._tmp_file.name, language=None, enable_diarization=False,
                    num_speakers=None, progress_callback=None,
                )
        upload.assert_not_called()
        self.assertIsNotNone(transcribe_runs.read_receipt(directory, "assemblyai.json"))

    def test_region_and_key_are_part_of_the_receipt_key(self):
        # Two different API keys for the same file must never share a run
        # directory: resuming the wrong account's transcript_id would poll
        # (or worse, successfully return) someone else's job.
        dir_a = transcribe_runs.run_dir(
            "unused", self._tmp_file.name, "assemblyai",
            model_size=f"diar0-spk0-us-{hashlib.sha256(b'key-a').hexdigest()[:8]}", language=None,
        )
        dir_b = transcribe_runs.run_dir(
            "unused", self._tmp_file.name, "assemblyai",
            model_size=f"diar0-spk0-us-{hashlib.sha256(b'key-b').hexdigest()[:8]}", language=None,
        )
        self.assertNotEqual(dir_a, dir_b)


if __name__ == "__main__":
    unittest.main()
