import os
import subprocess
import sys
import threading
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND_ROOT = os.path.join(ROOT, "backend")
if BACKEND_ROOT not in sys.path:
    sys.path.insert(0, BACKEND_ROOT)
import cli as cli_mod


class BackgroundJobTests(unittest.TestCase):
    def test_jobs_run_together_up_to_the_worker_limit_and_return_their_own_results(self):
        running = 0
        peak = 0
        lock = threading.Lock()

        def work(value):
            def run():
                nonlocal running, peak
                with lock:
                    running += 1
                    peak = max(peak, running)
                time.sleep(0.05)
                with lock:
                    running -= 1
                return value
            return run

        jobs = cli_mod._start_background_jobs({i: work(i * 10) for i in range(8)}, workers=3)

        self.assertEqual([jobs[i].result() for i in range(8)], [i * 10 for i in range(8)])
        self.assertEqual(peak, 3)

    def test_a_failure_is_raised_for_that_job_only(self):
        def boom():
            raise ValueError("no")

        jobs = cli_mod._start_background_jobs({"a": lambda: 1, "b": boom}, workers=2)

        self.assertEqual(jobs["a"].result(), 1)
        with self.assertRaises(ValueError):
            jobs["b"].result()

    def test_a_job_still_running_does_not_keep_the_process_alive(self):
        code = (
            "import sys, time\n"
            f"sys.path.insert(0, {BACKEND_ROOT!r})\n"
            "import cli\n"
            "cli._start_background_jobs({0: lambda: time.sleep(60)}, workers=1)\n"
            "time.sleep(0.2)\n"
        )
        started = time.time()
        subprocess.run([sys.executable, "-c", code], check=True, timeout=30, capture_output=True)

        self.assertLess(time.time() - started, 20)


if __name__ == "__main__":
    unittest.main()
