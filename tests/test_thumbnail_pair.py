"""The two-person thumbnail layout.

Frames are synthetic: a red person sits left and a blue person sits right, each
with a textured face patch so the sharpness scoring has something to read. The
face detector is stubbed to report where those patches are, which is the one
thing a drawn frame cannot give a real detector.
"""

import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

import numpy as np

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BACKEND_ROOT = os.path.join(ROOT, "backend")
if BACKEND_ROOT not in sys.path:
    sys.path.insert(0, BACKEND_ROOT)

import cv2

from services import multicam
from services import thumbnail_ai
from services import thumbnail_html as th
from services import thumbnail_pair as tp

W, H = 1920, 1080
LEFT_X, RIGHT_X, FACE_Y, FACE = 500, 1400, 420, 200
PANEL = (540, 1344)
RED, BLUE = 2, 0  # BGR channel index


def _person_frame(people=(("left", LEFT_X, RED), ("right", RIGHT_X, BLUE)), seed=0):
    rng = np.random.default_rng(seed)
    frame = np.full((H, W, 3), 90, dtype=np.uint8)
    for _, x, channel in people:
        stripe = frame[:, x - 300:x + 300]
        stripe[:] = 20
        stripe[:, :, channel] = 200
        patch = frame[FACE_Y - FACE // 2:FACE_Y + FACE // 2, x - FACE // 2:x + FACE // 2]
        patch[:, :, channel] = rng.integers(120, 255, size=patch.shape[:2], dtype=np.uint8)
    return frame


def _face(x, confidence=0.9):
    return {"cx": x, "cy": FACE_Y, "fw": FACE, "fh": FACE, "confidence": confidence}


def _dominant(path) -> int:
    image = cv2.imread(path)
    return int(np.argmax(image.reshape(-1, 3).mean(axis=0)))


class SeatPairTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.video = os.path.join(self.tmp, "episode.mp4")
        open(self.video, "wb").close()
        self.frames = [(float(t), _person_frame(seed=t)) for t in range(1, 7)]

    def _pick(self, faces_for, **kw):
        with mock.patch.object(tp, "_sample_frames", return_value=iter(self.frames)), \
             mock.patch.object(tp, "_find_faces", side_effect=faces_for), \
             mock.patch.object(multicam, "session_for_render", return_value=None):
            return tp.pick_pair(self.video, 0.0, 8.0, os.path.join(self.tmp, "pair"), PANEL, **kw)

    def _both(self, frame):
        # The left person is clearest at 3 s, the right one at 5 s.
        t = next(t for t, f in self.frames if f is frame)
        return [_face(LEFT_X, 0.99 if t == 3.0 else 0.7), _face(RIGHT_X, 0.99 if t == 5.0 else 0.7)]

    def test_places_two_crops_left_and_right_from_their_own_frames(self):
        result = self._pick(self._both)
        self.assertEqual(result["layout"], "pair")
        left, right = result["people"]
        self.assertEqual((left["side"], right["side"]), ("left", "right"))
        self.assertEqual(_dominant(left["path"]), RED)
        self.assertEqual(_dominant(right["path"]), BLUE)
        self.assertEqual(cv2.imread(left["path"]).shape[:2], (PANEL[1], PANEL[0]))
        self.assertEqual((left["source_time"], right["source_time"]), (3.0, 5.0))
        self.assertEqual(result["roles"], "footage_order")

    def test_swap_flips_the_sides(self):
        result = self._pick(self._both, swap=True)
        left, right = result["people"]
        self.assertEqual(_dominant(left["path"]), BLUE)
        self.assertEqual(_dominant(right["path"]), RED)
        self.assertEqual(left["source_time"], 5.0)
        self.assertTrue(result["swapped"])

    def test_the_speaker_who_talks_more_goes_left_as_the_guest(self):
        face_map = {"clusters": [{"center_x": LEFT_X}, {"center_x": RIGHT_X}],
                    "speaker_mappings": {"A": 0, "B": 1}}
        segments = [{"start": 0.0, "end": 2.0, "speaker": "A"}, {"start": 2.0, "end": 8.0, "speaker": "B"}]
        result = self._pick(self._both, face_map=face_map, segments=segments)
        left, right = result["people"]
        self.assertEqual((left["role"], right["role"]), ("guest", "host"))
        self.assertEqual(_dominant(left["path"]), BLUE)
        self.assertEqual(result["roles"], "talk_time")

    def test_falls_back_to_one_face_when_only_one_person_is_on_screen(self):
        result = self._pick(lambda frame: [_face(LEFT_X)])
        self.assertEqual(result["layout"], "single")
        self.assertEqual(result["people"], [])
        self.assertIn("Only one person", result["reason"])
        self.assertIn("single-face layout", result["reason"])

    def test_falls_back_without_a_source_video(self):
        result = tp.pick_pair(None, 0.0, 8.0, self.tmp, PANEL)
        self.assertEqual(result["layout"], "single")


class ImagePairTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.red = os.path.join(self.tmp, "red.png")
        self.blue = os.path.join(self.tmp, "blue.png")
        cv2.imwrite(self.red, _person_frame(people=(("left", 960, RED),)))
        cv2.imwrite(self.blue, _person_frame(people=(("left", 960, BLUE),)))

    def _pick(self, **kw):
        with mock.patch.object(tp, "_find_faces", return_value=[]):
            return tp.pick_pair(None, None, None, os.path.join(self.tmp, "pair"), PANEL, **kw)

    def test_named_images_take_their_sides(self):
        result = self._pick(left_image=self.red, right_image=self.blue)
        left, right = result["people"]
        self.assertEqual(_dominant(left["path"]), RED)
        self.assertEqual(_dominant(right["path"]), BLUE)
        self.assertIsNone(left["source_time"])
        self.assertEqual(result["roles"], "chosen")

    def test_swap_flips_named_images(self):
        left, right = self._pick(left_image=self.red, right_image=self.blue, swap=True)["people"]
        self.assertEqual(_dominant(left["path"]), BLUE)
        self.assertEqual(_dominant(right["path"]), RED)

    def test_one_image_alone_is_refused(self):
        with self.assertRaises(ValueError):
            self._pick(left_image=self.red)


class MulticamPairTests(unittest.TestCase):
    def test_each_person_comes_from_their_own_camera_guest_left(self):
        tmp = tempfile.mkdtemp()
        video = os.path.join(tmp, "render.mp4")
        open(video, "wb").close()
        session = SimpleNamespace(people=[multicam.Person("host", "Nika", "host"),
                                          multicam.Person("guest", "Ana", "guest")])
        colour = {"host": RED, "guest": BLUE}

        def still(_session, person_id, tl, out, width=1280):
            cv2.imwrite(str(out), _person_frame(people=(("only", 960, colour[person_id]),)))
            return {"path": str(out), "camera": f"/cams/{person_id}.mov", "camera_time": round(tl - 90, 3)}

        with mock.patch.object(multicam, "session_for_render", return_value=session), \
             mock.patch.object(multicam, "render_to_timeline", side_effect=lambda s, t: t + 100), \
             mock.patch.object(multicam, "person_still", side_effect=still), \
             mock.patch.object(tp, "_find_faces", return_value=[_face(960)]):
            result = tp.pick_pair(video, 10.0, 30.0, os.path.join(tmp, "pair"), PANEL)

        self.assertEqual(result["roles"], "multicam")
        left, right = result["people"]
        self.assertEqual((left["role"], left["name"]), ("guest", "Ana"))
        self.assertEqual(_dominant(left["path"]), BLUE)
        self.assertEqual(_dominant(right["path"]), RED)
        self.assertEqual(left["camera"], "/cams/guest.mov")
        self.assertTrue(10.0 <= left["source_time"] <= 30.0)
        self.assertAlmostEqual(left["camera_time"], left["source_time"] + 10, places=1)


class RenderToTimelineTests(unittest.TestCase):
    def test_skips_removed_stretches(self):
        session = multicam.MulticamSession(
            session_id="abc123abc999", name="ep",
            cuts=[{"start": 10.0, "end": 40.0, "source_id": "a"}],
            removals=[{"start": 15.0, "end": 20.0}],
        )
        self.assertEqual(multicam.render_to_timeline(session, 2.0), 12.0)
        self.assertEqual(multicam.render_to_timeline(session, 6.0), 21.0)
        self.assertIsNone(multicam.render_to_timeline(session, 26.0))


class PairHtmlTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.left = os.path.join(self.tmp, "a.jpg")
        self.right = os.path.join(self.tmp, "b.jpg")
        for p in (self.left, self.right):
            cv2.imwrite(p, np.zeros((10, 10, 3), dtype=np.uint8))

    def test_two_panels_replace_the_single_photo(self):
        people = [{"side": "left", "path": self.left}, {"side": "right", "path": self.right}]
        html = th._build_html("Line one", "Line two", photo_path=self.left, people=people,
                              config={"pair_box_y": "86%"})
        self.assertIn(f'<div class="pair pair-left"><img src="{Path(self.left).as_uri()}"', html)
        self.assertIn(f'<div class="pair pair-right"><img src="{Path(self.right).as_uri()}"', html)
        self.assertIn('class="pair-divider"', html)
        self.assertNotIn('class="photo"', html)
        self.assertIn("top: 86%", html)

    def test_a_missing_panel_draws_the_single_photo(self):
        people = [{"side": "left", "path": self.left}, {"side": "right", "path": "/nope.jpg"}]
        html = th._build_html("Line one", "Line two", photo_path=self.left, people=people)
        self.assertIn('class="photo"', html)
        self.assertNotIn("pair-left", html)

    def test_panel_size_follows_the_canvas(self):
        self.assertEqual(th.pair_panel_size({"width": 1080, "height": 1920, "pair_photo_height": "70%"}), (540, 1344))


class RenderVariationsPairTests(unittest.TestCase):
    def test_pair_layout_reaches_every_variation(self):
        tmp = tempfile.mkdtemp()
        pair = {"layout": "pair", "roles": "footage_order", "swapped": False,
                "people": [{"side": "left", "path": "l.jpg", "source_time": 3.0},
                           {"side": "right", "path": "r.jpg", "source_time": 5.0}]}
        with mock.patch.object(tp, "pick_pair", return_value=pair) as pick, \
             mock.patch.object(thumbnail_ai, "generate_thumbnail_with_template",
                               side_effect=lambda **kw: kw["output_path"]) as draw:
            out = thumbnail_ai.render_variations(
                "Title", tmp, video_path="v.mp4", start_second=0.0, end_second=8.0,
                config={"variations": 2, "layout": "pair"}, line1="One", line2="Two",
                grounding={"payoff": "You learn X"},
            )
        self.assertEqual(len(out["paths"]), 2)
        self.assertIs(out["pair"], pair)
        self.assertEqual(pick.call_args.args[1:3], (0.0, 8.0))
        for call in draw.call_args_list:
            self.assertEqual(call.kwargs["people"], pair["people"])
            self.assertEqual(call.kwargs["grounding"], {"payoff": "You learn X"})

    def test_single_layout_never_looks_for_two_people(self):
        with mock.patch.object(tp, "pick_pair") as pick:
            self.assertIsNone(thumbnail_ai.resolve_pair({"layout": "single"}, tempfile.mkdtemp(), "v.mp4", 0.0, 8.0))
        pick.assert_not_called()


class ThumbnailRenderCliTests(unittest.TestCase):
    def test_result_records_each_face_and_its_source_time(self):
        import argparse
        import io
        from contextlib import redirect_stdout

        import cli as cli_mod

        tmp = tempfile.mkdtemp()
        pair = {"layout": "pair", "roles": "talk_time", "swapped": False, "people": [
            {"side": "left", "role": "guest", "from": "seats", "path": "l.jpg", "source_time": 12.5},
            {"side": "right", "role": "host", "from": "seats", "path": "r.jpg", "source_time": 31.0},
        ]}
        args = argparse.Namespace(
            title="Title", frame=None, output=os.path.join(tmp, "t.png"), line1="One", line2="Two",
            frame_info=None, logo=None, payoff=None, context_line=None, preview_text=None,
            video="v.mp4", start=10.0, end=40.0, layout="pair", left_image=None, right_image=None, swap=False,
        )
        out = io.StringIO()
        with mock.patch.object(tp, "pick_pair", return_value=pair), \
             mock.patch.object(cli_mod, "_cached_transcript", return_value={}), \
             mock.patch.object(thumbnail_ai, "generate_thumbnail_with_template", return_value=args.output) as draw, \
             redirect_stdout(out):
            cli_mod.cmd_thumbnail_render(args)
        result = json.loads(out.getvalue().strip().splitlines()[-1])
        self.assertEqual(result["layout"], "pair")
        self.assertEqual([(p["side"], p["source_time"]) for p in result["people"]], [("left", 12.5), ("right", 31.0)])
        self.assertEqual(draw.call_args.kwargs["people"], pair["people"])

    def test_fallback_without_a_frame_says_why(self):
        import argparse

        import cli as cli_mod

        args = argparse.Namespace(
            title="Title", frame=None, output="/tmp/t.png", line1=None, line2=None, frame_info=None, logo=None,
            payoff=None, context_line=None, preview_text=None, video="v.mp4", start=1.0, end=9.0,
            layout="pair", left_image=None, right_image=None, swap=False,
        )
        single = {"layout": "single", "people": [], "reason": "Only one person is on screen in this clip."}
        with mock.patch.object(tp, "pick_pair", return_value=single), \
             mock.patch.object(cli_mod, "_cached_transcript", return_value={}), \
             mock.patch("builtins.print") as printed, \
             self.assertRaises(SystemExit):
            cli_mod.cmd_thumbnail_render(args)
        self.assertIn("Only one person", printed.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
