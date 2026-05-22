"""Standalone ffprobe wrapper used by integration emitters."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any


def probe_media(path: str | Path) -> dict[str, Any]:
    p = str(Path(path).resolve())

    v_cmd = [
        "ffprobe", "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height,r_frame_rate,nb_frames,duration",
        "-show_entries", "format=duration",
        "-of", "json",
        p,
    ]
    v_data = json.loads(subprocess.check_output(v_cmd, text=True))
    v_stream = (v_data.get("streams") or [{}])[0]
    fmt_duration = float((v_data.get("format") or {}).get("duration") or 0.0)

    width = int(v_stream.get("width", 0))
    height = int(v_stream.get("height", 0))

    rfr = v_stream.get("r_frame_rate", "30/1")
    num_s, den_s = rfr.split("/")
    fps_num, fps_den = float(num_s), float(den_s)
    fps = fps_num / fps_den if fps_den > 0 else 30.0

    nb_frames_raw = v_stream.get("nb_frames")
    if nb_frames_raw and str(nb_frames_raw).isdigit() and int(nb_frames_raw) > 0:
        duration_frames = int(nb_frames_raw)
    elif v_stream.get("duration"):
        duration_frames = round(float(v_stream["duration"]) * fps)
    elif fmt_duration > 0:
        duration_frames = round(fmt_duration * fps)
    else:
        raise RuntimeError(f"Could not determine duration for {p}")

    a_cmd = [
        "ffprobe", "-v", "error",
        "-select_streams", "a:0",
        "-show_entries", "stream=channels",
        "-of", "json",
        p,
    ]
    a_data = json.loads(subprocess.check_output(a_cmd, text=True))
    a_streams = a_data.get("streams") or []
    has_audio = len(a_streams) > 0
    audio_channels = int(a_streams[0].get("channels", 0)) if has_audio else 0

    return {
        "width": width,
        "height": height,
        "fps": fps,
        "duration_frames": duration_frames,
        "has_audio": has_audio,
        "audio_channels": audio_channels,
    }
