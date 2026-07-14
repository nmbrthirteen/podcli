"""Multi-signal saliency engine — fuse channels, peak-pick, expand reactions, snap.

For profiles whose candidate source is "saliency" (party, action), moments are
generated from a fused interestingness curve instead of from an LLM reading the
transcript. This is what lets podcli auto-cut highlights from footage with no useful
transcript (party videos, action).

Each channel is normalized against THIS video's own distribution (never a global
scale) so different recordings, rooms and mic levels compare fairly, then combined by
the active profile's weights. Peaks on the fused curve become candidate clips; a peak
driven by a laugh or cheer is expanded backwards to capture the moment that caused the
reaction, since the funny thing happens just before people react to it.
"""

from typing import Optional, Callable

import numpy as np

from services.profiles import get_profile, ContentProfile
from services.audio_analyzer import extract_audio_energy
from services.audio_events import extract_audio_events, is_available as audio_events_available

GRID_HZ = 1.0  # common time grid; energy is per-second, so 1 Hz is the natural rate


def _robust_z(x: np.ndarray) -> np.ndarray:
    """Median/MAD normalization — resistant to the heavy tails of RMS and reactions."""
    if x.size == 0:
        return x
    med = np.median(x)
    mad = np.median(np.abs(x - med))
    scale = 1.4826 * mad if mad > 1e-9 else (np.std(x) or 1.0)
    return (x - med) / scale


def _energy_curve(energy_data: list[dict], n_bins: int) -> np.ndarray:
    """Per-second loudness onto the grid; silence (<= -60 dB) floored so it doesn't win."""
    curve = np.full(n_bins, -60.0)
    for e in energy_data:
        b = int(e["time"] * GRID_HZ)
        if 0 <= b < n_bins:
            curve[b] = max(curve[b], e.get("rms_db", -60.0))
    return curve


def _reaction_curve(events_data: list[dict], n_bins: int) -> np.ndarray:
    """Peak reaction (laugh/cheer/scream) per grid bin."""
    curve = np.zeros(n_bins)
    for e in events_data:
        b = int(e["time"] * GRID_HZ)
        if 0 <= b < n_bins:
            level = max(e.get("laughter", 0), e.get("cheering", 0), e.get("screaming", 0))
            curve[b] = max(curve[b], level)
    return curve


def _dilate(curve: np.ndarray, radius_bins: int) -> np.ndarray:
    """Spread each spike to its neighbors (grayscale dilation) so a brief, narrow
    reaction still aligns with, and can win, the fused peak near it."""
    if radius_bins <= 0 or curve.size == 0:
        return curve
    out = curve.copy()
    for r in range(1, radius_bins + 1):
        out[r:] = np.maximum(out[r:], curve[:-r])
        out[:-r] = np.maximum(out[:-r], curve[r:])
    return out


def fuse_channels(channels: dict[str, np.ndarray], profile: ContentProfile) -> np.ndarray:
    """Weighted sum of per-video-normalized channels, weights renormalized over what exists."""
    present = {k: v for k, v in channels.items() if profile.channel_weights.get(k, 0) > 0 and v.size}
    if not present:
        return np.zeros(next(iter(channels.values())).size if channels else 0)
    total_w = sum(profile.channel_weights[k] for k in present)
    fused = None
    for k, curve in present.items():
        w = profile.channel_weights[k] / total_w
        contrib = w * _robust_z(curve)
        fused = contrib if fused is None else fused + contrib
    return fused


def pick_peaks(curve: np.ndarray, height: float, min_gap_bins: int) -> list[int]:
    """Local maxima above `height`, then greedy non-maximum suppression by min gap.

    Peaks are taken in descending value order and a lower peak is dropped if it falls
    within min_gap_bins of an already-chosen higher one (1-D NMS).
    """
    if curve.size == 0:
        return []
    candidates = [
        i for i in range(1, len(curve) - 1)
        if curve[i] >= curve[i - 1] and curve[i] >= curve[i + 1] and curve[i] >= height
    ]
    if curve[0] >= height and (len(curve) == 1 or curve[0] > curve[1]):
        candidates.append(0)
    if len(curve) > 1 and curve[-1] >= height and curve[-1] > curve[-2]:
        candidates.append(len(curve) - 1)
    candidates.sort(key=lambda i: curve[i], reverse=True)
    chosen: list[int] = []
    for i in candidates:
        if all(abs(i - j) >= min_gap_bins for j in chosen):
            chosen.append(i)
    return sorted(chosen)


def _snap_to_quiet(target_sec: float, energy_curve: np.ndarray, window_sec: float = 1.5) -> float:
    """Nudge a boundary to the quietest second nearby, so cuts land in a lull not mid-action."""
    if energy_curve.size == 0:
        return target_sec
    center = int(round(target_sec * GRID_HZ))
    lo = max(0, center - int(window_sec * GRID_HZ))
    hi = min(len(energy_curve), center + int(window_sec * GRID_HZ) + 1)
    if lo >= hi:
        return target_sec
    local = energy_curve[lo:hi]
    return (lo + int(np.argmin(local))) / GRID_HZ


