"""Signal math behind multicam editing: sync, drift, who-speaks-when, and the cut plan.

Everything here works on numpy arrays so it can be tested with synthetic audio.
The orchestration (ffmpeg extraction, sessions, rendering) lives in multicam.py.

Sync runs in two passes. A coarse pass cross-correlates 10 ms onset envelopes
over the full length of both files, which tolerates different mics, gain and EQ.
A fine pass runs GCC-PHAT on raw audio at several checkpoints; fitting a line
through those checkpoints yields both the offset and the clock drift between
devices (cameras and recorders commonly drift a frame every 10-20 minutes).
"""

from __future__ import annotations

import heapq
from dataclasses import dataclass
from typing import Optional

import numpy as np

FRAME_SECONDS = 0.01


def onset_envelope(samples: np.ndarray, sample_rate: int) -> np.ndarray:
    """Half-wave rectified log-energy rise per 10 ms frame, z-scored."""
    hop = int(round(sample_rate * FRAME_SECONDS))
    frames = len(samples) // hop
    if frames < 2:
        return np.zeros(0, dtype=np.float32)
    x = samples[: frames * hop].astype(np.float32).reshape(frames, hop)
    log_energy = np.log10(np.mean(x * x, axis=1) + 1e-9)
    onset = np.maximum(0.0, np.diff(log_energy, prepend=log_energy[0]))
    std = float(onset.std())
    if std < 1e-9:
        return np.zeros(frames, dtype=np.float32)
    return ((onset - onset.mean()) / std).astype(np.float32)


@dataclass
class CoarseMatch:
    lag_seconds: float
    score: float
    peak_ratio: float

    @property
    def ok(self) -> bool:
        return self.score >= 6.0 and self.peak_ratio <= 0.85


def coarse_lag(
    ref_env: np.ndarray,
    src_env: np.ndarray,
    *,
    min_overlap_seconds: float = 20.0,
) -> Optional[CoarseMatch]:
    """Lag (seconds) such that src time 0 sits at that time on the reference.

    score is the peak's robust z-score over all valid lags; peak_ratio is the
    runner-up peak (at least 1 s away) divided by the best one.
    """
    n, m = len(ref_env), len(src_env)
    if n == 0 or m == 0:
        return None
    size = 1 << int(np.ceil(np.log2(n + m)))
    corr = np.fft.irfft(np.fft.rfft(ref_env, size) * np.conj(np.fft.rfft(src_env, size)), size)
    lags = np.concatenate([np.arange(0, n), np.arange(-(m - 1), 0)])
    values = np.concatenate([corr[:n], corr[size - (m - 1):]]) if m > 1 else corr[:n]
    overlap = np.minimum(n, lags + m) - np.maximum(0, lags)
    min_overlap = min(int(min_overlap_seconds / FRAME_SECONDS), int(0.5 * min(n, m)))
    valid = overlap >= max(1, min_overlap)
    if not valid.any():
        return None
    lags, values = lags[valid], values[valid]

    best = int(np.argmax(values))
    peak = float(values[best])
    median = float(np.median(values))
    mad = float(np.median(np.abs(values - median))) * 1.4826 + 1e-9
    guard = int(1.0 / FRAME_SECONDS)
    away = np.abs(lags - lags[best]) > guard
    runner_up = float(values[away].max()) if away.any() else median
    ratio = (runner_up - median) / (peak - median) if peak > median else 1.0
    return CoarseMatch(
        lag_seconds=float(lags[best]) * FRAME_SECONDS,
        score=(peak - median) / mad,
        peak_ratio=max(0.0, float(ratio)),
    )


def gcc_phat(ref: np.ndarray, src: np.ndarray, sample_rate: int, max_shift_seconds: float) -> tuple[float, float]:
    """Shift (seconds) of src inside ref, and the peak's prominence.

    ref must be the src window padded by max_shift on both sides; a return of 0
    means src lines up with the center of ref.
    """
    size = 1 << int(np.ceil(np.log2(len(ref) + len(src))))
    cross = np.fft.rfft(ref.astype(np.float64), size) * np.conj(np.fft.rfft(src.astype(np.float64), size))
    cross /= np.abs(cross) + 1e-12
    corr = np.fft.irfft(cross, size)
    pad = int(round(max_shift_seconds * sample_rate))
    window = corr[: 2 * pad + 1]
    best = int(np.argmax(window))
    peak = float(window[best])
    spread = float(np.std(window)) + 1e-12
    shift = best
    # Parabolic interpolation gets sub-sample precision.
    if 0 < best < len(window) - 1:
        a, b, c = window[best - 1], window[best], window[best + 1]
        denom = a - 2 * b + c
        if abs(denom) > 1e-12:
            shift = best + 0.5 * (a - c) / denom
    return (shift - pad) / sample_rate, peak / spread


