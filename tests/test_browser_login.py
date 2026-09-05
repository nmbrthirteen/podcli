import io
import json
import os
import sys
import tempfile
import unittest
import urllib.error
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from services import podcli_cloud  # noqa: E402


def http_error(code, body):
    return urllib.error.HTTPError(
        "https://api.podcli.com", code, "err", {},
        io.BytesIO(json.dumps(body).encode("utf-8")))


class BrowserLoginTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        patcher = mock.patch.dict(podcli_cloud.paths, {"home": self.tmp})
        patcher.start()
        self.addCleanup(patcher.stop)
        env = mock.patch.dict(os.environ, {"PODCLI_TOKEN": ""})
        env.start()
        self.addCleanup(env.stop)

    def test_the_cli_never_offers_to_take_a_password(self):
        self.assertFalse(hasattr(podcli_cloud, "login"))

    def test_starting_asks_for_a_code_and_takes_no_credential(self):
        with mock.patch.object(podcli_cloud, "_unauthenticated") as call:
            call.return_value = {"deviceCode": "d", "userCode": "BCDF-2345"}
            podcli_cloud.start_cli_auth("nik@studio")

        method, path, body = call.call_args[0]
        self.assertEqual((method, path), ("POST", "/v1/auth/cli"))
        self.assertEqual(body, {"label": "nik@studio"})

    def test_a_pending_poll_is_not_a_session(self):
        with mock.patch.object(podcli_cloud, "_unauthenticated") as call:
            call.return_value = {"status": "pending", "interval": 3}
            self.assertIsNone(podcli_cloud.poll_cli_auth("d"))
        self.assertFalse(podcli_cloud.signed_in())

    def test_an_approved_poll_writes_the_session_to_disk(self):
        with mock.patch.object(podcli_cloud, "_unauthenticated") as call:
            call.return_value = {"token": "sess-abc", "workspaceId": "w1"}
            podcli_cloud.poll_cli_auth("d")

        self.assertEqual(podcli_cloud.read_token(), "sess-abc")
        self.assertEqual(podcli_cloud._auth_data()["workspace_id"], "w1")

    @unittest.skipIf(os.name == "nt", "Unix file modes are not enforced on Windows")
    def test_the_token_file_is_not_readable_by_other_users(self):
        with mock.patch.object(podcli_cloud, "_unauthenticated") as call:
            call.return_value = {"token": "sess-abc", "workspaceId": "w1"}
            podcli_cloud.poll_cli_auth("d")

        mode = os.stat(os.path.join(self.tmp, "auth.json")).st_mode
        self.assertEqual(mode & 0o077, 0)

    def test_a_denied_request_is_final_rather_than_worth_retrying(self):
        with mock.patch("urllib.request.urlopen",
                        side_effect=http_error(403, {"error": "sign-in was denied"})):
            with self.assertRaises(podcli_cloud.CloudError) as caught:
                podcli_cloud.poll_cli_auth("d")

        self.assertFalse(caught.exception.retryable)
        self.assertIn("denied", str(caught.exception))

    def test_being_rate_limited_while_waiting_is_worth_retrying(self):
        with mock.patch("urllib.request.urlopen",
                        side_effect=http_error(429, {"error": "slow down"})):
            with self.assertRaises(podcli_cloud.CloudError) as caught:
                podcli_cloud.poll_cli_auth("d")

        self.assertTrue(caught.exception.retryable)

    def test_a_dropped_connection_carries_no_status_to_give_up_on(self):
        with mock.patch("urllib.request.urlopen",
                        side_effect=urllib.error.URLError("connection reset")):
            with self.assertRaises(podcli_cloud.CloudError) as caught:
                podcli_cloud.poll_cli_auth("d")

        self.assertEqual(caught.exception.status, 0)


if __name__ == "__main__":
    unittest.main()
