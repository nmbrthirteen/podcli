"""
Audio energy analysis for smarter clip scoring.

Extracts volume/RMS data per second from a video file using FFmpeg,
then identifies energy peaks (loud moments, laughs, emphasis).
Used by the clip suggestor to boost scores for high-energy segments.
"""

import subprocess
import json
import re
from typing import Optional, Callable


def extract_audio_energy(
    video_path: str,
    window_sec: float = 1.0,
) -> list[dict]:
    """
    Extract per-second RMS energy levels from a video's audio track.

    Returns list of {time, rms, peak} dicts, one per window.
    """
    # Use ffmpeg astats filter to get per-frame audio stats
    cmd = [
        "ffmpeg",
        "-i", video_path,
        "-af", f"astats=metadata=1:reset={int(1/window_sec)},"
               f"ametadata=print:key=lavfi.astats.Overall.RMS_level:file=-",
        "-f", "null", "-",
    ]

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)

    # Parse the ametadata output from stdout
    # Format: "frame:N    pts:N    pts_time:N.N\nlavfi.astats.Overall.RMS_level=N.N"
    energy_data = []
    lines = (result.stdout or "").split("\n")

    current_time = None
    for line in lines:
        line = line.strip()
        pts_match = re.search(r'pts_time:(\d+\.?\d*)', line)
        if pts_match:
            current_time = float(pts_match.group(1))

        rms_match = re.search(r'RMS_level=(-?\d+\.?\d*)', line)
        if rms_match and current_time is not None:
            rms_db = float(rms_match.group(1))
            energy_data.append({
                "time": round(current_time, 2),
                "rms_db": round(rms_db, 2),
            })
            current_time = None

    if not energy_data:
        # Fallback: use volumedetect for a simpler analysis
        return _fallback_energy(video_path)

    return energy_data


def _fallback_energy(video_path: str) -> list[dict]:
    """
    Simpler fallback: extract volume per second using ffprobe + loudness.
    Returns a basic energy profile.
    """
    # Get duration first
    probe_cmd = [
        "ffprobe", "-v", "quiet", "-print_format", "json",
        "-show_format", video_path,
    ]
    probe = subprocess.run(probe_cmd, capture_output=True, text=True)
    try:
        duration = float(json.loads(probe.stdout)["format"]["duration"])
    except (json.JSONDecodeError, KeyError):
        return []

    # Use ebur128 filter which reliably outputs loudness per second
    cmd = [
        "ffmpeg",
        "-i", video_path,
        "-af", "ebur128=peak=true",
        "-f", "null", "-",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)

    energy_data = []
    # Parse ebur128 output from stderr: "t: N.N   M: -N.N S: -N.N"
    for line in (result.stderr or "").split("\n"):
        m = re.search(r't:\s*(\d+\.?\d*)\s+M:\s*(-?\d+\.?\d*)', line)
        if m:
            energy_data.append({
                "time": round(float(m.group(1)), 2),
                "rms_db": round(float(m.group(2)), 2),
            })

    return energy_data


def compute_energy_scores(
    energy_data: list[dict],
    segments: list[dict],
) -> list[float]:
    """
    For each segment, compute an energy score (0-10) based on audio loudness.

    Higher scores = louder/more energetic moments.
    Uses z-score normalization so it adapts to each podcast's baseline.
    """
    if not energy_data:
        return [0.0] * len(segments)

    # Build time→rms lookup
    rms_values = [e["rms_db"] for e in energy_data]
    if not rms_values:
        return [0.0] * len(segments)

    # Filter out silence (-inf or very low values)
    valid_rms = [r for r in rms_values if r > -60]
    if not valid_rms:
        return [0.0] * len(segments)

    mean_rms = sum(valid_rms) / len(valid_rms)
    std_rms = max((sum((r - mean_rms) ** 2 for r in valid_rms) / len(valid_rms)) ** 0.5, 0.1)

    scores = []
    for seg in segments:
        seg_start = seg.get("start", 0)
        seg_end = seg.get("end", 0)

        # Get energy values within this segment's time range
        seg_rms = [
            e["rms_db"] for e in energy_data
            if seg_start <= e["time"] <= seg_end and e["rms_db"] > -60
        ]

        if not seg_rms:
            scores.append(0.0)
            continue

        # Average RMS for segment
        avg_rms = sum(seg_rms) / len(seg_rms)
        # Peak RMS (most energetic moment)
        peak_rms = max(seg_rms)

        # Z-score: how many std devs above mean
        z_avg = (avg_rms - mean_rms) / std_rms
        z_peak = (peak_rms - mean_rms) / std_rms

        # Combine: weight peak more (catches laughs, emphasis)
        raw_score = z_avg * 0.4 + z_peak * 0.6

        # Normalize to 0-10 range (clamp)
        score = max(0, min(10, (raw_score + 1) * 3))
        scores.append(round(score, 2))

    return scores


def get_energy_profile(
    video_path: str,
    segments: list[dict],
    progress_callback: Optional[Callable] = None,
) -> dict:
    """
    Full pipeline: extract energy data and score all segments.

    Returns {energy_data, segment_scores, mean_rms, peak_times}
    """
    if progress_callback:
        progress_callback(0, "Analyzing audio energy...")

    energy_data = extract_audio_energy(video_path)

    if progress_callback:
        progress_callback(70, "Scoring segments by energy...")

    segment_scores = compute_energy_scores(energy_data, segments)

    # Find peak moments (top 10% loudest)
    if energy_data:
        sorted_by_rms = sorted(energy_data, key=lambda e: e["rms_db"], reverse=True)
        top_count = max(1, len(sorted_by_rms) // 10)
        peak_times = [e["time"] for e in sorted_by_rms[:top_count]]
    else:
        peak_times = []

    if progress_callback:
        progress_callback(100, "Audio analysis complete")

    return {
        "energy_data": energy_data,
        "segment_scores": segment_scores,
        "peak_times": peak_times,
    }
