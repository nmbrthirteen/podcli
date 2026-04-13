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
