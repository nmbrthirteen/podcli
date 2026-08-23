"""Pure helper functions for the face-tracking / camera-movement
pipeline.

Extracted from video_processor.py. These are all side-effect-free:
they take dicts / scalars / lists and return decisions (a camera
center, a speaker id, a clamped crop_x). No ffmpeg, no cv2, no
numpy. Trivially unit-testable in isolation, but they govern the
camera's behavior during every clip render.
"""

from __future__ import annotations


def update_tripod_camera(
    current_center_x: float,
    target_center_x: float | None,
    crop_w: int,
    video_width: int,
    dt: float,
    force_snap: bool = False,
) -> float:
    """Heavy-tripod camera movement inspired by OpenShorts.

    The camera stays still while the subject remains inside a safe
    zone, then moves at a bounded speed instead of constantly chasing
    every face twitch. Always clamps the result so the crop window
    never falls off the source frame.
    """
    half_crop = crop_w / 2.0
    min_center = half_crop
    max_center = max(half_crop, video_width - half_crop)

    if target_center_x is None:
        return min(max(current_center_x, min_center), max_center)

    if force_snap:
        current_center_x = target_center_x
    else:
        diff = target_center_x - current_center_x
        # Wide enough to absorb natural head movement and detector
        # noise, narrow enough to keep the speaker composed near center.
        safe_zone_radius = crop_w * 0.22
        if abs(diff) > safe_zone_radius:
            slow_speed = 72.0
            fast_speed = 360.0
            speed = fast_speed if abs(diff) > crop_w * 0.5 else slow_speed
            step = min(abs(diff), speed * max(dt, 0.01))
            current_center_x += step if diff > 0 else -step

    return min(max(current_center_x, min_center), max_center)


def choose_camera_speaker(
    transcript_speaker: str | None,
    transcript_duration: float,
    active_speaker: str | None,
    pending_speaker: str | None,
    pending_count: int,
    min_turn_duration: float = 2.6,
    confirmation_frames: int = 3,
) -> tuple[str | None, str | None, int, bool]:
    """Stabilize diarization before the camera switches.

    Brief interjections don't move the camera; sustained turns must
    be confirmed for `confirmation_frames` samples before the switch
    is committed. Returns:
        (camera_speaker, pending_speaker, pending_count, switched_now)
    """
    if transcript_speaker is None:
        return active_speaker, pending_speaker, pending_count, False

    if active_speaker is None:
        return transcript_speaker, None, 0, True

    if transcript_speaker == active_speaker:
        return active_speaker, None, 0, False

    # Brief interjections should not move the camera.
    if transcript_duration < min_turn_duration:
        return active_speaker, None, 0, False

    if pending_speaker != transcript_speaker:
        return active_speaker, transcript_speaker, 1, False

    pending_count += 1
    if pending_count < confirmation_frames:
        return active_speaker, pending_speaker, pending_count, False

    return transcript_speaker, None, 0, True


def safe_default_center(
    width: int,
    crop_w: int,
    face_map: dict | None,
    has_any_split: bool,
    first_speaker: str | None,
    speaker_anchor_x: dict,
) -> float:
    """Pick a safe initial camera center.

    On split-screen, width/2 is the dead zone between two feeds.  Use
    the face_map or speaker_anchor to start on an actual speaker
    instead. Fallback order:
      1. first_speaker's learned anchor
      2. largest face_map cluster
      3. plain center (non-split)
      4. left quarter (split with no map)
    """
    if first_speaker and first_speaker in speaker_anchor_x:
        return float(speaker_anchor_x[first_speaker])

    if face_map and face_map.get("clusters"):
        clusters = face_map["clusters"]
        best = max(clusters, key=lambda c: c.get("count", 0))
        return float(best["center_x"])

    if not has_any_split:
        return float(width) / 2

    return float(width) / 4