def sentences_from_words(words: list[dict]) -> list[dict]:
    """Group word-level timestamps into sentences on terminal punctuation.

    Whisper *segments* often straddle a sentence boundary ("...was. But then..."), so
    snapping to them still starts a clip mid-thought. Splitting on .?! gives real
    sentence edges to snap to.
    """
    sents: list[dict] = []
    start = None
    end = None
    for w in words:
        text = str(w.get("word", "")).strip()
        if not text:
            continue
        if start is None:
            start = w.get("start", 0.0)
        end = w.get("end", start)
        if text.endswith((".", "?", "!")):
            sents.append({"start": start, "end": end})
            start = None
    if start is not None:
        sents.append({"start": start, "end": end})
    return sents


def _seg_index_at(t: float, segments: list[dict]) -> int:
    """Index of the segment covering t, else the nearest segment start."""
    for i, s in enumerate(segments):
        if s.get("start", 0) <= t <= s.get("end", 0):
            return i
    return min(range(len(segments)), key=lambda i: abs(segments[i].get("start", 0) - t))


def _sentence_window(
    peak_sec: float,
    back_sec: float,
    fwd_sec: float,
    segments: list[dict],
    min_dur: float,
    max_dur: float,
) -> tuple[float, float]:
    """Build a clip out of whole segments (sentences) so it never cuts mid-thought.

    Starts at a sentence boundary, extends back ~back_sec and forward ~fwd_sec (and
    enough to clear min_dur), always snapping to segment edges and never exceeding
    max_dur.
    """
    idx = _seg_index_at(peak_sec, segments)
    start = segments[idx].get("start", peak_sec)
    end = segments[idx].get("end", peak_sec)
    i = idx
    while i > 0 and (peak_sec - segments[i - 1]["start"]) <= back_sec and (end - segments[i - 1]["start"]) <= max_dur:
        i -= 1
        start = segments[i]["start"]
    j = idx
    while (
        j < len(segments) - 1
        and (segments[j + 1]["end"] - start) <= max_dur
        and ((end - start) < min_dur or (segments[j]["end"] - peak_sec) < fwd_sec)
    ):
        j += 1
        end = segments[j]["end"]
    return start, end


def _window_for_peak(
    peak_sec: float,
    reaction_level: float,
    profile: ContentProfile,
    duration: float,
    energy_curve: np.ndarray,
    min_dur: float,
    max_dur: float,
    segments: Optional[list[dict]] = None,
    reaction_threshold: float = 0.06,
) -> tuple[float, float, bool]:
    """Clip window for a peak. A reaction peak expands backwards from the reaction onset.

    With a transcript, boundaries snap to whole sentences so each clip is a complete
    thought; without one (party footage) they snap to audio lulls instead.
    """
    is_reaction = reaction_level >= reaction_threshold
    back = profile.reaction_lookback_sec if is_reaction else min_dur / 2.0
    fwd = profile.reaction_payoff_sec if is_reaction else min_dur / 2.0

    if segments:
        start, end = _sentence_window(peak_sec, back, fwd, segments, min_dur, max_dur)
    else:
        start = _snap_to_quiet(max(0.0, peak_sec - back), energy_curve)
        end = _snap_to_quiet(min(duration, peak_sec + fwd), energy_curve)
        if end - start < min_dur:
            if is_reaction:
                start = max(0.0, end - min_dur)
            else:
                end = min(duration, start + min_dur)
        elif end - start > max_dur:
            start = end - max_dur if is_reaction else start
            end = start + max_dur

    return round(max(0.0, start), 1), round(min(duration, end), 1), is_reaction


