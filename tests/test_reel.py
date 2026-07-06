"""Tests for backend.services.reel session + edit logic (no video/render)."""

import os
import sys
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BACKEND_ROOT = os.path.join(ROOT, "backend")
if BACKEND_ROOT not in sys.path:
    sys.path.insert(0, BACKEND_ROOT)

from services import reel as R


def _session(**kw):
    return R.ReelSession(
        session_id=kw.get("sid", "unittest_reel"),
        source="/tmp/fake.mp4",
        profile="party",
        out_dir="/tmp/fake_out",
        moments=[R.Moment(100.0, 120.0, "energy_peak", "one"),
                 R.Moment(200.0, 215.0, "reaction", "two")],
    )


class MomentTests(unittest.TestCase):
    def test_duration(self):
        self.assertEqual(R.Moment(10.0, 25.0).duration, 15.0)


class EditTests(unittest.TestCase):
    def setUp(self):
        self.s = _session()

    def tearDown(self):
        p = R.session_path(self.s.session_id)
        if os.path.exists(p):
            os.remove(p)

    def test_longer_extends_end_and_marks_dirty(self):
        R.edit_moment(self.s, 1, "longer", 10)
        self.assertEqual(self.s.moments[0].end, 130.0)
        self.assertTrue(self.s.moments[0].dirty)

    def test_shorter_pulls_end_in(self):
        R.edit_moment(self.s, 1, "shorter", 5)
        self.assertEqual(self.s.moments[0].end, 115.0)

    def test_earlier_moves_start_back(self):
        R.edit_moment(self.s, 1, "earlier", 8)
        self.assertEqual(self.s.moments[0].start, 92.0)

    def test_shift_moves_both_bounds(self):
        R.edit_moment(self.s, 2, "shift", -20)
        self.assertEqual(self.s.moments[1].start, 180.0)
        self.assertEqual(self.s.moments[1].end, 195.0)

    def test_drop_removes_moment(self):
        R.edit_moment(self.s, 1, "drop")
        self.assertEqual(len(self.s.moments), 1)
        self.assertEqual(self.s.moments[0].text, "two")

    def test_drop_marks_shifted_moments_dirty(self):
        for m in self.s.moments:
            m.dirty = False
        R.edit_moment(self.s, 1, "drop")
        # The moment that slid into position 1 must re-cut, else it reuses the
        # dropped moment's clip file.
        self.assertTrue(self.s.moments[0].dirty)

    def test_toggle_disables_without_removing(self):
        R.edit_moment(self.s, 1, "toggle")
        self.assertFalse(self.s.moments[0].enabled)
        self.assertEqual(len(self.s.moments), 2)

    def test_bad_index_raises(self):
        with self.assertRaises(IndexError):
            R.edit_moment(self.s, 99, "longer", 5)

    def test_unknown_op_raises(self):
        with self.assertRaises(ValueError):
            R.edit_moment(self.s, 1, "flip", 5)


class PersistenceTests(unittest.TestCase):
    def tearDown(self):
        p = R.session_path("roundtrip_reel")
        if os.path.exists(p):
            os.remove(p)

    def test_save_and_load_roundtrip(self):
        s = _session(sid="roundtrip_reel")
        s.save()
        loaded = R.ReelSession.load("roundtrip_reel")
        self.assertEqual(loaded.session_id, "roundtrip_reel")
        self.assertEqual(len(loaded.moments), 2)
        self.assertEqual(loaded.moments[1].why, "reaction")
        self.assertIsInstance(loaded.moments[0], R.Moment)

    def test_format_defaults_to_horizontal(self):
        s = _session(sid="roundtrip_reel")
        s.save()
        self.assertEqual(R.ReelSession.load("roundtrip_reel").format, "horizontal")


class FormatTests(unittest.TestCase):
    def test_horizontal_pads_to_1920x1080(self):
        vf = R._scale_filter("horizontal")
        self.assertIn("1920:1080", vf)
        self.assertIn("pad=", vf)

    def test_vertical_crops_to_fill(self):
        vf = R._scale_filter("vertical")
        self.assertIn("1080:1920", vf)
        self.assertIn("crop=1080:1920", vf)

    def test_square_crops_to_fill(self):
        vf = R._scale_filter("square")
        self.assertIn("crop=1080:1080", vf)


class SessionRegistryTests(unittest.TestCase):
    _ids = ("registry_a", "registry_b")

    def tearDown(self):
        for sid in self._ids:
            p = R.session_path(sid)
            if os.path.exists(p):
                os.remove(p)

    def test_list_and_delete(self):
        for sid in self._ids:
            _session(sid=sid).save()
        listed = {s["session_id"] for s in R.list_sessions()}
        self.assertTrue(set(self._ids) <= listed)
        summary = next(s for s in R.list_sessions() if s["session_id"] == "registry_a")
        self.assertEqual(summary["moment_count"], 2)
        self.assertEqual(summary["enabled_count"], 2)
        self.assertTrue(R.delete_session("registry_a"))
        self.assertFalse(R.delete_session("registry_a"))
        self.assertNotIn("registry_a", {s["session_id"] for s in R.list_sessions()})


if __name__ == "__main__":
    unittest.main()
