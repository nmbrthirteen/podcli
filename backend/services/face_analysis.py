"""
Face analysis service — runs once during transcription, caches results.

Detects face positions throughout the video and maps them to speakers.
Clip generation uses this pre-computed data instead of re-scanning every time.

Output: face_map dict with observations, clusters, speaker mappings, and
pre-computed crop positions for each speaker segment.
"""

import os
import sys
from typing import Optional, Callable


def analyze_faces(
    video_path: str,
    speaker_segments: list[dict],
    duration: float,
    progress_callback: Optional[Callable[[int, str], None]] = None,
) -> Optional[dict]:
    """
    Analyze face positions throughout the video and map to speakers.

    Args:
        video_path: Path to the video file
        speaker_segments: List of {speaker, start, end} dicts from diarization
        duration: Video duration in seconds
        progress_callback: Optional (percent, message) callback

    Returns:
        face_map dict or None if analysis fails:
        {
            "observations": [{time, face_center_x, face_width, confidence}, ...],
            "clusters": [{center_x, count, crop_x}, ...],
            "speaker_mappings": {speaker: cluster_index, ...},
            "is_split_screen": bool,
            "dominant_speaker": str or None,
            "video_width": int,
            "video_height": int,
        }
    """
    try:
        import cv2
        import numpy as np
        from services.face_detector import create_detector, detect_faces
        from services.face_track_helpers import seats_from_frames
    except ImportError:
        print("Warning: OpenCV not available, skipping face analysis", file=sys.stderr)
        return None

    if progress_callback:
        progress_callback(0, "Starting face analysis...")

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        return None

    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30

    # Create YuNet detector
    detector = create_detector(width, height)
    if detector is None:
        cap.release()
        print("Warning: Face detection model not found, skipping", file=sys.stderr)
        return None

    # Sample ~2 frames per second across the full video (enough for analysis)
    sample_count = min(300, max(20, int(duration * 2)))
    observations = []
    faces_per_frame = []

    if progress_callback:
        progress_callback(10, f"Scanning {sample_count} frames for faces...")

    # We compare each face's mouth ROI against the same face's mouth ROI one
    # frame later. High difference = the mouth is moving = this person is
    # speaking. Listeners stay static.
    #
    # The pair has to be adjacent frames. This used to diff against the
    # previous *sample*, and sample_count is capped at 300, so on a 60-minute
    # episode the two frames were 12 seconds apart and the difference measured
    # nothing but the passage of time. Every split-screen episode over about
    # two and a half minutes was choosing its speaker from noise.

    # Per sampled frame, the x of every face in it. Clustering needs to know
    # which positions were simultaneous, which a flat observation list cannot say.
    frame_positions: list[list[int]] = []

    def _mouth_roi_gray(frame, cx: int, cy: int, fw: int, fh: int):
        """Return a small downsampled grayscale of the mouth area,
        or None if the bbox is out of frame."""
        y0 = int(cy + fh * 0.10)
        y1 = int(cy + fh * 0.55)
        x0 = int(cx - fw * 0.35)
        x1 = int(cx + fw * 0.35)
        h_frame, w_frame = frame.shape[:2]
        y0 = max(0, min(h_frame - 2, y0))
        y1 = max(y0 + 1, min(h_frame, y1))
        x0 = max(0, min(w_frame - 2, x0))
        x1 = max(x0 + 1, min(w_frame, x1))
        roi = frame[y0:y1, x0:x1]
        if roi.size == 0:
            return None
        # 16x16 downsample + grayscale — cheap to diff and robust to
        # sub-pixel jitter.
        small = cv2.resize(roi, (16, 16))
        return cv2.cvtColor(small, cv2.COLOR_BGR2GRAY).astype(np.int16)

    # Some containers under-report CAP_PROP_FRAME_COUNT, which would confine
    # sampling to the front of the video; fall back to duration*fps.
    reported_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    est_frames = int(duration * fps) if duration and fps else 0
    total_frames = max(reported_frames, est_frames, 1)
    # grab() advances cheaply, retrieve() decodes only the sampled frames — far
    # cheaper than a cap.set() seek (keyframe re-decode) per sample.
    step = max(1, total_frames // sample_count)
    frame_idx = -1
    sampled = 0
    while sampled < sample_count:
        if not cap.grab():
            break
        frame_idx += 1
        if frame_idx % step != 0:
            continue
        ret, frame = cap.retrieve()
        if not ret:
            continue
        pos_ms = cap.get(cv2.CAP_PROP_POS_MSEC)
        t = pos_ms / 1000.0 if pos_ms and pos_ms > 0 else frame_idx / fps

        # The very next frame, decoded but never detected on: the face boxes
        # from this frame are still accurate one frame later, so the second
        # decode buys the mouth diff and costs no inference. Skipped when the
        # sampler is already taking every frame, where borrowing one would
        # halve the sample count.
        next_frame = None
        if step > 1 and cap.grab():
            frame_idx += 1
            got_next, candidate = cap.retrieve()
            if got_next:
                next_frame = candidate

        faces = detect_faces(detector, frame, width, height)

        frame_faces = 0
        for f in faces:
            fh = f.get("fh", f["fw"])  # YuNet returns fh; fall back to fw
            mouth = _mouth_roi_gray(frame, f["cx"], f["cy"], f["fw"], fh)

            activity = 0.0
            if mouth is not None and next_frame is not None:
                later = _mouth_roi_gray(next_frame, f["cx"], f["cy"], f["fw"], fh)
                if later is not None and later.shape == mouth.shape:
                    activity = float(np.mean(np.abs(later - mouth)))

            observations.append({
                "time": round(t, 3),
                "face_center_x": f["cx"],
                "face_center_y": f["cy"],
                "face_width": f["fw"],
                "confidence": f["confidence"],
                "activity": round(activity, 3),
            })
            frame_faces += 1

        faces_per_frame.append(frame_faces)
        frame_positions.append([f["cx"] for f in faces])

        if progress_callback and sampled % 20 == 0:
            pct = 10 + int(60 * sampled / sample_count)
            progress_callback(pct, f"Analyzing faces... {sampled}/{sample_count}")
        sampled += 1

    cap.release()

    if len(observations) < 3:
        return None

    if progress_callback:
        progress_callback(75, "Clustering face positions...")

    # Cluster faces into seats: the fixed positions a person occupies.
    #
    # Two seats mean two people on screen at once, and the only evidence for
    # that is a frame holding both. Splitting a flat list of positions at the
    # midline cannot tell two people apart from one person who leaned across
    # it: three sampled frames on the far side were enough to mint a second
    # seat. Every speaker mapped to that invented seat then rendered as a hold
    # on empty wall for the length of their turn.
    target_ratio = 1080 / 1920  # 9:16
    crop_w = int(height * target_ratio)
    positions = np.array([o["face_center_x"] for o in observations])
    mid_x = width // 2
    seam_margin = 20

    seats = seats_from_frames(frame_positions, width)

    clusters_list = []
    if seats:
        left_seat, right_seat = seats
        clusters_list.append({
            "center_x": left_seat,
            "count": sum(1 for x in positions if x < mid_x),
            "crop_x": max(0, min(left_seat - crop_w // 2, mid_x - crop_w - seam_margin)),
        })
        clusters_list.append({
            "center_x": right_seat,
            "count": sum(1 for x in positions if x >= mid_x),
            "crop_x": max(mid_x + seam_margin, min(right_seat - crop_w // 2, width - crop_w)),
        })
    else:
        # One seat, which is not the same as one speaker. The guest may be
        # off camera entirely, and the render should stay on the person it can
        # actually see.
        cx = int(np.median(positions))
        clusters_list.append({
            "center_x": cx,
            "count": len(positions),
            "crop_x": max(0, min(cx - crop_w // 2, width - crop_w)),
        })

    clusters_list.sort(key=lambda c: c["center_x"])

    # Detect split-screen and mixed layouts (Riverside-style recordings
    # that switch between split-screen and single-person views)
    avg_faces = float(np.mean(faces_per_frame)) if faces_per_frame else 0
    is_split_screen = avg_faces >= 1.5 and len(clusters_list) >= 2
    split_count = sum(1 for f in faces_per_frame if f >= 2)
    single_count = sum(1 for f in faces_per_frame if f == 1)
    is_mixed_layout = split_count >= 3 and single_count >= 3

    # Map speakers to clusters
    speakers = sorted(set(s.get("speaker", "") for s in speaker_segments if s.get("speaker")))
    speaker_mappings = {}

    if progress_callback:
        progress_callback(85, "Mapping speakers to face positions...")

    if is_split_screen and len(clusters_list) >= 2:
        # Split-screen speaker→cluster mapping.
        #
        # Pure face-position counting is symmetric on true split-screens
        # (both faces are visible every frame), so presence votes alone
        # can't tell which face is actually speaking. We use the
        # per-observation `activity` signal — the mouth-ROI frame-to-
        # frame pixel difference computed above. During a speaker's
        # turn, THEIR face has high activity (mouth moving) and the
        # listener's face has near-zero activity.
        speaker_talk_time = {}
        speaker_first_time = {}
        for seg in speaker_segments:
            sp = seg.get("speaker")
            if sp:
                speaker_talk_time[sp] = speaker_talk_time.get(sp, 0) + (seg["end"] - seg["start"])
                if sp not in speaker_first_time:
                    speaker_first_time[sp] = seg["start"]

        speakers_by_talk = sorted(speakers, key=lambda s: speaker_talk_time.get(s, 0), reverse=True)
        top_2 = speakers_by_talk[:2]

        # Observation list now includes activity per face. Keep track of
        # both the x position (for cluster matching) and the activity
        # score (for vote weighting).
        obs_arr = [
            (o["time"], o["face_center_x"], o.get("activity", 0.0))
            for o in observations
        ]
        obs_arr.sort(key=lambda x: x[0])
        obs_times = [t for t, _, _ in obs_arr]

        def _activity_vote_range(start: float, end: float, votes_out: list) -> None:
            """Accumulate activity-weighted votes for observations inside
            [start-0.25, end+0.25]. A small baseline of +1 is added for
            every matching observation so a completely static face still
            beats a missing cluster, but activity dominates when present."""
            import bisect
            lo = bisect.bisect_left(obs_times, start - 0.25)
            hi = bisect.bisect_right(obs_times, end + 0.25)
            for idx in range(lo, hi):
                _t, obs_cx, obs_activity = obs_arr[idx]
                for ci, cl in enumerate(clusters_list):
                    if abs(obs_cx - cl["center_x"]) < width * 0.15:
                        # Presence gets 1 vote; activity gets ×20 weight so
                        # a handful of mouth-motion observations outweigh
                        # thousands of static-face observations.
                        votes_out[ci] += 1.0 + (obs_activity * 20.0)

        for speaker in top_2:
            sp_ranges = [
                (s["start"], s["end"])
                for s in speaker_segments
                if s.get("speaker") == speaker
            ]
            votes = [0.0] * len(clusters_list)
            for start, end in sp_ranges:
                _activity_vote_range(start, end, votes)
            if any(votes):
                speaker_mappings[speaker] = int(np.argmax(votes))

        # Resolve conflicts: if both speakers mapped to the same cluster,
        # assign the weaker one to the other cluster. Use the same
        # full-turn voting as the primary pass so the tiebreaker doesn't
        # regress back to the ±1.5s start-only sampling.
        def _confidence_for(speaker: str, cluster_index: int) -> int:
            """Count observations inside this speaker's turns that fall
            near the given cluster's center_x."""
            cluster_x = clusters_list[cluster_index]["center_x"]
            score = 0.0
            for s in speaker_segments:
                if s.get("speaker") != speaker:
                    continue
                import bisect
                lo = bisect.bisect_left(obs_times, s["start"] - 0.25)
                hi = bisect.bisect_right(obs_times, s["end"] + 0.25)
                for idx in range(lo, hi):
                    _t, obs_cx, obs_activity = obs_arr[idx]
                    if abs(obs_cx - cluster_x) < width * 0.15:
                        # Activity-weighted confidence so the conflict
                        # resolver uses the same signal as the primary pass.
                        score += 1.0 + (obs_activity * 20.0)
            return score

        if len(top_2) == 2 and top_2[0] in speaker_mappings and top_2[1] in speaker_mappings:
            if speaker_mappings[top_2[0]] == speaker_mappings[top_2[1]]:
                other_ci = 1 - speaker_mappings[top_2[0]]
                # Give the less-confident speaker the other cluster.
                sp0_votes = _confidence_for(top_2[0], speaker_mappings[top_2[0]])
                sp1_votes = _confidence_for(top_2[1], speaker_mappings[top_2[1]])
                if sp0_votes >= sp1_votes:
                    speaker_mappings[top_2[1]] = other_ci
                else:
                    speaker_mappings[top_2[0]] = other_ci

        # If observation-based mapping failed for either speaker, fall back
        # to temporal ordering (first-to-speak = left).
        if len(top_2) == 2:
            top_2_by_first = sorted(top_2, key=lambda s: speaker_first_time.get(s, float("inf")))
            if top_2[0] not in speaker_mappings and top_2[1] not in speaker_mappings:
                speaker_mappings[top_2_by_first[0]] = 0
                speaker_mappings[top_2_by_first[1]] = 1
            elif top_2[0] not in speaker_mappings:
                speaker_mappings[top_2[0]] = 1 - speaker_mappings[top_2[1]]
            elif top_2[1] not in speaker_mappings:
                speaker_mappings[top_2[1]] = 1 - speaker_mappings[top_2[0]]

        # Extra speakers → dominant speaker's cluster
        dominant_idx = speaker_mappings.get(speakers_by_talk[0], 0) if speakers_by_talk else 0
        for sp in speakers_by_talk[2:]:
            speaker_mappings[sp] = dominant_idx
    elif len(clusters_list) >= 2 and len(speakers) >= 2:
        # Non-split-screen: same activity-weighted voting over full
        # speaker turns. On non-split layouts a single face is usually
        # on-screen at a time, so the signal is already strong even
        # without activity, but we use activity for consistency.
        obs_arr2 = [
            (o["time"], o["face_center_x"], o.get("activity", 0.0))
            for o in observations
        ]
        obs_arr2.sort(key=lambda x: x[0])
        obs_times2 = [t for t, _, _ in obs_arr2]
        for speaker in speakers:
            sp_ranges = [
                (s["start"], s["end"])
                for s in speaker_segments
                if s.get("speaker") == speaker
            ]
            votes = [0.0] * len(clusters_list)
            import bisect
            for start, end in sp_ranges:
                lo = bisect.bisect_left(obs_times2, start - 0.25)
                hi = bisect.bisect_right(obs_times2, end + 0.25)
                for idx in range(lo, hi):
                    _t, obs_cx, obs_activity = obs_arr2[idx]
                    for ci, cl in enumerate(clusters_list):
                        if abs(obs_cx - cl["center_x"]) < width * 0.15:
                            votes[ci] += 1.0 + (obs_activity * 20.0)
            if any(votes):
                speaker_mappings[speaker] = int(np.argmax(votes))
    elif len(speakers) == 1 and clusters_list:
        speaker_mappings[speakers[0]] = 0

    # Determine dominant speaker
    speaker_durations = {}
    for seg in speaker_segments:
        sp = seg.get("speaker", "")
        if sp:
            speaker_durations[sp] = speaker_durations.get(sp, 0) + (seg["end"] - seg["start"])
    dominant_speaker = max(speaker_durations, key=speaker_durations.get) if speaker_durations else None

    if progress_callback:
        progress_callback(100, "Face analysis complete")

    return {
        "observations": observations,
        "clusters": clusters_list,
        "speaker_mappings": speaker_mappings,
        "is_split_screen": is_split_screen,
        "is_mixed_layout": is_mixed_layout,
        "dominant_speaker": dominant_speaker,
        "video_width": width,
        "video_height": height,
        "_mappings_v2": True,
    }
