"""Pure helpers for building ffmpeg motion-aware filter expressions.

All functions here are side-effect-free: they take keyframe / window
lists and return strings or lists. No ffmpeg is actually invoked from
this module — the caller passes the returned expressions into a
filter_complex graph.

Extracted from video_processor.py so the pure math can be unit-tested
in isolation.
"""

from __future__ import annotations


def build_cam_expr(
    keyframes: list,
    duration: float,
    is_split: bool,
    max_parts: int = 80,
) -> str | None:
    """Build an FFmpeg crop_x expression from SmoothedCameraman keyframes.

    keyframes: [(time, crop_x), ...]
    Returns the expression string, or None if too complex to express.
    """
    if not keyframes:
        return None
    # Ensure t=0 covered.
    if keyframes[0][0] > 0.05:
        keyframes = [(0, keyframes[0][1])] + keyframes
    if len(keyframes) == 1:
        return str(keyframes[0][1])

    def _eased_move_expr(start_t: float, move_t: float, start_x: int, end_x: int) -> str:
        # Smootherstep (6t^5 − 15t^4 + 10t^3) gives a softer start/landing
        # than smoothstep while still finishing inside the bounded window.
        progress = f"((t-{start_t:.3f})/{move_t:.3f})"
        eased = (
            f"((6*({progress})*({progress})*({progress})*({progress})*({progress}))"
            f"-(15*({progress})*({progress})*({progress})*({progress}))"
            f"+(10*({progress})*({progress})*({progress})))"
        )
        return f"{start_x}+(({end_x}-{start_x})*{eased})"

    parts: list[str] = []
    for i in range(len(keyframes) - 1):
        t0, x0 = keyframes[i]
        t1, x1 = keyframes[i + 1]
        dt = max(0.01, t1 - t0)
        jump = abs(x1 - x0)
        # In split/mixed layouts, avoid both long pans and hard snaps.
        # Force very short eased handoffs for virtually all jumps.
        blurred_cut_jump = 100000 if is_split else 180
        quick_reframe_jump = 24 if is_split else 90

        if jump < 2:
            # Negligible movement — hold.
            parts.append(f"if(between(t\\,{t0:.3f}\\,{t1:.3f})\\,{x0}\\,")
        elif jump >= blurred_cut_jump:
            # Very large jumps in non-split layouts still read better as
            # short blurred cuts than long animated slides.
            parts.append(f"if(between(t\\,{t0:.3f}\\,{t1:.3f})\\,{x1}\\,")
        elif jump >= quick_reframe_jump:
            # Moderate jump: use a short bounded reframe, but keep it
            # much tighter than a literal camera pan.
            pan_t = min(0.08 if is_split else 0.18, dt)
            pan_end_t = round(t0 + pan_t, 3)
            parts.append(
                f"if(between(t\\,{t0:.3f}\\,{pan_end_t:.3f})\\,"
                f"{_eased_move_expr(t0, pan_t, x0, x1)}\\,"
            )
            if pan_end_t < t1:
                parts.append(f"if(between(t\\,{pan_end_t:.3f}\\,{t1:.3f})\\,{x1}\\,")
        else:
            # Small pans still benefit from eased starts/stops so they
            # read like a camera operator, not a value sliding on rails.
            parts.append(
                f"if(between(t\\,{t0:.3f}\\,{t1:.3f})\\,"
                f"{_eased_move_expr(t0, dt, x0, x1)}\\,"
            )

    if len(parts) > max_parts:
        return None

    return "".join(parts) + str(keyframes[-1][1]) + ")" * len(parts)


def motion_windows_from_keyframes(
    keyframes: list,
    min_jump: int = 60,
    max_window_duration: float = 0.5,
    max_windows: int = 16,
) -> list[tuple[float, float]]:
    """Find short reframe windows worth accenting with blur / zoom."""
    windows: list[tuple[float, float]] = []
    for i in range(len(keyframes) - 1):
        t0, x0 = keyframes[i]
        t1, x1 = keyframes[i + 1]
        if abs(x1 - x0) < min_jump:
            continue
        dt = t1 - t0
        if dt <= 0.01 or dt > max_window_duration:
            continue
        windows.append((t0, t1))

    if not windows or len(windows) > max_windows:
        return []
    return windows


