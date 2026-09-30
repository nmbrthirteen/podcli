import os
import sys

import numpy as np

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BACKEND_ROOT = os.path.join(ROOT, "backend")
if BACKEND_ROOT not in sys.path:
    sys.path.insert(0, BACKEND_ROOT)

from services.multicam_signal import (  # noqa: E402
    BOTH,
    SILENT,
    Camera,
    coarse_lag,
    fit_clock,
    gcc_phat,
    level_db,
    onset_envelope,
    plan_cuts,
    segments_to_frames,
    speaker_frames,
    speech_bounds,
)

RATE = 8000


def speechlike(seconds: float, seed: int) -> np.ndarray:
    """Noise gated into irregular syllable-length bursts, like talking."""
    rng = np.random.default_rng(seed)
    n = int(seconds * RATE)
    gate = np.zeros(n, dtype=np.float32)
    t = 0
    while t < n:
        burst = int(rng.uniform(0.08, 0.35) * RATE)
        gap = int(rng.uniform(0.05, 0.6) * RATE)
        gate[t:t + burst] = rng.uniform(0.3, 1.0)
        t += burst + gap
    return (rng.standard_normal(n).astype(np.float32) * gate * 8000).astype(np.float32)


def other_device(x: np.ndarray, seed: int) -> np.ndarray:
    """Same sound through another mic: lowpassed, quieter, with its own hiss."""
    rng = np.random.default_rng(seed)
    kernel = np.ones(6, dtype=np.float32) / 6
    return np.convolve(x, kernel, mode="same") * 0.4 + rng.standard_normal(len(x)).astype(np.float32) * 150


def test_coarse_lag_finds_a_late_starting_source():
    ref = speechlike(180, seed=1)
    src = other_device(ref[40 * RATE:150 * RATE], seed=2)
    match = coarse_lag(onset_envelope(ref, RATE), onset_envelope(src, RATE))
    assert match is not None and match.ok
    assert abs(match.lag_seconds - 40.0) <= 0.02


def test_coarse_lag_finds_a_source_that_started_before_the_reference():
    full = speechlike(200, seed=3)
    ref = full[30 * RATE:]
    src = other_device(full[:120 * RATE], seed=4)
    match = coarse_lag(onset_envelope(ref, RATE), onset_envelope(src, RATE))
    assert match is not None and match.ok
    assert abs(match.lag_seconds + 30.0) <= 0.02


def test_unrelated_audio_is_not_reported_as_synced():
    match = coarse_lag(onset_envelope(speechlike(120, 5), RATE), onset_envelope(speechlike(90, 6), RATE))
    assert match is not None and not match.ok


def test_gcc_phat_refines_to_sub_millisecond():
    ref = speechlike(30, seed=7)
    shift = 0.1234
    start = int((5 + shift) * RATE)
    src = other_device(ref[start:start + 10 * RATE], seed=8)
    pad = int(0.25 * RATE)
    window = ref[5 * RATE - pad:5 * RATE + 10 * RATE + pad]
    found, prominence = gcc_phat(window, src, RATE, 0.25)
    assert abs(found - shift) < 0.001
    assert prominence > 10


def test_fit_clock_recovers_drift_and_drops_an_outlier():
    speed = 1.0 + 80e-6
    pts = [(s, 12.5 + s * speed, 1.0) for s in (60, 1200, 2400, 3600, 4800)]
    pts.append((3000, 12.5 + 3000 * speed + 0.4, 1.0))
    fit = fit_clock(pts)
    assert fit is not None
    assert abs(fit.offset - 12.5) < 1e-4
    assert abs(fit.speed - speed) < 1e-7
    assert fit.checkpoints == 5


def test_fit_clock_rejects_implausible_speed():
    fit = fit_clock([(0, 5.0, 1.0), (100, 106.0, 1.0)])
    assert fit is not None and fit.speed == 1.0


def _two_person_levels():
    host = speechlike(20, seed=10)
    guest = speechlike(20, seed=11)
    host[10 * RATE:] = 0
    guest[:10 * RATE] = 0
    # Each mic hears the other person 20 dB down, and the guest mic runs hot.
    host_mic = host + guest * 0.1
    guest_mic = (guest + host * 0.1) * 3
    return level_db(host_mic.astype(np.float32), RATE), level_db(guest_mic.astype(np.float32), RATE)


def test_speaker_frames_ignores_bleed_and_mic_gain():
    labels = speaker_frames(list(_two_person_levels()))
    first, second = labels[100:900], labels[1100:1900]
    assert (first == 0).mean() > 0.6 and (first == 1).mean() < 0.05
    assert (second == 1).mean() > 0.6 and (second == 0).mean() < 0.05


def test_speaker_frames_marks_crosstalk_as_both():
    a = speechlike(10, seed=12)
    b = speechlike(10, seed=13)
    labels = speaker_frames([level_db(a, RATE), level_db(b, RATE)])
    assert (labels == BOTH).mean() > 0.1