def detect_highlights(
    video_path: str,
    profile_name: str = "party",
    top_n: int = 8,
    min_dur: float = 8.0,
    max_dur: float = 60.0,
    height_z: float = 1.0,
    segments: Optional[list[dict]] = None,
    words: Optional[list[dict]] = None,
    reaction_threshold: float = 0.06,
    progress_callback: Optional[Callable] = None,
    energy_data: Optional[list[dict]] = None,
    events_data: Optional[list[dict]] = None,
    wav_path: Optional[str] = None,
) -> list[dict]:
    """
    Generate highlight clips from a video's fused signal curve.

    If a transcript is provided, clip boundaries snap to whole sentences so each clip
    is a complete thought; `words` (with punctuation) gives true sentence edges and is
    preferred over `segments`. Without any transcript, boundaries snap to audio lulls.
    `reaction_threshold` is the laughter/cheer level that counts as a reaction — low
    (~0.06) for conversational chuckles, higher for loud belly-laughs/crowds.
    `energy_data`/`events_data` accept the profiles the orchestration layer already
    computed so the source audio isn't decoded again; `wav_path` is a pre-extracted
    16 kHz mono WAV used when extraction is still needed.
    Returns clip dicts compatible with the render pipeline:
    {title, start_second, end_second, duration, score, reasons, preview}.
    """
    profile = get_profile(profile_name)
    snap_units = sentences_from_words(words) if words else segments

    if energy_data is None:
        if progress_callback:
            progress_callback(10, "Analyzing audio energy...")
        energy_data = extract_audio_energy(video_path, wav_path=wav_path)

    if events_data is None:
        events_data = []
        if audio_events_available():
            if progress_callback:
                progress_callback(40, "Detecting laughter and reactions...")
            events_data = extract_audio_events(video_path, wav_path=wav_path)

    last_times = [e["time"] for e in energy_data] + [e["time"] for e in events_data]
    if not last_times:
        return []
    duration = max(last_times) + 1.0
    n_bins = int(duration * GRID_HZ) + 1

    energy_curve = _energy_curve(energy_data, n_bins)
    # Dilate reactions by ~2s so a single-frame laugh isn't suppressed by a louder
    # energy neighbor and so the fused peak lands on the reaction, not next to it.
    reaction_curve = _dilate(_reaction_curve(events_data, n_bins), int(2 * GRID_HZ))

    fused = fuse_channels(
        {"energy": energy_curve, "audio_event": reaction_curve}, profile
    )
    if fused.size == 0:
        return []

    if progress_callback:
        progress_callback(70, "Selecting highlight moments...")
    min_gap_bins = max(1, int(profile.peak_min_gap_sec * GRID_HZ))

    # Reaction moments are primary candidates — a detected laugh/cheer is almost always
    # worth a clip regardless of loudness, so they aren't made to out-compete energy in
    # the blended curve. Energy peaks then fill the rest, minus any that collide with a
    # reaction. Reaction score is offset above energy so reactions rank first.
    reaction_peaks = pick_peaks(reaction_curve, reaction_threshold, min_gap_bins)
    energy_peaks = pick_peaks(fused, height_z, min_gap_bins)

    candidates = [(i, float(reaction_curve[i]), True) for i in reaction_peaks]
    reaction_bins = {i for i in reaction_peaks}
    for i in energy_peaks:
        if all(abs(i - j) >= min_gap_bins for j in reaction_bins):
            candidates.append((i, float(fused[i]), False))

    def rank_key(c):
        i, val, is_reaction = c
        return (1 if is_reaction else 0, val)

    candidates.sort(key=rank_key, reverse=True)
    candidates = candidates[:top_n]

    clips = []
    for i, val, want_reaction in candidates:
        peak_sec = i / GRID_HZ
        reaction_level = float(reaction_curve[i]) if want_reaction else 0.0
        start, end, is_reaction = _window_for_peak(
            peak_sec, reaction_level, profile, duration, energy_curve, min_dur, max_dur,
            snap_units, reaction_threshold,
        )
        if end - start < min_dur * 0.75:
            continue
        kind = "laugh/cheer" if is_reaction else "high energy"
        score = round(10.0 + reaction_level * 10.0, 2) if is_reaction else round(float(val), 2)
        clips.append({
            "title": f"Highlight ({kind}) at {int(peak_sec // 60):d}:{int(peak_sec % 60):02d}",
            "start_second": start,
            "end_second": end,
            "duration": round(end - start),
            "score": score,
            "reasons": ["reaction"] if is_reaction else ["energy_peak"],
            "preview": "",
            "content_type": "highlight",
        })

    clips.sort(key=lambda c: c["start_second"])
    if progress_callback:
        progress_callback(100, f"Found {len(clips)} highlights")
    return clips


def detect_highlights_pooled(
    video_paths: list[str],
    profile_name: str = "party",
    top_n: int = 15,
    min_dur: float = 8.0,
    max_dur: float = 60.0,
    progress_callback: Optional[Callable] = None,
) -> list[dict]:
    """
    Detect highlights across many videos and rank them globally — "the best N bits
    from tonight" across a folder of party clips.

    Each returned clip carries a `source_file`. Ranking is reaction-first, then by
    score, so a genuine laugh in any file outranks a merely loud moment in another.
    """
    pooled: list[dict] = []
    n = len(video_paths) or 1
    for idx, path in enumerate(video_paths):
        clips = detect_highlights(
            path, profile_name=profile_name, top_n=top_n, min_dur=min_dur, max_dur=max_dur
        )
        for c in clips:
            c["source_file"] = path
        pooled.extend(clips)
        if progress_callback:
            progress_callback(
                int((idx + 1) / n * 100), f"{path}: {len(clips)} highlights"
            )

    pooled.sort(
        key=lambda c: (1 if "reaction" in c.get("reasons", []) else 0, c.get("score", 0)),
        reverse=True,
    )
    return pooled[:top_n]