@dataclass
class ClockFit:
    offset: float
    speed: float
    residual_ms: float
    checkpoints: int


def fit_clock(points: list[tuple[float, float, float]], *, max_residual: float = 0.02) -> Optional[ClockFit]:
    """Fit timeline = offset + speed * source from (source_s, timeline_s, weight) points.

    Checkpoints further than max_residual seconds from a Theil-Sen line are
    dropped before the weighted least-squares fit.
    A single point yields speed 1. Implausible speeds (over 1000 ppm) fall back
    to the median offset at speed 1, since real clock drift is far smaller.
    """
    if not points:
        return None
    pts = np.array(points, dtype=np.float64)

    def solve(p: np.ndarray) -> tuple[float, float]:
        if len(p) < 2 or np.ptp(p[:, 0]) < 1.0:
            return float(np.median(p[:, 1] - p[:, 0])), 1.0
        w = p[:, 2]
        slope, intercept = np.polyfit(p[:, 0], p[:, 1], 1, w=w)
        return float(intercept), float(slope)

    # Theil-Sen seeds the outlier test; least squares alone lets one bad
    # checkpoint drag the line far enough that good points look bad too.
    slopes = [
        (pts[j, 1] - pts[i, 1]) / (pts[j, 0] - pts[i, 0])
        for i in range(len(pts)) for j in range(i + 1, len(pts))
        if pts[j, 0] - pts[i, 0] >= 1.0
    ]
    speed = float(np.median(slopes)) if slopes else 1.0
    offset = float(np.median(pts[:, 1] - speed * pts[:, 0]))
    keep = np.abs(pts[:, 1] - (offset + speed * pts[:, 0])) <= max_residual
    if keep.any():
        pts = pts[keep]
    offset, speed = solve(pts)
    residual = np.abs(pts[:, 1] - (offset + speed * pts[:, 0]))
    if abs(speed - 1.0) > 1e-3:
        offset, speed = float(np.median(pts[:, 1] - pts[:, 0])), 1.0
        residual = np.abs(pts[:, 1] - (offset + pts[:, 0]))
    return ClockFit(
        offset=offset,
        speed=speed,
        residual_ms=float(residual.max()) * 1000.0,
        checkpoints=len(pts),
    )


def level_db(samples: np.ndarray, sample_rate: int) -> np.ndarray:
    """RMS level in dBFS per 10 ms frame for int16 or float samples."""
    hop = int(round(sample_rate * FRAME_SECONDS))
    frames = len(samples) // hop
    if frames == 0:
        return np.zeros(0, dtype=np.float32)
    x = samples[: frames * hop].astype(np.float32)
    if samples.dtype == np.int16:
        x /= 32768.0
    power = np.mean(x.reshape(frames, hop) ** 2, axis=1)
    return (10.0 * np.log10(power + 1e-10)).astype(np.float32)


def _smooth_power_db(db: np.ndarray, frames: int) -> np.ndarray:
    if frames <= 1 or len(db) == 0:
        return db
    power = 10.0 ** (db / 10.0)
    kernel = np.ones(frames, dtype=np.float32) / frames
    return (10.0 * np.log10(np.convolve(power, kernel, mode="same") + 1e-10)).astype(np.float32)


SILENT = -1
BOTH = -2


def speaker_frames(
    levels: list[np.ndarray],
    *,
    smooth_seconds: float = 0.3,
    speech_window_db: float = 20.0,
    floor_margin_db: float = 10.0,
    both_margin_db: float = 4.0,
) -> np.ndarray:
    """Per-frame dominant speaker index from one timeline-aligned level track per person.

    Each track is normalized to its own typical speech level (95th percentile)
    so a hot guest mic doesn't win every frame. Bleed from the other person
    lands far below that level and loses the argmax. Returns SILENT when nobody
    clears their noise floor and BOTH when two people are within both_margin_db.
    """
    if not levels:
        return np.zeros(0, dtype=np.int32)
    length = min(len(level) for level in levels)
    frames = int(round(smooth_seconds / FRAME_SECONDS))
    stacked = np.stack([_smooth_power_db(level[:length], frames) for level in levels])
    floors = np.percentile(stacked, 20, axis=1, keepdims=True)
    speech = np.percentile(stacked, 95, axis=1, keepdims=True)
    normalized = stacked - speech
    active = (normalized > -speech_window_db) & (stacked > floors + floor_margin_db)
    scores = np.where(active, normalized, -np.inf)

    order = np.argsort(-scores, axis=0)
    best = order[0]
    labels = best.astype(np.int32)
    best_score = np.take_along_axis(scores, order[:1], axis=0)[0]
    labels[~np.isfinite(best_score)] = SILENT
    if len(levels) > 1:
        second_score = np.take_along_axis(scores, order[1:2], axis=0)[0]
        gap = np.where(np.isfinite(second_score), best_score - np.nan_to_num(second_score, neginf=0.0), np.inf)
        both = gap < both_margin_db
        labels[both] = BOTH
    return labels


