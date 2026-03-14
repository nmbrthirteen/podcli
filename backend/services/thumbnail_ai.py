"""
AI-driven thumbnail generator.

1. Extract candidate frames from video (OpenCV)
2. Claude analyzes frames + clip content → generates HTML with exact CSS
3. Playwright renders HTML → PNG

Claude makes ALL visual decisions: crop, text size, positioning, colors.
The config provides brand constraints (accent color, logo, font), Claude works within them.
"""

import json
import os
import subprocess
import sys
import tempfile
import base64
from typing import Optional


def _load_brand_config() -> dict:
    """Load brand constraints from thumbnail-config.json."""
    defaults = {
        "width": 1080,
        "height": 1920,
        "accent_color": "#00CED1",
        "bg_color": "#0D0D0D",
        "font_family": "'Inter', 'Helvetica Neue', 'Arial', sans-serif",
        "enabled": True,
        "variations": 3,
    }
    config_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "..", ".podcli", "thumbnail-config.json"
    )
    if os.path.exists(config_path):
        try:
            with open(config_path) as f:
                defaults.update(json.load(f))
        except Exception:
            pass
    return defaults


def _frame_sharpness(frame, face_roi=None) -> float:
    """Laplacian variance — higher = sharper. Measures face region if provided."""
    import cv2
    if face_roi is not None:
        x1, y1, x2, y2 = face_roi
        region = frame[y1:y2, x1:x2]
    else:
        region = frame
    gray = cv2.cvtColor(region, cv2.COLOR_BGR2GRAY)
    return cv2.Laplacian(gray, cv2.CV_64F).var()


