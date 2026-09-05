import argparse
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from services import cloud_render, podcli_cloud  # noqa: E402


class FakePut:
    """Stands in for object storage, recording what was PUT where."""

    def __init__(self):
        self.puts = []

    def __call__(self, url, body, total, index, count):
        self.puts.append((url, len(body)))
        return f"etag-{index}"


class ParamsTests(unittest.TestCase):
    def test_only_what_the_worker_understands_is_sent(self):
        params = cloud_render._params({
            "top_clips": 6,
            "caption_style": "hormozi",
            "crop_strategy": "speaker",
            "format": "vertical",
            "logo_path": "/Users/nik/logo.png",
            "outro_path": "/Users/nik/outro.mp4",
        }, argparse.Namespace())

        self.assertEqual(params["topN"], 6)
        self.assertEqual(params["captionStyle"], "hormozi")
        self.assertEqual(params["crop"], "speaker")
        # A path on this disk means nothing to a machine that cannot see it.
        self.assertNotIn("logo_path", params)
        self.assertNotIn("outro_path", params)

    def test_keys_the_config_never_set_are_left_out_entirely(self):
        params = cloud_render._params({"top_clips": 3}, argparse.Namespace())
        self.assertEqual(set(params), {"topN"})

    def test_fast_mode_is_passed_through_as_a_draft(self):
        params = cloud_render._params({"fast_mode": True}, argparse.Namespace())
        self.assertTrue(params["fast"])


class UploadTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.video = os.path.join(self.tmp, "episode.mp4")
        with open(self.video, "wb") as fh:
            fh.write(b"x" * 2500)

    def test_a_small_file_goes_up_in_one_put(self):
        put = FakePut()
        with mock.patch.object(cloud_render, "_put", put), \
             mock.patch.object(podcli_cloud, "request") as api:
            api.return_value = {"sourceId": "s1", "uploadUrl": "https://store/put"}
            source_id = cloud_render._upload(self.video, 2500)

        self.assertEqual(source_id, "s1")
        self.assertEqual(put.puts, [("https://store/put", 2500)])
        # Completing a single PUT must not claim a part list it never made.
        self.assertEqual(api.call_args_list[-1][0][2], {})

    def test_a_large_file_goes_up_in_pieces_and_reports_their_etags(self):
        put = FakePut()
        with mock.patch.object(cloud_render, "_put", put), \
             mock.patch.object(podcli_cloud, "request") as api:
            api.return_value = {
                "sourceId": "s1",
                "multipart": {"partSize": 1000, "urls": ["u1", "u2", "u3"]},
            }
            cloud_render._upload(self.video, 2500)

        self.assertEqual([size for _, size in put.puts], [1000, 1000, 500])
        self.assertEqual(api.call_args_list[-1][0][2], {"parts": [
            {"partNumber": 1, "etag": "etag-1"},
            {"partNumber": 2, "etag": "etag-2"},
            {"partNumber": 3, "etag": "etag-3"},
        ]})

    def test_a_part_with_no_etag_stops_the_upload_rather_than_completing_it(self):
        with mock.patch.object(cloud_render, "_put", return_value=""), \
             mock.patch.object(podcli_cloud, "request") as api:
            api.return_value = {
                "sourceId": "s1",
                "multipart": {"partSize": 1000, "urls": ["u1", "u2", "u3"]},
            }
            with self.assertRaises(cloud_render.CloudRenderError):
                cloud_render._upload(self.video, 2500)

        # Only the opening call happened: nothing was marked complete.
        self.assertEqual(len(api.call_args_list), 1)


class FollowTests(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(cloud_render.time, "sleep")
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_a_finished_render_is_returned(self):
        with mock.patch.object(podcli_cloud, "request") as api:
            api.side_effect = [
                {"status": "running", "progress": 20, "stage": "transcribing"},
                {"status": "done", "progress": 100, "result": {"clips": 4}},
            ]
            job = cloud_render._follow("j1")

        self.assertEqual(job["result"]["clips"], 4)

    def test_a_failed_render_says_why(self):
        with mock.patch.object(podcli_cloud, "request") as api:
            api.return_value = {"status": "failed", "error": "no speech found"}
            with self.assertRaises(cloud_render.CloudRenderError) as caught:
                cloud_render._follow("j1")

        self.assertIn("no speech found", str(caught.exception))

    def test_a_bad_minute_does_not_abandon_an_hour_long_render(self):
        with mock.patch.object(podcli_cloud, "request") as api:
            api.side_effect = [
                podcli_cloud.CloudError("gateway", status=503, retryable=True),
                podcli_cloud.CloudError("connection reset"),
                {"status": "done", "progress": 100, "result": {"clips": 1}},
            ]
            job = cloud_render._follow("j1")

        self.assertEqual(job["status"], "done")

    def test_a_revoked_session_stops_the_wait(self):
        with mock.patch.object(podcli_cloud, "request") as api:
            api.side_effect = podcli_cloud.CloudError("session expired", status=401)
            with self.assertRaises(cloud_render.CloudRenderError):
                cloud_render._follow("j1")


class DownloadTests(unittest.TestCase):
    def setUp(self):
        self.out = tempfile.mkdtemp()

    def test_clips_land_in_the_output_folder_under_readable_names(self):
        with mock.patch.object(podcli_cloud, "request") as api, \
             mock.patch.object(cloud_render, "_download") as download:
            api.side_effect = [
                {"episodes": [{"id": "e1", "source_id": "s1"}]},
                {"clips": [
                    {"title": "The 3 rules of hiring", "video": "https://store/1"},
                    {"title": "Why it/works", "video": "https://store/2"},
                ]},
            ]
            saved = cloud_render._download_clips("s1", self.out)

        self.assertEqual(saved, 2)
        names = [os.path.basename(call[0][1]) for call in download.call_args_list]
        self.assertEqual(names, ["01_The_3_rules_of_hiring.mp4", "02_Why_it_works.mp4"])

    def test_a_clip_whose_video_has_not_landed_yet_is_skipped(self):
        with mock.patch.object(podcli_cloud, "request") as api, \
             mock.patch.object(cloud_render, "_download"):
            api.side_effect = [
                {"episodes": [{"id": "e1", "source_id": "s1"}]},
                {"clips": [{"title": "Pending", "video": None}]},
            ]
            self.assertEqual(cloud_render._download_clips("s1", self.out), 0)

    def test_another_upload_s_episode_is_not_mistaken_for_this_one(self):
        with mock.patch.object(podcli_cloud, "request") as api:
            api.return_value = {"episodes": [{"id": "e9", "source_id": "someone-else"}]}
            self.assertEqual(cloud_render._download_clips("s1", self.out), 0)


class GateTests(unittest.TestCase):
    def test_rendering_in_the_cloud_needs_a_session(self):
        with mock.patch.object(podcli_cloud, "signed_in", return_value=False):
            with self.assertRaises(cloud_render.CloudRenderError) as caught:
                cloud_render.run("/tmp/x.mp4", {}, "/tmp", argparse.Namespace())

        self.assertIn("podcli login", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