def expand_motion_windows(
    windows: list[tuple[float, float]],
    pad_before: float = 0.03,
    pad_after: float = 0.05,
) -> list[tuple[float, float]]:
    """Pad and merge adjacent motion windows so blur eases in and out."""
    if not windows:
        return []

    expanded: list[tuple[float, float]] = []
    for t0, t1 in windows:
        start = max(0.0, t0 - pad_before)
        end = t1 + pad_after
        if expanded and start <= expanded[-1][1] + 0.01:
            expanded[-1] = (expanded[-1][0], max(expanded[-1][1], end))
        else:
            expanded.append((start, end))
    return expanded


def build_motion_blur_filter(
    keyframes: list,
    min_jump: int = 60,
    max_window_duration: float = 0.5,
    max_windows: int = 16,
    core_sigma: float = 3.8,
    core_steps: int = 2,
    core_pad_before: float = 0.02,
    core_pad_after: float = 0.05,
    outer_sigma: float = 1.6,
    outer_steps: int = 1,
    outer_pad_before: float = 0.05,
    outer_pad_after: float = 0.08,
) -> str:
    """Build a layered full-frame blur filter for motion windows.

    A soft outer blur eases the transition in and out, while a tighter
    core blur covers the actual crop move. Empty string when no windows
    qualify (caller concatenates safely).
    """
    base_windows = motion_windows_from_keyframes(
        keyframes=keyframes,
        min_jump=min_jump,
        max_window_duration=max_window_duration,
        max_windows=max_windows,
    )
    if not base_windows:
        return ""

    outer_windows = expand_motion_windows(
        base_windows,
        pad_before=outer_pad_before,
        pad_after=outer_pad_after,
    )
    core_windows = expand_motion_windows(
        base_windows,
        pad_before=core_pad_before,
        pad_after=core_pad_after,
    )

    outer_enable = "+".join(
        f"between(t\\,{t0:.3f}\\,{t1:.3f})" for t0, t1 in outer_windows
    )
    core_enable = "+".join(
        f"between(t\\,{t0:.3f}\\,{t1:.3f})" for t0, t1 in core_windows
    )
    return (
        f",gblur=sigma={outer_sigma:.1f}:steps={outer_steps}:enable='{outer_enable}'"
        f",gblur=sigma={core_sigma:.1f}:steps={core_steps}:enable='{core_enable}'"
    )


def build_motion_zoom_filter(
    keyframes: list,
    target_w: int,
    target_h: int,
    min_jump: int = 60,
    max_window_duration: float = 0.5,
    max_windows: int = 16,
    max_zoom: float = 0.018,
) -> str:
    """Build a tiny center-zoom bump filter for motion windows.

    The goal is a subliminal push that reads as an editorial beat, not
    a visible punch-in. Empty string when no windows qualify.
    """
    windows = motion_windows_from_keyframes(
        keyframes=keyframes,
        min_jump=min_jump,
        max_window_duration=max_window_duration,
        max_windows=max_windows,
    )
    if not windows:
        return ""

    bumps: list[str] = []
    for t0, t1 in windows:
        dt = max(0.01, t1 - t0)
        progress = f"((t-{t0:.3f})/{dt:.3f})"
        bumps.append(
            f"(16*pow({progress}\\,2)*pow((1-{progress})\\,2)*between(t\\,{t0:.3f}\\,{t1:.3f}))"
        )

    zoom_expr = "+".join(bumps)
    return (
        f",scale=w='iw*(1+{max_zoom:.4f}*({zoom_expr}))'"
        f":h='ih*(1+{max_zoom:.4f}*({zoom_expr}))'"
        f":eval=frame,crop={target_w}:{target_h}:(iw-{target_w})/2:(ih-{target_h})/2"
    )


def simplify_keyframes(keyframes: list, tolerance: int = 5) -> list:
    """Remove intermediate keyframes that lie on a line between neighbours."""
    if len(keyframes) <= 2:
        return keyframes
    result = [keyframes[0]]
    for i in range(1, len(keyframes) - 1):
        t_prev, x_prev = result[-1]
        t_curr, x_curr = keyframes[i]
        t_next, x_next = keyframes[i + 1]
        dt_total = t_next - t_prev
        if dt_total < 0.01:
            continue
        expected = x_prev + (x_next - x_prev) * (t_curr - t_prev) / dt_total
        if abs(x_curr - expected) > tolerance:
            result.append(keyframes[i])
    result.append(keyframes[-1])
    return result
