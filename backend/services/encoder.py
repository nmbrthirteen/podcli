"""
Hardware-accelerated encoder detection and configuration.

Probes system for available encoders:
- macOS: h264_videotoolbox (M-series / Intel GPU)
- NVIDIA: h264_nvenc
- AMD: h264_amf (Windows) / h264_vaapi (Linux)
- Fallback: libx264 (CPU, always available)

Returns optimal FFmpeg encoder flags for the current system.
"""

import subprocess
import platform
import tempfile
import os
import functools
import sys


@functools.lru_cache(maxsize=1)
def detect_encoders() -> dict:
    """
    Detect available hardware encoders.
    """
    system = platform.system()
    available = ["libx264"]

    candidates = []
    if system == "Darwin":
        candidates = ["h264_videotoolbox"]
    elif system == "Linux":
        candidates = ["h264_nvenc", "h264_vaapi"]
    elif system == "Windows":
        candidates = ["h264_nvenc", "h264_amf", "h264_qsv"]

    for enc in candidates:
        if _test_encoder(enc):
            available.append(enc)

    priority = [
        "h264_videotoolbox",
        "h264_nvenc",
        "h264_amf",
        "h264_vaapi",
        "h264_qsv",
        "libx264",
    ]

    best = "libx264"
    for enc in priority:
        if enc in available:
            best = enc
            break

    return {
        "available": available,
        "best": best,
        "best_flags": _get_encoder_flags(best),
        "system": system,
    }


def _test_encoder(encoder: str) -> bool:
    """
    Test if an FFmpeg encoder works by encoding a small real video to a temp file.
    Writing to /dev/null or -f null fails for some HW encoders.
    """
    tmp_out = None
    try:
        tmp_out = tempfile.mktemp(suffix=".mp4")
        flags = _get_encoder_flags(encoder)
        cmd = [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", "color=black:s=320x240:d=0.5:r=24",
            "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono",
            "-t", "0.5",
            *flags,
            "-c:a", "aac",
            "-shortest",
            tmp_out,
        ]
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=15,
        )
        # Check both return code AND that the file was actually created
        return result.returncode == 0 and os.path.exists(tmp_out) and os.path.getsize(tmp_out) > 100
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return False
    finally:
        if tmp_out and os.path.exists(tmp_out):
            try:
                os.remove(tmp_out)
            except OSError:
                pass


def _get_encoder_flags(encoder: str) -> list[str]:
    """
    Get optimal FFmpeg flags for a given encoder.
    Kept minimal to avoid conflicts with filter_complex.
    """
    flags = {
        "h264_videotoolbox": [
            "-c:v", "h264_videotoolbox",
            "-b:v", "6M",              # Bitrate mode (more reliable than -q:v)
            "-allow_sw", "1",          # Allow software fallback
        ],
        "h264_nvenc": [
            "-c:v", "h264_nvenc",
            "-preset", "p4",
            "-cq", "23",
            "-profile:v", "high",
        ],
        "h264_amf": [
            "-c:v", "h264_amf",
            "-quality", "balanced",
            "-rc", "cqp",
            "-qp_i", "23", "-qp_p", "23",
        ],
        "h264_vaapi": [
            "-c:v", "h264_vaapi",
            "-qp", "23",
        ],
        "h264_qsv": [
            "-c:v", "h264_qsv",
            "-preset", "medium",
            "-global_quality", "23",
        ],
        "libx264": [
            "-c:v", "libx264",
            "-crf", "23",
            "-preset", "medium",
        ],
    }
    return flags.get(encoder, flags["libx264"])


def get_video_encode_flags() -> list[str]:
    """Get the best available encoder flags. Main entry point."""
    try:
        info = detect_encoders()
        return info["best_flags"]
    except Exception:
        # Absolute fallback — never let encoder detection break the pipeline
        print("Warning: encoder detection failed, using libx264", file=sys.stderr)
        return ["-c:v", "libx264", "-crf", "23", "-preset", "medium"]


def get_encoder_info() -> dict:
    """Get full encoder detection info (for UI/logging)."""
    try:
        return detect_encoders()
    except Exception:
        return {"available": ["libx264"], "best": "libx264",
                "best_flags": ["-c:v", "libx264", "-crf", "23", "-preset", "medium"],
                "system": platform.system()}
