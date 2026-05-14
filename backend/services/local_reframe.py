"""Per-clip mouth-motion reframe planner."""

from __future__ import annotations

import logging
import os
import re
import tempfile
from pathlib import Path
from typing import Optional

from utils.proc import run as proc_run, ProcError

log = logging.getLogger("podcli.local_reframe")


_PTS_RE = re.compile(r"pts_time:([0-9.]+)")
_YAVG_RE = re.compile(r"lavfi\.signalstats\.YAVG=([0-9.]+)")


def count_scene_cuts(video_path: str, threshold: float = 0.35) -> int:
    """Count scene cuts in the clip via FFmpeg's `select='gt(scene,...)'` filter.

    Returns the number of detected cuts. Returns 0 if FFmpeg fails - callers
    should treat 0 as "no info" and proceed with their default plan.
    """
    try:
        result = proc_run(
            [
                "ffmpeg",
                "-i", str(video_path),
                "-filter:v", f"select='gt(scene,{threshold})',showinfo",
                "-f", "null", "-",
            ],
            timeout=180,
            check=False,
        )
    except ProcError as exc:
        log.warning("scene-cut count failed: %s", exc)
        return 0
    if result.returncode != 0:
        return 0
    return len(_PTS_RE.findall(result.stderr or ""))


def parse_motion_metadata(path: Path) -> tuple[list[float], list[float]]:
    """Parse FFmpeg metadata-print output into (times, YAVG values) lists."""
    times: list[float] = []
    values: list[float] = []
    current_time: Optional[float] = None
    try:
        raw = path.read_text(errors="ignore")
    except OSError:
        return [], []
    for line in raw.splitlines():
        pts_match = _PTS_RE.search(line)
        if pts_match:
            current_time = float(pts_match.group(1))
            continue
        val_match = _YAVG_RE.search(line)
        if val_match and current_time is not None:
            times.append(current_time)
            values.append(float(val_match.group(1)))
            current_time = None
    return times, values


def smooth_values(values: list[float], window: int = 15) -> list[float]:
    """Box-filter smoothing, no scipy dependency."""
    if not values:
        return []
    half = window // 2
    smoothed: list[float] = []
    n = len(values)
    for i in range(n):
        lo = max(0, i - half)
        hi = min(n, i + half + 1)
        smoothed.append(sum(values[lo:hi]) / (hi - lo))
    return smoothed


def build_speaker_timeline_from_motion(
    times: list[float],
    left_values: list[float],
    right_values: list[float],
    min_duration: float = 1.0,
    margin: float = 1.15,
) -> list[dict]:
    """Build a list of {start, end, speaker: "left"|"right"} segments.

    Inputs are per-frame YAVG motion values for the two mouth ROIs. We
    normalize each side by its own mean (so we compare *relative* activity,
    not absolute brightness), smooth with a box filter, then walk the
    timeline with hysteresis: only flip the active speaker when the other
    side's smoothed motion exceeds the current side by `margin`x. Finally
    merge segments shorter than `min_duration` into their neighbors so a
    half-second laugh from the listener doesn't yank the camera.
    """
    if not times or len(left_values) != len(right_values) or not left_values:
        return []
    n = min(len(times), len(left_values), len(right_values))
    times = times[:n]
    left_values = left_values[:n]
    right_values = right_values[:n]

    def _normalize(values: list[float]) -> list[float]:
        mean_value = sum(values) / max(len(values), 1)
        if mean_value <= 0:
            return [0.0] * len(values)
        return [v / mean_value for v in values]

    left = smooth_values(_normalize(left_values))
    right = smooth_values(_normalize(right_values))
    if not left or not right:
        return []

    current = 0 if left[0] >= right[0] else 1
    states: list[int] = []
    for lv, rv in zip(left, right):
        if current == 0 and rv > lv * margin:
            current = 1
        elif current == 1 and lv > rv * margin:
            current = 0
        states.append(current)

    segments: list[dict] = []
    i = 0
    while i < len(states):
        j = i
        while j + 1 < len(states) and states[j + 1] == states[i]:
            j += 1
        seg_start = times[i]
        seg_end = times[min(j + 1, len(times) - 1)]
        if seg_end <= seg_start:
            seg_end = seg_start + 0.05
        segments.append({
            "start": seg_start,
            "end": seg_end,
            "speaker": "left" if states[i] == 0 else "right",
        })
        i = j + 1

    merged: list[dict] = []
    for seg in segments:
        too_short = (seg["end"] - seg["start"]) < min_duration
        if merged and (too_short or merged[-1]["speaker"] == seg["speaker"]):
            merged[-1]["end"] = seg["end"]
            continue
        merged.append(dict(seg))
    return merged


