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

    # Load DNN face detector
    backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    proto = os.path.join(backend_dir, "models", "deploy.prototxt")
    model = os.path.join(backend_dir, "models", "res10_300x300_ssd_iter_140000.caffemodel")

    if not (os.path.exists(proto) and os.path.exists(model)):
        cap.release()
        print("Warning: Face detection model not found, skipping", file=sys.stderr)
        return None

    detector = cv2.dnn.readNetFromCaffe(proto, model)

    # Sample ~2 frames per second across the full video (enough for analysis)
    sample_count = min(300, max(20, int(duration * 2)))
    observations = []
    faces_per_frame = []

    if progress_callback:
        progress_callback(10, f"Scanning {sample_count} frames for faces...")

    for i in range(sample_count):
        t = i * duration / sample_count
        cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
        ret, frame = cap.read()
        if not ret:
            continue

        h, w = frame.shape[:2]
        blob = cv2.dnn.blobFromImage(
            cv2.resize(frame, (300, 300)), 1.0, (300, 300),
            (104.0, 177.0, 123.0)
        )
        detector.setInput(blob)
        detections = detector.forward()

        frame_faces = 0
        for j in range(detections.shape[2]):
            conf = detections[0, 0, j, 2]
            if conf > 0.5:
                x1 = int(detections[0, 0, j, 3] * w)
                x2 = int(detections[0, 0, j, 5] * w)
                fw = x2 - x1
                if fw < w * 0.04:
                    continue
                cx = (x1 + x2) // 2
                observations.append({
                    "time": round(t, 3),
                    "face_center_x": cx,
                    "face_width": fw,
                    "confidence": round(float(conf), 3),
                })
                frame_faces += 1
        faces_per_frame.append(frame_faces)

        if progress_callback and i % 20 == 0:
            pct = 10 + int(60 * i / sample_count)
            progress_callback(pct, f"Analyzing faces... {i}/{sample_count}")

    cap.release()

    if len(observations) < 3:
        return None

    if progress_callback:
        progress_callback(75, "Clustering face positions...")

    # Cluster faces by position
    target_ratio = 1080 / 1920  # 9:16
    crop_w = int(height * target_ratio)
    positions = np.array([o["face_center_x"] for o in observations])
    cluster_radius = width * 0.20

    clusters_list = []
    used = np.zeros(len(positions), dtype=bool)
    sorted_idx = np.argsort(positions)

    for idx in sorted_idx:
        if used[idx]:
            continue
        mask = (np.abs(positions - positions[idx]) < cluster_radius) & ~used
        if np.any(mask):
            cluster_center = int(np.median(positions[mask]))
            crop_x = max(0, min(cluster_center - crop_w // 2, width - crop_w))
            clusters_list.append({
                "center_x": cluster_center,
                "count": int(np.sum(mask)),
                "crop_x": crop_x,
            })
            used |= mask

    if not clusters_list:
        return None

    clusters_list.sort(key=lambda c: c["center_x"])

    # Detect split-screen
    avg_faces = float(np.mean(faces_per_frame)) if faces_per_frame else 0
    is_split_screen = avg_faces >= 1.5 and len(clusters_list) >= 2

    # Map speakers to clusters
    speakers = sorted(set(s.get("speaker", "") for s in speaker_segments if s.get("speaker")))
    speaker_mappings = {}

    if progress_callback:
        progress_callback(85, "Mapping speakers to face positions...")

    if is_split_screen and len(clusters_list) >= 2 and len(speakers) == 2:
        # Split-screen: map by who speaks first → left face
        speaker_first_time = {}
        for seg in speaker_segments:
            sp = seg.get("speaker")
            if sp and sp not in speaker_first_time:
                speaker_first_time[sp] = seg["start"]
        speakers_sorted = sorted(speakers, key=lambda s: speaker_first_time.get(s, float("inf")))
        speaker_mappings[speakers_sorted[0]] = 0  # left cluster
        speaker_mappings[speakers_sorted[1]] = 1  # right cluster
    elif len(clusters_list) >= 2 and len(speakers) >= 2:
        # Non-split-screen: vote by which face is visible when speaker talks
        obs_arr = [(o["time"], o["face_center_x"]) for o in observations]
        for speaker in speakers:
            sp_times = [s["start"] for s in speaker_segments if s.get("speaker") == speaker]
            votes = [0] * len(clusters_list)
            for t in sp_times:
                for obs_t, obs_cx in obs_arr:
                    if abs(obs_t - t) < 1.5:
                        for ci, cl in enumerate(clusters_list):
                            if abs(obs_cx - cl["center_x"]) < cluster_radius:
                                votes[ci] += 1
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
        "dominant_speaker": dominant_speaker,
        "video_width": width,
        "video_height": height,
    }
