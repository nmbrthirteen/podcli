"""
Two people side by side on a thumbnail, each taken from the clip's own footage.

An interview clip is a conversation, and a thumbnail with one face hides half
of it. This picks one sharp, face-forward frame per person from inside the
clip and cuts each into a portrait panel: the guest on the left and the host
on the right, unless asked otherwise.

Who is who comes from what podcli already knows, in this order:
  1. images the caller names for each side;
  2. a multicam edit whose render is this video, where every person has a
     camera of their own and a role;
  3. two people seated in one shot, or two panes of a call recording, found
     the same way the face map finds seats.
When none of these tells two people apart, the result says so and the caller
draws the single-face layout. A panel is never filled with an invented or
repeated person.
"""

import os
import tempfile
from pathlib import Path
from typing import Iterator, Optional

from services.face_track_helpers import seats_from_frames
from services.thumbnail_ai import (
    EXPRESSION_FLOOR,
    SHARPNESS_FLOOR,
    _tile_bounds_for_face,
    face_portrait_score,
)

SAMPLES = 36

# A face belongs to the nearer seat only when it sits this close to it, as a
# share of the frame width. Further out it is someone walking through.
_SEAT_REACH = 0.15

# The face's share of the panel width, and where its centre sits down the panel.
_FACE_WIDTH_SHARE = 0.42
_FACE_HEIGHT_AT = 0.38


def _single(reason: str) -> dict:
    return {"layout": "single", "people": [], "reason": reason}


def _sample_frames(video_path: str, start: float, end: float, count: int) -> Iterator[tuple[float, object]]:
    """(source second, BGR frame) spread across the clip, clear of its edges."""
    import cv2

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        return
    try:
        margin = min(0.6, (end - start) * 0.08)
        a, b = start + margin, end - margin
        if b <= a:
            a, b = start, end
        for i in range(count):
            t = a + (i + 0.5) * (b - a) / count
            cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
            ok, frame = cap.read()
            if ok:
                yield round(t, 2), frame
    finally:
        cap.release()


_detectors: dict = {}


def _find_faces(frame) -> list[dict]:
    """Faces in one frame as [{cx, cy, fw, fh, confidence}], or [] without a detector."""
    from services.face_detector import create_detector, detect_faces

    h, w = frame.shape[:2]
    if (w, h) not in _detectors:
        _detectors[(w, h)] = create_detector(w, h)
    detector = _detectors[(w, h)]
    return detect_faces(detector, frame, w, h) if detector else []