def segments_to_frames(segments: list[dict], people: list[str], length: int) -> np.ndarray:
    """Rasterize diarized {start, end, person} turns into the speaker_frames label format."""
    labels = np.full(length, SILENT, dtype=np.int32)
    index = {person: i for i, person in enumerate(people)}
    for seg in segments:
        who = index.get(seg.get("person"))
        if who is None:
            continue
        a = max(0, int(float(seg["start"]) / FRAME_SECONDS))
        b = min(length, int(float(seg["end"]) / FRAME_SECONDS))
        if b <= a:
            continue
        current = labels[a:b]
        labels[a:b] = np.where((current != SILENT) & (current != who), BOTH, who)
    return labels


def _runs(values: np.ndarray) -> list[list[int]]:
    """Run-length encode into [value, start_frame, end_frame] lists."""
    if len(values) == 0:
        return []
    change = np.flatnonzero(np.diff(values)) + 1
    starts = np.concatenate([[0], change])
    ends = np.concatenate([change, [len(values)]])
    return [[int(values[s]), int(s), int(e)] for s, e in zip(starts, ends)]


@dataclass
class Camera:
    source_id: str
    person: str
    start: float
    end: float

    def covers(self, a: float, b: float) -> bool:
        return self.start <= a + 1e-6 and self.end >= b - 1e-6