def clamp_away_from_dead_zone(
    crop_x: int,
    crop_w: int,
    width: int,
    face_map: dict | None,
    has_any_split: bool,
) -> int:
    """Snap a crop that centers on the split-screen seam to the nearest
    cluster instead.

    Only triggers when the crop CENTER is close to the midline of the
    source frame — a crop that merely straddles the midline is fine on
    fullscreen layouts where the face genuinely sits near center.
    """
    if not has_any_split or not face_map:
        return crop_x

    mid_x = width // 2
    crop_center = crop_x + crop_w // 2

    seam_margin = crop_w // 8  # ~75px on a 607-wide crop
    if abs(crop_center - mid_x) > seam_margin:
        return crop_x

    clusters = face_map.get("clusters", [])
    if not clusters:
        return max(0, width // 4 - crop_w // 2)

    best_cluster = min(
        clusters, key=lambda c: abs(c["center_x"] - crop_center)
    )
    snapped = max(0, min(best_cluster["center_x"] - crop_w // 2, width - crop_w))
    return snapped


def upgrade_speaker_mappings(face_map: dict) -> dict:
    """Invalidate stale speaker-to-cluster mappings from old caches.

    v1 caches used "first-to-speak = left" which breaks when the host
    speaks first but sits on the right. This function clears the
    mappings entirely and stamps `_mappings_v2 = True` so the clear
    only runs once per cache. Cluster positions are kept — they're
    still useful for dead-zone clamping and safe defaults.

    Note: face_analysis.py writes its own v2-quality mappings and
    stamps them with `_mappings_v2=True`, so newly-built face_maps
    skip this function entirely.
    """
    face_map = dict(face_map)
    face_map["speaker_mappings"] = {}
    face_map["_mappings_v2"] = True
    return face_map


def crop_center_keeping_faces_visible(
    face_xs: list[float],
    crop_w: int,
    video_width: int,
    margin_ratio: float = 0.15,
) -> float:
    """Pick the crop center that leaves the most sampled faces in frame.

    The median of a run of face positions is only the right answer when
    those positions are unimodal. When a run mixes two layouts, a
    fullscreen shot at 960 and a split-screen tile at 1440, the median lands
    at 1200, which on a 607-wide crop is the wall between them, and the
    render holds on nothing for the length of the run.

    So score candidate centers by how many faces they actually keep inside
    the crop, and only fall back to closeness as a tiebreak. On a unimodal
    run every candidate keeps every face and this returns the median, which
    is what the caller wanted in the first place.
    """
    if not face_xs:
        return float(video_width) / 2

    from statistics import median

    max_crop_x = max(0, video_width - crop_w)
    margin = crop_w * margin_ratio

    def _center_of(candidate: float) -> float:
        crop_x = min(max(candidate - crop_w / 2.0, 0.0), float(max_crop_x))
        return crop_x + crop_w / 2.0

    def _score(candidate: float) -> tuple[int, float]:
        center = _center_of(candidate)
        low = center - crop_w / 2.0 + margin
        high = center + crop_w / 2.0 - margin
        inside = [x for x in face_xs if low <= x <= high]
        if not inside:
            return 0, -float("inf")
        spread = sum(abs(x - center) for x in inside) / len(inside)
        return len(inside), -spread

    candidates = list(face_xs) + [float(median(face_xs))]
    best = max(candidates, key=_score)
    return _center_of(best)


def seats_from_frames(
    frame_positions: list[list[float]],
    video_width: int,
    min_paired_frames: int = 3,
    min_separation_ratio: float = 0.20,
    min_paired_share: float = 0.10,
) -> tuple[int, int] | None:
    """Find the two fixed positions people occupy, or None if there is one.

    Takes the x of every face in every sampled frame, grouped by frame. Two
    seats are only real if some frame held two faces at once and they sat far
    enough apart to be two people rather than two heads sharing one camera.

    Counting a flat list of positions either side of the midline cannot make
    that distinction, and mistaking one person for two is expensive: the
    invented seat is empty wall, and every speaker mapped to it renders as a
    hold on nothing.
    """
    from statistics import median

    paired = [sorted(xs) for xs in frame_positions if len(xs) >= 2]
    with_faces = sum(1 for xs in frame_positions if xs)
    # A share as well as a count, so a handful of false positives, a face in a
    # photo on the shelf behind someone, cannot seat a second person.
    if len(paired) < max(min_paired_frames, with_faces * min_paired_share):
        return None

    left = int(median([xs[0] for xs in paired]))
    right = int(median([xs[-1] for xs in paired]))
    if (right - left) <= video_width * min_separation_ratio:
        return None
    return left, right


def clip_layout_is_mixed(
    detections: list,
    face_map: dict | None = None,
    min_share: float = 0.15,
    min_frames: int = 3,
) -> bool:
    """Does this clip switch between a split screen and a fullscreen shot?

    Judged on the clip's own frames first. The layout used to be read only
    from the episode-wide face_map, and a face_map cached by a podcli older
    than the mixed-layout work carries no is_mixed_layout key at all, so the
    default turned those into "not mixed". That sent Riverside recordings down
    a path that holds one camera position across a layout change, and every
    fullscreen stretch rendered as the wall beside the speaker.

    Both layouts must hold a real share of the frames. A count alone would
    call a few frames with a missed second face a layout change and route
    almost every clip through the mixed path.
    """
    split_frames = sum(1 for _, faces in detections if len(faces) >= 2)
    single_frames = sum(1 for _, faces in detections if len(faces) == 1)
    layout_frames = split_frames + single_frames

    if layout_frames and (
        split_frames >= max(min_frames, layout_frames * min_share)
        and single_frames >= max(min_frames, layout_frames * min_share)
    ):
        return True

    # A clip sitting entirely inside one layout still belongs to a mixed
    # episode, so the episode-wide flag stays a valid hint.
    return bool(face_map and face_map.get("is_mixed_layout", False))


def followed_face_cx_at(
    t_target: float,
    tracked_detections: list,
    segment_tracks: list,
    fallback_track_id: int | None = None,
    window: float = 1.5,
) -> float | None:
    """Where the speaker the camera is following actually is at a given time.

    Used to check a planned crop still holds its subject. It has to ask about
    the followed track, not the biggest face in the frame: on a split screen
    the biggest face is whoever sits closest to their own camera, and on the
    recording this was written for one speaker measures 845px across against
    the other's 518. Taking the larger one made every correct crop onto the
    quieter side look like a crop onto nobody, so each keyframe was pulled
    back to the same person and the camera stopped switching.

    Falls back to the most prominent face only when the followed speaker is
    not on screen anywhere near this moment, where any face beats none.
    """
    track_id = None
    for start_t, end_t, _speaker, tid, *_rest in segment_tracks:
        if start_t <= t_target <= end_t:
            track_id = tid
            break
    if track_id is None:
        track_id = fallback_track_id

    best_cx = best_dt = None
    fallback_cx, fallback_dt = None, None
    for t, faces in tracked_detections:
        dt = abs(t - t_target)
        if dt > window or not faces:
            continue
        if fallback_dt is None or dt < fallback_dt:
            fallback_cx = float(max(faces, key=lambda f: f["fw"])["cx"])
            fallback_dt = dt
        if track_id is None:
            continue
        face = next((f for f in faces if f["track_id"] == track_id), None)
        if face is not None and (best_dt is None or dt < best_dt):
            best_cx, best_dt = float(face["cx"]), dt

    return best_cx if best_cx is not None else fallback_cx
