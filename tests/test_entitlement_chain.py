import json
import os
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from services import ai_provider, podcli_cloud  # noqa: E402


class EntitlementTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        patcher = mock.patch.dict(podcli_cloud.paths, {"home": self.tmp})
        patcher.start()
        self.addCleanup(patcher.stop)
        # PODCLI_TOKEN would shadow the file these tests are about.
        env = mock.patch.dict(os.environ, {"PODCLI_TOKEN": "", "PODCLI_AI_PROVIDER": ""})
        env.start()
        self.addCleanup(env.stop)

    def write_auth(self, **fields):
        with open(os.path.join(self.tmp, "auth.json"), "w", encoding="utf-8") as fh:
            json.dump({"token": "t", "workspace_id": "w", **fields}, fh)

    def test_unknown_plan_still_tries_the_cloud(self):
        self.write_auth()
        self.assertTrue(podcli_cloud.entitled())

    def test_free_plan_is_not_entitled(self):
        self.write_auth(plan="free", plan_checked_at=time.time())
        self.assertFalse(podcli_cloud.entitled())

    def test_paid_plan_is_entitled(self):
        for plan in ("pro", "team", "studio"):
            with self.subTest(plan=plan):
                self.write_auth(plan=plan, plan_checked_at=time.time())
                self.assertTrue(podcli_cloud.entitled())

    def test_a_stale_free_verdict_is_retried(self):
        # A subscription bought after the last check must work without re-login.
        self.write_auth(plan="free",
                        plan_checked_at=time.time() - podcli_cloud.PLAN_TTL_SECONDS - 1)
        self.assertTrue(podcli_cloud.entitled())

    def test_remember_plan_keeps_the_token(self):
        self.write_auth()
        podcli_cloud.remember_plan("pro")
        with open(os.path.join(self.tmp, "auth.json"), encoding="utf-8") as fh:
            data = json.load(fh)
        self.assertEqual(data["token"], "t")
        self.assertEqual(data["workspace_id"], "w")
        self.assertEqual(data["plan"], "pro")

    def test_free_workspace_skips_the_cloud_leg(self):
        self.write_auth(plan="free", plan_checked_at=time.time())
        with mock.patch.object(ai_provider.ai_cli, "_find_ai_cli_candidates",
                               return_value=[("/bin/claude", "claude")]):
            chain = ai_provider._chain()
        self.assertEqual([kind for kind, _, _ in chain], ["cli"])

    def test_paid_workspace_puts_the_cloud_first(self):
        self.write_auth(plan="pro", plan_checked_at=time.time())
        with mock.patch.object(ai_provider.ai_cli, "_find_ai_cli_candidates",
                               return_value=[("/bin/claude", "claude")]):
            chain = ai_provider._chain()
        self.assertEqual([kind for kind, _, _ in chain], ["cloud", "cli"])

    def test_forced_cloud_mode_ignores_a_free_verdict(self):
        self.write_auth(plan="free", plan_checked_at=time.time())
        with mock.patch.dict(os.environ, {"PODCLI_AI_PROVIDER": "cloud"}):
            chain = ai_provider._chain()
        self.assertEqual([kind for kind, _, _ in chain], ["cloud"])


if __name__ == "__main__":
    unittest.main()