def test_segments_to_frames_marks_overlap():
    labels = segments_to_frames(
        [{"start": 0, "end": 2, "person": "host"}, {"start": 1, "end": 3, "person": "guest"}],
        ["host", "guest"],
        400,
    )
    assert labels[50] == 0 and labels[150] == BOTH and labels[250] == 1 and labels[350] == SILENT


def _labels(pattern: list[tuple[int, float]]) -> np.ndarray:
    out = []
    for value, seconds in pattern:
        out += [value] * int(round(seconds / 0.01))
    return np.array(out, dtype=np.int32)


CAMS = [
    Camera("cam_host", "host", 0, 100),
    Camera("cam_guest", "guest", 0, 100),
    Camera("cam_wide", "wide", 0, 100),
]


def test_plan_cuts_follows_the_speaker_and_folds_short_blips():
    labels = _labels([(0, 10), (1, 0.5), (0, 5), (SILENT, 2), (1, 10)])
    cuts = plan_cuts(labels, ["host", "guest"], CAMS, range_start=0, range_end=27.5, min_shot=2.0)
    assert [c["source_id"] for c in cuts] == ["cam_host", "cam_guest"]
    assert cuts[0]["start"] == 0 and cuts[-1]["end"] == 27.5
    assert abs(cuts[1]["start"] - 17.5) < 0.02


def test_plan_cuts_goes_wide_on_crosstalk():
    labels = _labels([(0, 6), (BOTH, 3), (1, 6)])
    cuts = plan_cuts(labels, ["host", "guest"], CAMS, range_start=0, range_end=15)
    assert [c["source_id"] for c in cuts] == ["cam_host", "cam_wide", "cam_guest"]


def test_plan_cuts_breaks_long_monologues_with_a_wide_shot():
    labels = _labels([(0, 70)])
    cuts = plan_cuts(labels, ["host", "guest"], CAMS, range_start=0, range_end=70, max_shot=30, wide_insert=4)
    ids = [c["source_id"] for c in cuts]
    assert ids.count("cam_wide") == 2
    assert all(c["end"] - c["start"] <= 30 for c in cuts)


def test_plan_cuts_switches_to_the_next_camera_part():
    cams = [
        Camera("guest_part1", "guest", 0, 20),
        Camera("guest_part2", "guest", 20, 60),
        Camera("cam_host", "host", 0, 60),
    ]
    labels = _labels([(1, 40)])
    cuts = plan_cuts(labels, ["host", "guest"], cams, range_start=0, range_end=40, max_shot=0)
    assert [c["source_id"] for c in cuts] == ["guest_part1", "guest_part2"]
    assert cuts[0]["end"] == 20 and cuts[1]["start"] == 20


def test_plan_cuts_respects_a_range_that_starts_mid_timeline():
    labels = _labels([(0, 10), (1, 10)])
    cuts = plan_cuts(labels, ["host", "guest"], CAMS, range_start=5, range_end=15)
    assert cuts[0] == {"start": 5.0, "end": 10.0, "source_id": "cam_host"}
    assert cuts[1]["source_id"] == "cam_guest" and cuts[1]["end"] == 15.0


def test_speech_bounds():
    assert speech_bounds(_labels([(SILENT, 3), (0, 2), (SILENT, 1)])) == (3.0, 5.0)
    assert speech_bounds(_labels([(SILENT, 1)])) is None


def test_plan_cuts_never_overlaps_wide_inserts():
    labels = _labels([(0, 20)])
    cuts = plan_cuts(labels, ["host", "guest"], CAMS, range_start=0, range_end=20, max_shot=3, wide_insert=4)
    assert all(a["end"] <= b["start"] + 1e-6 for a, b in zip(cuts, cuts[1:]))
    assert abs(sum(c["end"] - c["start"] for c in cuts) - 20) < 1e-6


def test_flickering_crosstalk_becomes_one_wide_shot():
    rng = np.random.default_rng(1)
    flicker = rng.choice([BOTH, BOTH, 0, 1], size=500).astype(np.int32)
    labels = np.concatenate([_labels([(0, 15)]), flicker, _labels([(1, 10)])])
    cuts = plan_cuts(labels, ["host", "guest"], CAMS, range_start=0, range_end=30, max_shot=0)
    assert [c["source_id"] for c in cuts] == ["cam_host", "cam_wide", "cam_guest"]
    assert cuts[1]["end"] - cuts[1]["start"] > 3.0


def test_plan_cuts_scales_to_a_three_hour_episode():
    import time

    rng = np.random.default_rng(0)
    runs = rng.integers(5, 150, size=40000)
    labels = np.repeat(rng.choice([0, 1, BOTH], size=len(runs)).astype(np.int32), runs)
    cams = [Camera("a", "host", 0, 11000), Camera("b", "guest", 0, 11000), Camera("w", "wide", 0, 11000)]
    t0 = time.perf_counter()
    cuts = plan_cuts(labels, ["host", "guest"], cams, range_start=0, range_end=len(labels) / 100)
    assert time.perf_counter() - t0 < 5
    assert all(c["end"] - c["start"] >= 2.0 - 1e-6 for c in cuts[1:-1])
