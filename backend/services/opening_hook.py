"""Opening hook: a short spoken passage from inside a clip, played first.

A clip's body plays its keep ranges in source order. A hook puts one range
from inside that body ahead of it, so the playback order stops being
monotonic on the source clock. "repeat" plays the passage again where it
belongs; "move" lifts it out of the body.
"""

from typing import Optional

MIN_HOOK_SECONDS = 1.0
MAX_HOOK_SECONDS = 15.0
HOOK_MODES = ("repeat", "move")

# Agents and the studio round timestamps independently. A hook that starts on
# the clip's own start_second must not fail containment by a float hair.
_EDGE_TOLERANCE = 0.01

# A move hook can leave a sliver of body between its edge and a segment edge.
# A part that short is a frame or two of encoder padding, not content.
_MIN_REMNANT_SECONDS = 0.1


def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _covering_spans(ranges: list[dict]) -> list[list[float]]:
    spans: list[list[float]] = []
    for r in sorted(ranges, key=lambda r: r["start"]):
        if spans and r["start"] <= spans[-1][1] + _EDGE_TOLERANCE:
            spans[-1][1] = max(spans[-1][1], r["end"])
        else:
            spans.append([r["start"], r["end"]])
    return spans


def validate_hook(
    hook: Optional[dict],
    start_second: float,
    end_second: float,
    segments: Optional[list[dict]] = None,
) -> Optional[dict]:
    """Check a hook against the clip it opens. Returns it normalized, or None.

    The body is the clip's segments when it has any, else its whole range.
    Raises ValueError naming what is wrong.
    """
    if hook is None:
        return None
    if not isinstance(hook, dict):
        raise ValueError("hook must be an object with start, end and mode")
    start, end, mode = hook.get("start"), hook.get("end"), hook.get("mode")
    if not _is_number(start) or not _is_number(end):
        raise ValueError("hook start and end must be numbers of seconds")
    if mode not in HOOK_MODES:
        raise ValueError('hook mode must be "repeat" or "move"')
    if end <= start:
        raise ValueError("hook end must be greater than hook start")
    length = end - start
    if length < MIN_HOOK_SECONDS or length > MAX_HOOK_SECONDS:
        raise ValueError(
            f"hook runs {length:.1f}s. It must run between "
            f"{MIN_HOOK_SECONDS:.0f} and {MAX_HOOK_SECONDS:.0f} seconds."
        )
    body = [s for s in (segments or []) if s["end"] > s["start"]]
    if not body:
        body = [{"start": start_second, "end": end_second}]
    if not any(
        a - _EDGE_TOLERANCE <= start and end <= b + _EDGE_TOLERANCE
        for a, b in _covering_spans(body)
    ):
        raise ValueError(
            f"hook {start:.2f}-{end:.2f}s is not inside the clip body. "
            "Pick a passage the clip already plays."
        )
    return {"start": float(start), "end": float(end), "mode": mode}


def snap_hook_to_words(hook: dict, words: Optional[list[dict]]) -> dict:
    """Widen a hook edge that cuts through a word so the whole word plays."""
    start, end = hook["start"], hook["end"]
    for w in words or []:
        if w["start"] < start < w["end"]:
            start = w["start"]
        if w["start"] < end < w["end"]:
            end = w["end"]
    return {**hook, "start": round(start, 3), "end": round(end, 3)}


def order_with_hook(body: list[dict], hook: dict) -> list[dict]:
    """The ranges to cut, in the order they play: the hook, then the body.

    repeat keeps the body whole. move removes the hook's span from it.
    """
    head = {"start": hook["start"], "end": hook["end"]}
    if hook["mode"] == "repeat":
        return [head] + [{"start": s["start"], "end": s["end"]} for s in body]
    rest = []
    for s in body:
        for a, b in (
            (s["start"], min(s["end"], hook["start"])),
            (max(s["start"], hook["end"]), s["end"]),
        ):
            if b - a >= _MIN_REMNANT_SECONDS:
                rest.append({"start": a, "end": b})
    if not rest:
        raise ValueError(
            "A move hook cannot take the whole clip. Use repeat, or widen the clip."
        )
    return [head] + rest


def keyframes_to_playback(
    keyframes: list[dict],
    origin: float,
    ranges: list[dict],
    part_durations: Optional[list[float]] = None,
) -> list[dict]:
    """Re-express manual crop keyframes on the clip's playback clock.

    Keyframes arrive relative to origin (the requested start_second) on the
    source clock. Ranges play in list order, so one source instant can play
    zero, one or two times. A keyframe holds until the next one, so each range
    opens on whichever keyframe was in force at its first source instant.
    """
    src = sorted((origin + float(k["t"]), k["x_pct"]) for k in keyframes)
    if not src:
        return []
    out = []
    offset = 0.0
    for i, r in enumerate(ranges):
        held = src[0][1]
        for t, x in src:
            if t <= r["start"]:
                held = x
        out.append({"t": round(offset, 3), "x_pct": held})
        for t, x in src:
            if r["start"] < t < r["end"]:
                out.append({"t": round(offset + t - r["start"], 3), "x_pct": x})
        probed = part_durations[i] if part_durations and i < len(part_durations) else 0.0
        offset += probed if probed > 0 else r["end"] - r["start"]
    return out