def build_pan_x_expression(timeline: list[dict], left_x: int, right_x: int) -> str:
    """Compile the speaker timeline into one nested if(lt(t,...)) expression.

    The expression is evaluated by FFmpeg's crop filter per-frame, so the
    crop window snaps to the active speaker without keyframe interpolation.
    No animation by design. Smooth pans on speaker switches produce a
    "drunken cameraman" effect on split-screen footage; hard cuts read as
    intentional editing.
    """
    if not timeline:
        return str(left_x)

    def x_for(speaker: str) -> int:
        return left_x if speaker == "left" else right_x

    expr = str(x_for(timeline[-1]["speaker"]))
    for seg in reversed(timeline[:-1]):
        # Escape commas so the expression is safe inside a filtergraph.
        expr = (
            f"if(lt(t\\,{seg['end']:.4f})\\,{x_for(seg['speaker'])}\\,{expr})"
        )
    return expr


def _safe_clip(value: int, lo: int, hi: int) -> int:
    return max(lo, min(int(value), hi))


def _even(value: int) -> int:
    return value - (value % 2)


def detect_local_speaker_reframe_plan(
    clip_path: str,
    face_map: dict,
    target_ratio: float = 9 / 16,
    max_scene_cuts: int = 2,
    motion_timeout: float = 120.0,
) -> Optional[dict]:
    """Plan a per-clip pan based on local mouth motion.

    Returns:
      {
        "mode": "pan",
        "width": int, "height": int,
        "crop_w": int, "crop_h": int,
        "left_x": int, "right_x": int,
        "x_expression": str,        # ready to drop into ffmpeg crop=...:x='...'
        "timeline": [...],
        "scene_cuts": int,
      }
    or None when the plan doesn't apply (not split-screen, too many cuts,
    motion analysis failed, single-side timeline).

    The caller is responsible for falling back to the existing reframe
    pipeline when this returns None.
    """
    if not face_map:
        return None
    clusters = face_map.get("clusters") or []
    if len(clusters) < 2 or not face_map.get("is_split_screen"):
        return None

    width = int(face_map.get("video_width") or 0)
    height = int(face_map.get("video_height") or 0)
    if width <= 0 or height <= 0:
        return None

    if width / max(height, 1) <= 1.2:
        return None

    cuts = count_scene_cuts(clip_path)
    if cuts > max_scene_cuts:
        log.info("local-reframe skip: %d scene cuts in clip", cuts)
        return None

    left_cluster, right_cluster = clusters[0], clusters[1]
    left_cx = int(left_cluster.get("center_x", width // 4))
    right_cx = int(right_cluster.get("center_x", 3 * width // 4))
    if abs(right_cx - left_cx) < width * 0.15:
        return None

    # PIP guard: a face ratio over 3x usually means inset, not split-screen.
    obs = face_map.get("observations") or []
    def _mean_face_w_for(side_cx: int) -> float:
        widths = [
            float(o.get("face_width") or 0)
            for o in obs
            if abs(int(o.get("face_center_x", 0)) - side_cx) < width * 0.15
            and o.get("face_width")
        ]
        return sum(widths) / len(widths) if widths else 0.0

    left_w = _mean_face_w_for(left_cx)
    right_w = _mean_face_w_for(right_cx)
    if left_w > 0 and right_w > 0:
        ratio = max(left_w, right_w) / max(1.0, min(left_w, right_w))
        if ratio > 3.0:
            log.info(
                "local-reframe skip: face-size ratio %.1f looks like PIP",
                ratio,
            )
            return None

    crop_w = _even(min(width, int(height * target_ratio)))
    crop_h = _even(height)
    max_x = max(0, width - crop_w)
    left_x = _safe_clip(left_cx - crop_w // 2, 0, max_x)
    right_x = _safe_clip(right_cx - crop_w // 2, 0, max_x)

    def _mouth_roi(cluster: dict, side_cx: int) -> tuple[int, int, int, int]:
        approx_face_w = 0
        observations = face_map.get("observations") or []
        side_obs = [
            o for o in observations
            if abs(int(o.get("face_center_x", 0)) - side_cx) < width * 0.15
        ]
        if side_obs:
            approx_face_w = int(
                sum(o.get("face_width", 0) for o in side_obs) / len(side_obs)
            )
        if approx_face_w < 60:
            approx_face_w = max(80, width // 8)
        cy_values = [o.get("face_center_y", height // 2) for o in side_obs]
        cy = int(sum(cy_values) / len(cy_values)) if cy_values else height // 2
        roi_w = _even(max(80, int(approx_face_w * 1.2)))
        roi_h = _even(max(70, int(approx_face_w * 0.85)))
        roi_x = _safe_clip(side_cx - roi_w // 2, 0, max(0, width - roi_w))
        roi_y = _safe_clip(cy, 0, max(0, height - roi_h))
        return roi_x, roi_y, roi_w, roi_h

    lx, ly, lw, lh = _mouth_roi(left_cluster, left_cx)
    rx, ry, rw, rh = _mouth_roi(right_cluster, right_cx)

    with tempfile.TemporaryDirectory(prefix="podcli_reframe_") as tmp:
        left_motion = Path(tmp) / "left.txt"
        right_motion = Path(tmp) / "right.txt"
        filter_complex = (
            f"[0:v]split=2[l][r];"
            f"[l]crop={lw}:{lh}:{lx}:{ly},format=gray,"
            f"tblend=all_mode=difference,signalstats,"
            f"metadata=mode=print:key=lavfi.signalstats.YAVG"
            f":file={_escape_path_for_filter(left_motion)}[lo];"
            f"[r]crop={rw}:{rh}:{rx}:{ry},format=gray,"
            f"tblend=all_mode=difference,signalstats,"
            f"metadata=mode=print:key=lavfi.signalstats.YAVG"
            f":file={_escape_path_for_filter(right_motion)}[ro]"
        )
        try:
            proc_run(
                [
                    "ffmpeg", "-y",
                    "-i", str(clip_path),
                    "-filter_complex", filter_complex,
                    "-map", "[lo]", "-f", "null", "-",
                    "-map", "[ro]", "-f", "null", "-",
                ],
                timeout=motion_timeout,
                check=False,
            )
        except ProcError as exc:
            log.warning("local-reframe motion ffmpeg failed: %s", exc)
            return None

        if not left_motion.exists() or not right_motion.exists():
            return None
        times, left_values = parse_motion_metadata(left_motion)
        _, right_values = parse_motion_metadata(right_motion)

    timeline = build_speaker_timeline_from_motion(times, left_values, right_values)
    if len(timeline) < 2:
        return None

    x_expression = build_pan_x_expression(timeline, left_x, right_x)

    return {
        "mode": "pan",
        "width": width,
        "height": height,
        "crop_w": crop_w,
        "crop_h": crop_h,
        "left_x": left_x,
        "right_x": right_x,
        "x_expression": x_expression,
        "timeline": timeline,
        "scene_cuts": cuts,
    }


def _escape_path_for_filter(path: Path) -> str:
    """Escape a filesystem path for use inside an FFmpeg filter argument."""
    return (
        str(path)
        .replace("\\", "\\\\")
        .replace(":", "\\:")
        .replace("'", "\\'")
        .replace(" ", "\\ ")
    )