def extract_candidate_frames(
    video_path: str,
    output_dir: str,
    count: int = 5,
) -> list[dict]:
    """
    Extract candidate frames with detected faces from a video.
    Returns list of {path, face_x, face_y, face_w, face_h, confidence, timestamp}.

    Scoring favors:
    - High confidence (frontal, clear face)
    - Sharp face region (eyes open, not blurry)
    - Medium face size (natural portrait, not extreme close-up)
    """
    try:
        import cv2
        import numpy as np
    except ImportError:
        return []

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        return []

    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30
    duration = total_frames / fps
    src_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    src_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    is_portrait = src_h > src_w  # Already 9:16 or similar

    # Load face detector
    backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    proto = os.path.join(backend_dir, "models", "deploy.prototxt")
    model = os.path.join(backend_dir, "models", "res10_300x300_ssd_iter_140000.caffemodel")

    if not (os.path.exists(proto) and os.path.exists(model)):
        cap.release()
        return []

    detector = cv2.dnn.readNetFromCaffe(proto, model)
    os.makedirs(output_dir, exist_ok=True)

    # Sample more frames for better candidates
    start_t = duration * 0.1
    end_t = duration * 0.9
    sample_count = max(count * 6, 30)
    candidates = []

    for i in range(sample_count):
        t = start_t + i * (end_t - start_t) / sample_count
        cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
        ret, frame = cap.read()
        if not ret:
            continue

        h, w = frame.shape[:2]
        blob = cv2.dnn.blobFromImage(cv2.resize(frame, (300, 300)), 1.0, (300, 300), (104.0, 177.0, 123.0))
        detector.setInput(blob)
        detections = detector.forward()

        for j in range(detections.shape[2]):
            conf = detections[0, 0, j, 2]
            if conf > 0.7:
                x1 = max(0, int(detections[0, 0, j, 3] * w))
                y1 = max(0, int(detections[0, 0, j, 4] * h))
                x2 = min(w, int(detections[0, 0, j, 5] * w))
                y2 = min(h, int(detections[0, 0, j, 6] * h))
                fw = x2 - x1
                fh = y2 - y1

                if fw < w * 0.10:
                    continue

                # Split-screen check
                face_cx = (x1 + x2) / 2
                if 0.4 * w < face_cx < 0.6 * w and fw < w * 0.25:
                    continue

                # Sharpness of face region (proxy for eyes open, good expression)
                sharpness = _frame_sharpness(frame, (x1, y1, x2, y2))

                # Face size as fraction of frame
                face_area_pct = (fw * fh) / (w * h) * 100

                # Score: confidence × sharpness × size_preference
                # Prefer faces that are 5-25% of frame area (natural portrait)
                # Penalize extreme close-ups (>35%) and tiny faces (<3%)
                if face_area_pct > 35:
                    size_factor = 0.4  # Too zoomed in
                elif face_area_pct > 25:
                    size_factor = 0.7
                elif face_area_pct < 3:
                    size_factor = 0.5  # Too small
                else:
                    size_factor = 1.0  # Sweet spot

                score = (conf ** 2) * (sharpness ** 0.5) * size_factor

                candidates.append({
                    "frame": frame.copy(),
                    "timestamp": round(t, 1),
                    "confidence": round(float(conf), 3),
                    "face_x_pct": round((x1 + x2) / 2 / w * 100, 1),
                    "face_y_pct": round((y1 + y2) / 2 / h * 100, 1),
                    "face_w_pct": round(fw / w * 100, 1),
                    "face_h_pct": round(fh / h * 100, 1),
                    "sharpness": round(sharpness, 1),
                    "score": score,
                })

    cap.release()

    if not candidates:
        return []

    # Pick top N by score, spaced apart in time
    candidates.sort(key=lambda c: c["score"], reverse=True)
    selected = []
    for c in candidates:
        if len(selected) >= count:
            break
        too_close = any(abs(c["timestamp"] - s["timestamp"]) < 3 for s in selected)
        if not too_close:
            selected.append(c)

    # Detect split-screen: if all faces are in the left or right third,
    # the video is likely a Riverside/multi-cam layout. Pre-crop to just
    # the half containing the face.
    if selected:
        avg_face_x = sum(c["face_x_pct"] for c in selected) / len(selected)
        is_split = avg_face_x < 35 or avg_face_x > 65

        if is_split:
            for c in selected:
                h_frame, w_frame = c["frame"].shape[:2]
                face_px = int(w_frame * c["face_x_pct"] / 100)
                if avg_face_x < 35:
                    c["frame"] = c["frame"][:, :w_frame // 2]
                    new_w = w_frame // 2
                    c["face_x_pct"] = round(face_px / new_w * 100, 1)
                else:
                    c["frame"] = c["frame"][:, w_frame // 2:]
                    new_w = w_frame - w_frame // 2
                    c["face_x_pct"] = round((face_px - w_frame // 2) / new_w * 100, 1)
                c["face_w_pct"] = c["face_w_pct"] * 2
            # After split crop, source is no longer portrait
            is_portrait = False

    # Crop/compose each frame to 9:16
    target_ratio = 9 / 16  # 0.5625
    target_w, target_h = 1080, 1920

    for c in selected:
        frame = c["frame"]
        fh, fw = frame.shape[:2]
        face_cx = int(fw * c["face_x_pct"] / 100)
        face_cy = int(fh * c["face_y_pct"] / 100)
        src_ratio = fw / fh

        if is_portrait and src_ratio > 0.5:
            # Already portrait (9:16 ish) — scale to fit width, place face
            # in upper portion with dark padding below for text area.
            scale = target_w / fw
            new_w = target_w
            new_h = int(fh * scale)

            resized = cv2.resize(frame, (new_w, new_h), interpolation=cv2.INTER_LANCZOS4)
            face_cy_scaled = int(face_cy * scale)

            # Place face at ~30% from top of the 1920px canvas
            y_offset = int(target_h * 0.30) - face_cy_scaled
            # Clamp: don't go above top, and keep as much of the frame visible
            y_offset = max(0, min(y_offset, target_h - new_h))

            # Compose onto dark canvas
            canvas = np.zeros((target_h, target_w, 3), dtype=np.uint8)
            canvas[:] = (13, 13, 13)  # #0D0D0D in BGR
            end_y = min(y_offset + new_h, target_h)
            canvas[y_offset:end_y] = resized[:end_y - y_offset]

            c["frame"] = canvas
            c["face_x_pct"] = round(face_cx * scale / target_w * 100, 1)
            c["face_y_pct"] = round((face_cy_scaled + y_offset) / target_h * 100, 1)
        else:
            # Landscape source — crop to 9:16 centered on face
            crop_h = fh
            crop_w = int(crop_h * target_ratio)
            if crop_w > fw:
                crop_w = fw
                crop_h = int(crop_w / target_ratio)

            crop_x = face_cx - crop_w // 2
            crop_x = max(0, min(crop_x, fw - crop_w))

            crop_y = face_cy - int(crop_h * 0.25)
            crop_y = max(0, min(crop_y, fh - crop_h))

            c["frame"] = frame[crop_y:crop_y + crop_h, crop_x:crop_x + crop_w]
            new_face_cx = face_cx - crop_x
            new_face_cy = face_cy - crop_y
            c["face_x_pct"] = round(new_face_cx / crop_w * 100, 1)
            c["face_y_pct"] = round(new_face_cy / crop_h * 100, 1)

    # Save frames
    results = []
    for i, c in enumerate(selected):
        path = os.path.join(output_dir, f"frame_{i}.jpg")
        frame = c["frame"]
        fh, fw = frame.shape[:2]
        if (fw, fh) != (target_w, target_h):
            frame = cv2.resize(frame, (target_w, target_h), interpolation=cv2.INTER_LANCZOS4)
        cv2.imwrite(path, frame, [cv2.IMWRITE_JPEG_QUALITY, 92])
        results.append({
            "path": path,
            "timestamp": c["timestamp"],
            "confidence": c["confidence"],
            "face_x_pct": c["face_x_pct"],
            "face_y_pct": c["face_y_pct"],
            "face_w_pct": c["face_w_pct"],
            "face_h_pct": c["face_h_pct"],
        })

    return results


def ask_claude_for_layout(
    title: str,
    frame_path: str,
    frame_info: Optional[dict] = None,
    logo_path: Optional[str] = None,
    config: Optional[dict] = None,
) -> Optional[dict]:
    """
    Ask Claude to generate ALL layout values for the thumbnail.
    Claude sees the frame info and decides everything dynamically.

    Returns dict with line1, line2, box_y, photo_object_position, etc.
    """
    from services.claude_suggest import _find_claude
    claude_path = _find_claude()
    if not claude_path:
        return None

    cfg = config or _load_brand_config()

    face_ctx = "No face detected."
    if frame_info:
        face_ctx = (
            f"Face center: x={frame_info.get('face_x_pct', 50)}%, y={frame_info.get('face_y_pct', 40)}%.\n"
            f"Face size: {frame_info.get('face_w_pct', 20)}% width, {frame_info.get('face_h_pct', 25)}% height.\n"
            f"The frame is already pre-cropped to 1080x1920 (9:16) with face centered horizontally."
        )

    prompt = f"""You are a thumbnail layout engine. Given a title and face position, return CSS values for a YouTube Shorts thumbnail (1080x1920).

TITLE: "{title}"

PHOTO INFO:
{face_ctx}

Return ONLY valid JSON with these fields:
{{
  "line1": "FIRST LINE TEXT",
  "line2": "SECOND LINE TEXT",
  "box_y": "75%",
  "photo_object_position": "center 20%",
  "line1_font_size": "96px",
  "line2_font_size": "90px"
}}

RULES:
- Split the title into 2 impactful lines. Line 1 = setup, Line 2 = payoff.
- box_y: position the text box so it does NOT overlap the face. If face is high (y<40%), use 80-85%. If face is centered (y~50%), use 75-78%. If face is low, use 68-72%.
- photo_object_position: CSS value to show the face well. The photo is already 1080x1920 with face centered, so "center top" usually works. Adjust if face_y is unusual.
- Font sizes: for short text (1-2 words per line) use 96-110px. For medium (3-4 words) use 80-96px. For long (5+ words) use 64-80px. Line 2 should be 85-95% of Line 1.
- No slashes in the text lines."""

    project_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")

    try:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, dir=project_dir) as f:
            f.write(prompt)
            prompt_file = f.name

        result = subprocess.run(
            f'cat "{prompt_file}" | "{claude_path}" --print -p -',
            capture_output=True, text=True, shell=True, timeout=30,
            cwd=project_dir,
        )
        os.unlink(prompt_file)

        if result.returncode != 0:
            return None

        text = result.stdout.strip()
        start = text.find("{")
        end = text.rfind("}") + 1
        if start >= 0 and end > start:
            return json.loads(text[start:end])
    except Exception:
        pass
    return None


def generate_thumbnail_with_template(
    title: str,
    frame_path: str,
    output_path: str,
    logo_path: Optional[str] = None,
    frame_info: Optional[dict] = None,
    config: Optional[dict] = None,
    variation: int = 0,
) -> Optional[str]:
    """
    Template + AI layout. Claude decides all dynamic values per frame.
    Template provides consistent visual structure.
    """
    from services.thumbnail_html import generate_thumbnail, _load_config

    cfg = _load_config()
    if config:
        cfg.update(config)

    # Ask Claude for ALL layout decisions
    layout = ask_claude_for_layout(title, frame_path, frame_info, logo_path, cfg)

    if layout:
        line1 = layout.get("line1", title).strip().strip("/").strip()
        line2 = layout.get("line2", "").strip().strip("/").strip()
        # Apply Claude's CSS decisions to config
        if layout.get("box_y"):
            cfg["box_y"] = layout["box_y"]
            cfg["box_y_with_photo"] = layout["box_y"]
        if layout.get("photo_object_position"):
            cfg["photo_object_position"] = layout["photo_object_position"]
        if layout.get("line1_font_size"):
            cfg["line1_font_size"] = layout["line1_font_size"]
        if layout.get("line2_font_size"):
            cfg["line2_font_size"] = layout["line2_font_size"]
    else:
        # Fallback
        if " / " in title:
            parts = title.split(" / ", 1)
            line1, line2 = parts[0].strip(), parts[1].strip()
        else:
            words = title.split()
            mid = len(words) // 2 or 1
            line1 = " ".join(words[:mid])
            line2 = " ".join(words[mid:])

    return generate_thumbnail(
        line1, line2, output_path,
        photo_path=frame_path if frame_path and os.path.exists(frame_path) else None,
        logo_path=logo_path,
        config=cfg,
        variation=variation,
        face_info=frame_info,
    )


def generate_variations(
    title: str,
    output_dir: str,
    photo_path: Optional[str] = None,
    video_path: Optional[str] = None,
    logo_path: Optional[str] = None,
    config: Optional[dict] = None,
) -> list[str]:
    """
    Generate thumbnail variations using AI.

    1. Extract candidate frames from video (if no photo provided)
    2. For each variation, Claude generates the HTML layout
    3. Playwright renders to PNG

    Falls back to template-based generation if Claude is unavailable.
    """
    cfg = _load_brand_config()
    if config:
        cfg.update(config)

    os.makedirs(output_dir, exist_ok=True)
    n = cfg.get("variations", 3)

    # Get frames
    frames = []
    if photo_path and os.path.exists(photo_path):
        frames = [{"path": photo_path}]
    elif video_path:
        frames_dir = os.path.join(output_dir, "_frames")
        frames = extract_candidate_frames(video_path, frames_dir, count=n)

    if not frames:
        frames = [{"path": None}]  # No photo — dark bg only

    paths = []
    for i in range(n):
        frame = frames[i % len(frames)]
        frame_path = frame.get("path") or ""
        out_path = os.path.join(output_dir, f"thumb_v{i+1}.png")

        result = generate_thumbnail_with_template(
            title=title,
            frame_path=frame_path,
            output_path=out_path,
            logo_path=logo_path,
            frame_info=frame if frame_path else None,
            config=cfg,
            variation=i,
        )

        if result:
            paths.append(result)

    return paths


def thumbnail_to_video_frame(
    thumbnail_path: str,
    output_path: str,
    duration: float = 2.0,
    fade_in: float = 0.3,
    fade_out: float = 0.3,
    width: int = 1080,
    height: int = 1920,
) -> str:
    """Convert thumbnail to a short video clip with fade."""
    cmd = [
        "ffmpeg", "-y",
        "-loop", "1", "-i", thumbnail_path,
        "-t", str(duration),
        "-vf", (
            f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black,"
            f"fade=t=in:st=0:d={fade_in},"
            f"fade=t=out:st={duration - fade_out}:d={fade_out}"
        ),
        "-c:v", "libx264", "-crf", "18", "-preset", "fast",
        "-pix_fmt", "yuv420p", "-an",
        output_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"Failed: {result.stderr[-300:]}")
    return output_path
