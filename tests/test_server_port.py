"""Studio port resolution — must stay identical to src/config/server.ts."""

import os
import re
import sys
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BACKEND_ROOT = os.path.join(ROOT, "backend")
if BACKEND_ROOT not in sys.path:
    sys.path.insert(0, BACKEND_ROOT)

from config.server import DEFAULT_PORT, resolve_web_server_port, web_server_url


class WebServerPortTests(unittest.TestCase):
    def test_defaults_to_3847(self):
        self.assertEqual(DEFAULT_PORT, 3847)
        self.assertEqual(resolve_web_server_port({}), 3847)

    def test_reads_podcli_port_first(self):
        self.assertEqual(resolve_web_server_port({"PODCLI_PORT": "4000", "PORT": "5000"}), 4000)

    def test_falls_back_to_port(self):
        self.assertEqual(resolve_web_server_port({"PORT": "5000"}), 5000)

    def test_rejects_garbage_and_out_of_range(self):
        for raw in ("banana", "", "0", "70000", "-1"):
            self.assertEqual(resolve_web_server_port({"PORT": raw}), 3847, raw)

    def test_url_uses_resolved_port(self):
        self.assertEqual(web_server_url({"PODCLI_PORT": "4000"}), "http://localhost:4000")

    def test_typescript_resolver_agrees(self):
        ts = open(os.path.join(ROOT, "src", "config", "server.ts"), encoding="utf-8").read()
        self.assertIn("env.PODCLI_PORT || env.PORT", ts)
        self.assertTrue(re.search(r":\s*3847\s*;", ts))


if __name__ == "__main__":
    unittest.main()
