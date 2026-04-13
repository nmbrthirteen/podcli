import os
import sys
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from backend.utils.proc import ProcError, run


class ProcTests(unittest.TestCase):
    def test_run_captures_stdout(self):
        result = run(["echo", "hello"], timeout=5)
        self.assertEqual(result.returncode, 0)
        self.assertIn("hello", result.stdout)

    def test_run_raises_on_nonzero_exit(self):
        with self.assertRaises(ProcError) as ctx:
            run(["sh", "-c", "echo bad 1>&2; exit 7"], timeout=5)
        self.assertEqual(ctx.exception.returncode, 7)
        self.assertIn("bad", ctx.exception.stderr)

    def test_run_check_false_returns_result(self):
        result = run(["sh", "-c", "exit 3"], timeout=5, check=False)
        self.assertEqual(result.returncode, 3)

    def test_run_timeout_raises_proc_error(self):
        with self.assertRaises(ProcError) as ctx:
            run(["sh", "-c", "sleep 2"], timeout=0.2)
        self.assertEqual(ctx.exception.returncode, -1)
        self.assertIn("timeout", str(ctx.exception).lower())

    def test_empty_cmd_rejected(self):
        with self.assertRaises(ValueError):
            run([], timeout=1)


if __name__ == "__main__":
    unittest.main()
