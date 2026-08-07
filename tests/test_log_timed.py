import os
import sys
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from backend.utils.log import timed


class TimedTests(unittest.TestCase):
    def test_reserved_field_does_not_mask_exception(self):
        with self.assertRaises(ValueError):
            with timed("crop", "detect", ms=1, message="collide"):
                raise ValueError("original")

    def test_reserved_extra_field_does_not_mask_exception(self):
        with self.assertRaises(ValueError):
            with timed("crop", "detect") as extra:
                extra["level"] = "warn"
                raise ValueError("original")

    def test_yields_dict_for_late_fields(self):
        with timed("crop", "detect") as extra:
            extra["frames"] = 12
        self.assertEqual(extra["frames"], 12)


if __name__ == "__main__":
    unittest.main()