def _candidate(frame, face: dict, t: Optional[float]) -> dict:
    h, w = frame.shape[:2]
    x1, y1 = max(0, face["cx"] - face["fw"] // 2), max(0, face["cy"] - face["fh"] // 2)
    x2, y2 = min(w, face["cx"] + face["fw"] // 2), min(h, face["cy"] + face["fh"] // 2)
    score, sharpness, expression = face_portrait_score(frame, x1, y1, x2, y2, face["confidence"])
    return {
        "frame": frame, "face": face, "time": t, "score": score,
        "clean": sharpness >= SHARPNESS_FLOOR and expression >= EXPRESSION_FLOOR,
    }


def _best(candidates: list[dict]) -> Optional[dict]:
    """The highest-scoring face, preferring one that is neither blurred nor turned away."""
    clean = [c for c in candidates if c["clean"]]
    pool = clean or candidates
    return max(pool, key=lambda c: c["score"]) if pool else None


def _panel(frame, face: dict, size: tuple[int, int]):
    """Cut a portrait of one face to the panel's shape. Returns (image, face position in %)."""
    import cv2

    pw, ph = size
    ratio = pw / ph
    h, w = frame.shape[:2]
    crop_w = min(w, max(int(face["fw"] / _FACE_WIDTH_SHARE), 2))
    crop_h = int(crop_w / ratio)
    if crop_h > h:
        crop_h = h
        crop_w = int(crop_h * ratio)

    # A call recording's panes are tiles. Staying inside this face's tile keeps
    # the next person's pane, and the seam between them, out of the portrait.
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    tile_left, tile_right = _tile_bounds_for_face(gray, face["cx"])
    if tile_right - tile_left < crop_w:
        crop_w = tile_right - tile_left
        crop_h = int(crop_w / ratio)
    x = max(tile_left, min(face["cx"] - crop_w // 2, tile_right - crop_w))
    y = max(0, min(face["cy"] - int(crop_h * _FACE_HEIGHT_AT), h - crop_h))

    cut = frame[y:y + crop_h, x:x + crop_w]
    interp = cv2.INTER_AREA if crop_w > pw else cv2.INTER_LANCZOS4
    image = cv2.resize(cut, (pw, ph), interpolation=interp)
    return image, {
        "face_x_pct": round((face["cx"] - x) / crop_w * 100, 1),
        "face_y_pct": round((face["cy"] - y) / crop_h * 100, 1),
        "face_w_pct": round(face["fw"] / crop_w * 100, 1),
        "face_h_pct": round(face["fh"] / crop_h * 100, 1),
    }


def _write_panel(pick: dict, size: tuple[int, int], path: str) -> dict:
    import cv2

    image, where = _panel(pick["frame"], pick["face"], size)
    if not cv2.imwrite(path, image, [cv2.IMWRITE_JPEG_QUALITY, 92]):
        raise OSError(f"could not write {path}")
    return {"path": path, **where}


def _from_images(left: str, right: str, size: tuple[int, int], out_dir: str) -> list[dict]:
    import cv2

    people = []
    for n, (side, src) in enumerate((("left", left), ("right", right)), start=1):
        frame = cv2.imread(src)
        if frame is None:
            raise ValueError(f"cannot read the {side} image: {src}")
        faces = _find_faces(frame)
        h, w = frame.shape[:2]
        # A portrait without a detectable face is still the person the caller
        # chose; it is framed from its centre instead.
        face = max(faces, key=lambda f: f["fw"]) if faces else {
            "cx": w // 2, "cy": h // 3, "fw": int(w * _FACE_WIDTH_SHARE), "fh": int(w * _FACE_WIDTH_SHARE),
        }
        panel = _write_panel({"frame": frame, "face": face}, size, os.path.join(out_dir, f"image_{n}.jpg"))
        people.append({"side": side, "role": None, "from": "image", "image": src, "source_time": None, **panel})
    return people


def _talk_seconds(segments: Optional[list[dict]], start: float, end: float) -> dict[str, float]:
    talk: dict[str, float] = {}
    for s in segments or []:
        sp = s.get("speaker")
        overlap = min(end, s.get("end", 0)) - max(start, s.get("start", 0))
        if sp and overlap > 0:
            talk[sp] = talk.get(sp, 0.0) + overlap
    return talk


def _seat_roles(face_map: Optional[dict], segments: Optional[list[dict]], start: float, end: float) -> Optional[list[str]]:
    """["guest", "host"] or the reverse for the left and right seats, or None when unknown.

    The face map ties each diarized speaker to a seat. Of the two people
    talking in the clip, the one who talks more is taken as the guest: an
    interview clip is built around the answer.
    """
    clusters = (face_map or {}).get("clusters") or []
    mapping = (face_map or {}).get("speaker_mappings") or {}
    if len(clusters) != 2 or not mapping:
        return None
    talk = sorted(_talk_seconds(segments, start, end).items(), key=lambda kv: kv[1], reverse=True)
    if len(talk) < 2:
        return None
    guest_seat, host_seat = mapping.get(talk[0][0]), mapping.get(talk[1][0])
    if guest_seat not in (0, 1) or host_seat not in (0, 1) or guest_seat == host_seat:
        return None
    return ["guest", "host"] if guest_seat == 0 else ["host", "guest"]


def _from_seats(video_path, start, end, size, out_dir, face_map, segments, samples) -> tuple[Optional[list[dict]], str]:
    frames = list(_sample_frames(video_path, start, end, samples))
    if not frames:
        return None, "podcli could not read frames from this clip."
    found = [(t, frame, _find_faces(frame)) for t, frame in frames]
    width = frames[0][1].shape[1]
    seats = seats_from_frames([[f["cx"] for f in faces] for _, _, faces in found], width)
    if seats is None:
        return None, "Only one person is on screen in this clip."

    by_seat: list[list[dict]] = [[], []]
    for t, frame, faces in found:
        for face in faces:
            gaps = [abs(face["cx"] - seat) for seat in seats]
            nearest = gaps.index(min(gaps))
            if gaps[nearest] <= width * _SEAT_REACH:
                by_seat[nearest].append(_candidate(frame, face, t))
    picks = [_best(c) for c in by_seat]
    if not all(picks):
        return None, "One of the two people never shows a clear face in this clip."

    roles = _seat_roles(face_map, segments, start, end) or [None, None]
    people = []
    for i, (side, pick) in enumerate(zip(("left", "right"), picks)):
        panel = _write_panel(pick, size, os.path.join(out_dir, f"seat_{side}.jpg"))
        people.append({"side": side, "role": roles[i], "from": "seats", "source_time": pick["time"], **panel})
    return people, ""


def _from_multicam(video_path, start, end, size, out_dir, samples) -> tuple[Optional[list[dict]], str]:
    import cv2
    from services import multicam

    session = multicam.session_for_render(video_path)
    if session is None:
        return None, ""
    count = max(4, samples // 4)
    times = [start + (i + 0.5) * (end - start) / count for i in range(count)]
    people = []
    with tempfile.TemporaryDirectory(prefix="podcli_pair_") as stills:
        for person in session.people:
            candidates = []
            for i, t in enumerate(times):
                tl = multicam.render_to_timeline(session, t)
                if tl is None:
                    continue
                still = multicam.person_still(session, person.id, tl, Path(stills) / f"{person.id}_{i}.jpg")
                frame = cv2.imread(still["path"]) if still else None
                if frame is None:
                    continue
                faces = _find_faces(frame)
                if faces:
                    pick = _candidate(frame, max(faces, key=lambda f: f["fw"]), round(t, 2))
                    candidates.append({**pick, "still": still})
            best = _best(candidates)
            if best:
                people.append((person, best))
    if len(people) < 2:
        return None, "The multicam edit behind this video shows fewer than two clear faces in this clip."

    guests = [p for p in people if p[0].role == "guest"]
    hosts = [p for p in people if p[0].role != "guest"]
    chosen = [guests[0], hosts[0]] if guests and hosts else people[:2]
    out = []
    for i, (person, pick) in enumerate(chosen):
        side = ("left", "right")[i]
        panel = _write_panel(pick, size, os.path.join(out_dir, f"camera_{side}.jpg"))
        out.append({
            "side": side, "role": person.role, "name": person.name, "from": "multicam",
            "source_time": pick["time"], "camera": pick["still"]["camera"],
            "camera_time": pick["still"]["camera_time"], **panel,
        })
    return out, ""


def _arrange(people: list[dict], swap: bool) -> list[dict]:
    """Guest left and host right when the roles are known; footage order when not. Then swap."""
    ordered = list(people)
    if [p.get("role") for p in ordered] == ["host", "guest"]:
        ordered.reverse()
    if swap:
        ordered.reverse()
    return [{**p, "side": side} for p, side in zip(ordered, ("left", "right"))]


def pick_pair(
    video_path: Optional[str],
    start_second: Optional[float],
    end_second: Optional[float],
    output_dir: str,
    panel_size: tuple[int, int],
    *,
    left_image: Optional[str] = None,
    right_image: Optional[str] = None,
    swap: bool = False,
    face_map: Optional[dict] = None,
    segments: Optional[list[dict]] = None,
    samples: int = SAMPLES,
) -> dict:
    """Two portrait panels for the pair layout, or the reason there are none.

    Returns {"layout": "pair", "people": [left, right], "roles": ...} where
    each person records the panel path, the source second its face came from,
    and how podcli knew who it was. Without two distinct people it returns
    {"layout": "single", "people": [], "reason": ...}.
    """
    os.makedirs(output_dir, exist_ok=True)

    if left_image or right_image:
        if not (left_image and right_image):
            raise ValueError("pass both a left and a right image, or neither")
        for path in (left_image, right_image):
            if not os.path.exists(path):
                raise ValueError(f"image not found: {path}")
        people = _arrange(_from_images(left_image, right_image, panel_size, output_dir), swap)
        return {"layout": "pair", "people": people, "roles": "chosen", "swapped": swap}

    if not video_path or not os.path.exists(video_path):
        return _single("the two-person layout needs the clip's source video")
    if start_second is None or end_second is None or end_second <= start_second:
        return _single("the two-person layout needs the clip's start and end")

    people, camera_reason = _from_multicam(video_path, start_second, end_second, panel_size, output_dir, samples)
    roles = "multicam"
    if people is None:
        people, seat_reason = _from_seats(
            video_path, start_second, end_second, panel_size, output_dir, face_map, segments, samples,
        )
        roles = "talk_time" if people and people[0]["role"] else "footage_order"
        if people is None:
            reasons = " ".join(r for r in (camera_reason, seat_reason) if r)
            return _single(f"{reasons} The thumbnail uses the single-face layout.")
    return {"layout": "pair", "people": _arrange(people, swap), "roles": roles, "swapped": swap}
