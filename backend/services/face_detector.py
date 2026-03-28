"""
Shared face detector using OpenCV YuNet.

YuNet is a lightweight CNN face detector (227KB) built into OpenCV.
Much faster and more accurate than the old ResNet10 SSD, especially
on side profiles and partially occluded faces.

All face detection in the codebase should go through create_detector()
and detect_faces() to keep the model path and parameters in one place.
"""

import os
from typing import Optional

# Minimum face width as fraction of frame width (filters noise)
MIN_FACE_RATIO = 0.04

# Detection confidence threshold (0.5 balances accuracy vs recall;
# source 16:9 video scores higher than processed 9:16 clips)
CONFIDENCE_THRESHOLD = 0.5

# Max dimension for detection input — keeps inference fast.
# Coordinates are scaled back to original frame size.
_MAX_DIM = 640


def _model_path() -> str:
    backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(backend_dir, "models", "face_detection_yunet_2023mar.onnx")


def create_detector(frame_width: int, frame_height: int):
    """
    Create a YuNet face detector for the given frame dimensions.

    Internally uses a scaled-down input size for speed, but detect_faces()
    returns coordinates in the original frame space.

    Returns (detector, scale_x, scale_y) tuple, or None if model not found.
    """
    import cv2

    model = _model_path()
    if not os.path.exists(model):
        return None

    # Scale down to _MAX_DIM while preserving aspect ratio
    scale = min(_MAX_DIM / frame_width, _MAX_DIM / frame_height, 1.0)
    det_w = int(frame_width * scale)
    det_h = int(frame_height * scale)

    detector = cv2.FaceDetectorYN.create(
        model=model,
        config="",
        input_size=(det_w, det_h),
        score_threshold=CONFIDENCE_THRESHOLD,
        nms_threshold=0.3,
        top_k=10,
    )
    return (detector, det_w, det_h, frame_width / det_w, frame_height / det_h)


def detect_faces(detector_tuple, frame, frame_width: int, frame_height: int) -> list[dict]:
    """
    Detect faces in a single frame using YuNet.

    detector_tuple: the return value of create_detector()
    Returns list of dicts: [{cx, cy, fw, fh, confidence}, ...]
    Coordinates are in original frame space.
    """
    import cv2

    detector, det_w, det_h, scale_x, scale_y = detector_tuple

    resized = cv2.resize(frame, (det_w, det_h))
    _, faces = detector.detect(resized)

    results = []
    if faces is None:
        return results

    for face in faces:
        # Scale bbox back to original frame coordinates
        x = int(face[0] * scale_x)
        y = int(face[1] * scale_y)
        w = int(face[2] * scale_x)
        h = int(face[3] * scale_y)
        conf = float(face[-1])

        if w < frame_width * MIN_FACE_RATIO:
            continue

        cx = x + w // 2
        cy = y + h // 2

        results.append({
            "cx": cx,
            "cy": cy,
            "fw": w,
            "fh": h,
            "confidence": round(conf, 3),
        })

    return results


def is_available() -> bool:
    """Check if YuNet model file exists."""
    return os.path.exists(_model_path())