def plan_cuts(
    labels: np.ndarray,
    people: list[str],
    cameras: list[Camera],
    *,
    range_start: float,
    range_end: float,
    min_shot: float = 2.0,
    max_shot: float = 30.0,
    wide_insert: float = 4.0,
) -> list[dict]:
    """Turn per-frame speaker labels into [{start, end, source_id}] on the timeline.

    Silence holds the previous shot; crosstalk goes wide. Shots shorter than
    min_shot fold into a neighbour whose camera also covers them, and a single
    shot longer than max_shot gets a wide_insert-second wide shot in the middle
    when a wide camera exists. Split camera files (part 1, part 2) are handled
    because every decision checks which camera actually covers the moment.
    """
    if not cameras or range_end <= range_start:
        return []
    wide = [c for c in cameras if c.person == "wide"]
    by_person = {p: [c for c in cameras if c.person == p] for p in people}

    def pick(target: str, a: float, b: float) -> Optional[Camera]:
        pools = []
        if target == "wide":
            pools = [wide]
        else:
            pools = [by_person.get(target, []), wide]
        pools.append(cameras)
        for pool in pools:
            for cam in pool:
                if cam.covers(a, b):
                    return cam
        return None

    first = int(range_start / FRAME_SECONDS)
    last = int(np.ceil(range_end / FRAME_SECONDS))
    window = np.full(last - first, SILENT, dtype=np.int32)
    avail = labels[max(0, first):min(len(labels), last)]
    lead = max(0, -first)
    window[lead:lead + len(avail)] = avail

    targets: list[str] = []
    previous = "wide" if wide else (people[0] if people else "wide")
    for value in window:
        if value == SILENT:
            targets.append(previous)
            continue
        current = "wide" if value == BOTH else people[value]
        targets.append(current)
        previous = current
    codes = {name: i for i, name in enumerate(dict.fromkeys(targets))}
    names = list(codes)
    shot_runs = _runs(np.array([codes[t] for t in targets], dtype=np.int32))

    # Camera coverage can change inside a run (a camera file ends); split there.
    edges = sorted({c.start for c in cameras} | {c.end for c in cameras})
    shots: list[dict] = []
    for code, a, b in shot_runs:
        start = range_start + a * FRAME_SECONDS
        end = min(range_end, range_start + b * FRAME_SECONDS)
        cuts = [start] + [e for e in edges if start < e < end] + [end]
        for s, e in zip(cuts, cuts[1:]):
            cam = pick(names[code], s, e)
            if cam is None:
                continue
            if shots and shots[-1]["source_id"] == cam.source_id and abs(shots[-1]["end"] - s) < 1e-6:
                shots[-1]["end"] = e
            else:
                shots.append({"start": s, "end": e, "source_id": cam.source_id})

    by_id = {c.source_id: c for c in cameras}
    shots = _fold_short_shots(shots, min_shot, by_id)

    if wide and max_shot > 0 and wide_insert > 0:
        broken: list[dict] = []
        for shot in shots:
            length = shot["end"] - shot["start"]
            is_wide = by_id[shot["source_id"]].person == "wide"
            if is_wide or length <= max_shot:
                broken.append(shot)
                continue
            # Spacing inserts at least a shortest shot apart keeps them from
            # overlapping when the wide cutaway is longer than max_shot.
            pieces = int(length // max(max_shot, wide_insert + min_shot))
            step = length / (pieces + 1)
            cursor = shot["start"]
            for k in range(1, pieces + 1):
                mid = shot["start"] + step * k
                a, b = mid - wide_insert / 2, mid + wide_insert / 2
                if a - cursor < min_shot or shot["end"] - b < min_shot:
                    continue
                cam = pick("wide", a, b)
                if cam is None or cam.person != "wide":
                    continue
                broken.append({**shot, "start": cursor, "end": a})
                broken.append({"start": a, "end": b, "source_id": cam.source_id})
                cursor = b
            broken.append({**shot, "start": cursor, "end": shot["end"]})
        shots = broken

    return [
        {"start": round(s["start"], 3), "end": round(s["end"], 3), "source_id": s["source_id"]}
        for s in shots
        if s["end"] - s["start"] > 1e-3
    ]


def _merge_neighbours(shots: list[dict]) -> list[dict]:
    merged: list[dict] = []
    for s in shots:
        if merged and merged[-1]["source_id"] == s["source_id"] and abs(merged[-1]["end"] - s["start"]) < 1e-6:
            merged[-1]["end"] = s["end"]
        else:
            merged.append(s)
    return merged


def _fold_short_shots(shots: list[dict], min_shot: float, by_id: dict[str, Camera]) -> list[dict]:
    """Fold shots under min_shot into their longer neighbour whose camera covers them.

    Always folds the globally shortest shot next, so flickery crosstalk
    consolidates into one wide shot before the long shots around it grow into
    it. A heap over a linked list keeps that O(n log n) on 3-hour episodes.
    """
    shots = _merge_neighbours(shots)
    n = len(shots)
    prev = list(range(-1, n - 1))
    nxt = list(range(1, n + 1))
    nxt[-1:] = [-1] if n else []
    alive = [True] * n
    version = [0] * n
    heap: list[tuple[float, int, int]] = []

    def length(i: int) -> float:
        return shots[i]["end"] - shots[i]["start"]

    def push(i: int) -> None:
        if 0 <= i and alive[i] and length(i) < min_shot:
            heapq.heappush(heap, (length(i), i, version[i]))

    def unlink(i: int) -> None:
        alive[i] = False
        if prev[i] >= 0:
            nxt[prev[i]] = nxt[i]
        if nxt[i] >= 0:
            prev[nxt[i]] = prev[i]

    for i in range(n):
        push(i)
    while heap:
        _, i, v = heapq.heappop(heap)
        if not alive[i] or v != version[i]:
            continue
        shot = shots[i]
        candidates = [
            j for j in (prev[i], nxt[i])
            if j >= 0
            and abs((shots[j]["end"] if j == prev[i] else shots[j]["start"]) - (shot["start"] if j == prev[i] else shot["end"])) < 1e-6
            and by_id[shots[j]["source_id"]].covers(shot["start"], shot["end"])
        ]
        if not candidates:
            continue
        j = max(candidates, key=length)
        if j == prev[i]:
            shots[j]["end"] = shot["end"]
        else:
            shots[j]["start"] = shot["start"]
        unlink(i)
        # The fold can leave j touching a shot on the same camera; join them.
        for k in (prev[j], nxt[j]):
            if k >= 0 and shots[k]["source_id"] == shots[j]["source_id"]:
                shots[j]["start"] = min(shots[j]["start"], shots[k]["start"])
                shots[j]["end"] = max(shots[j]["end"], shots[k]["end"])
                unlink(k)
        # j grew, so its old heap entry is stale; its neighbours may now have a
        # camera to fold into that they lacked before.
        version[j] += 1
        for k in (j, prev[j], nxt[j]):
            push(k)

    out, i = [], next((k for k in range(n) if alive[k] and prev[k] == -1), -1)
    while i >= 0:
        out.append(shots[i])
        i = nxt[i]
    return out


def speech_bounds(labels: np.ndarray) -> Optional[tuple[float, float]]:
    """First and last frame where anyone speaks, in seconds."""
    spoken = np.flatnonzero(labels != SILENT)
    if len(spoken) == 0:
        return None
    return float(spoken[0]) * FRAME_SECONDS, float(spoken[-1] + 1) * FRAME_SECONDS
