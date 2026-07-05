"""Editable highlight-reel sessions — the shared core behind the CLI, MCP, and UI.

Detection (the slow signal analysis) runs once into a persisted session of moments.
Every later edit — longer, shorter, shift, drop, reorder — mutates that session and
re-cuts only the changed moment straight from the source (~seconds), then rebuilds the
reel. No re-detection, no heavy render. All three surfaces operate on the same session
so an edit made in one shows up in the others.
"""

import json
import os
import subprocess
from dataclasses import dataclass, field, asdict
from typing import Optional, Callable

from utils.proc import run as proc_run
from config.paths import paths


def _sessions_dir() -> str:
    # paths["packed"] is .podcli/packed; its parent is the .podcli root.
    d = os.path.join(os.path.dirname(paths["packed"]), "reels")
    os.makedirs(d, exist_ok=True)
    return d


def session_path(session_id: str) -> str:
    return os.path.join(_sessions_dir(), f"{session_id}.json")


@dataclass
class Moment:
    start: float
    end: float
    why: str = "energy_peak"
    text: str = ""
    enabled: bool = True
    dirty: bool = True  # needs a re-cut before the next build

    @property
    def duration(self) -> float:
        return round(self.end - self.start, 1)


@dataclass
class ReelSession:
    session_id: str
    source: str
    profile: str
    out_dir: str
    moments: list[Moment] = field(default_factory=list)

    def save(self) -> str:
        path = session_path(self.session_id)
        data = asdict(self)
        with open(path, "w") as f:
            json.dump(data, f, indent=2)
        return path

    @classmethod
    def load(cls, session_id: str) -> "ReelSession":
        with open(session_path(session_id)) as f:
            data = json.load(f)
        data["moments"] = [Moment(**m) for m in data.get("moments", [])]
        return cls(**data)


def _clip_text(words: list[dict], a: float, b: float) -> str:
    return " ".join(
        str(w.get("word", "")).strip()
        for w in words
        if w.get("start", 0) >= a - 0.05 and w.get("end", 0) <= b + 0.05
    )


def seed_session(
    session_id: str,
    source: str,
    out_dir: str,
    profile: str = "party",
    top_n: int = 10,
    min_dur: float = 15.0,
    max_dur: float = 60.0,
    words: Optional[list[dict]] = None,
    progress_callback: Optional[Callable] = None,
) -> ReelSession:
    """Run detection once and persist the moments as an editable session."""
    from services.saliency import detect_highlights

    clips = detect_highlights(
        source, profile_name=profile, top_n=top_n, min_dur=min_dur, max_dur=max_dur,
        words=words, progress_callback=progress_callback,
    )
    moments = [
        Moment(
            start=round(c["start_second"], 1),
            end=round(c["end_second"], 1),
            why=c.get("reasons", ["energy_peak"])[0],
            text=_clip_text(words, c["start_second"], c["end_second"]) if words else "",
        )
        for c in clips
    ]
    session = ReelSession(session_id, source, profile, out_dir, moments)
    session.save()
    return session


_EDITS = {
    "longer":  lambda m, s: setattr(m, "end", round(m.end + s, 1)),
    "shorter": lambda m, s: setattr(m, "end", round(max(m.start + 1, m.end - s), 1)),
    "earlier": lambda m, s: setattr(m, "start", round(max(0.0, m.start - s), 1)),
    "later":   lambda m, s: setattr(m, "start", round(min(m.end - 1, m.start + s), 1)),
    "shift":   lambda m, s: (setattr(m, "start", round(max(0.0, m.start + s), 1)),
                             setattr(m, "end", round(m.end + s, 1))),
}


def edit_moment(session: ReelSession, index: int, op: str, seconds: float = 0.0) -> ReelSession:
    """Apply an edit to one moment (1-based index) and mark it for re-cut."""
    if not (1 <= index <= len(session.moments)):
        raise IndexError(f"no moment {index} (have {len(session.moments)})")
    m = session.moments[index - 1]
    if op == "drop":
        session.moments.pop(index - 1)
    elif op == "toggle":
        m.enabled = not m.enabled
    elif op in _EDITS:
        _EDITS[op](m, seconds)
        m.dirty = True
    else:
        raise ValueError(f"unknown op {op!r}")
    session.save()
    return session


def _cut(source: str, out_dir: str, idx: int, m: Moment) -> str:
    clips_dir = os.path.join(out_dir, "clips")
    os.makedirs(clips_dir, exist_ok=True)
    out = os.path.join(clips_dir, f"clip_{idx:02d}.mp4")
    proc_run([
        "ffmpeg", "-y", "-ss", str(m.start), "-i", source, "-t", str(m.duration),
        "-vf", "scale=1920:1080:force_original_aspect_ratio=decrease,"
               "pad=1920:1080:(ow-iw)/2:(oh-ih)/2",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-c:a", "aac", "-b:a", "192k", out, "-loglevel", "error",
    ], timeout=120, check=True)
    return out


def build_reel(session: ReelSession, progress_callback: Optional[Callable] = None) -> str:
    """Re-cut only dirty moments, then concatenate all enabled moments into the reel."""
    os.makedirs(session.out_dir, exist_ok=True)
    files = []
    active = [(i, m) for i, m in enumerate(session.moments, 1) if m.enabled]
    for n, (i, m) in enumerate(active, 1):
        clip = os.path.join(session.out_dir, "clips", f"clip_{i:02d}.mp4")
        if m.dirty or not os.path.exists(clip):
            if progress_callback:
                progress_callback(int(n / len(active) * 90), f"cutting moment {i}")
            clip = _cut(session.source, session.out_dir, i, m)
            m.dirty = False
        files.append(clip)
    session.save()

    reel = os.path.join(session.out_dir, "highlights_reel.mp4")
    lst = os.path.join(session.out_dir, "_concat.txt")
    with open(lst, "w") as f:
        f.write("".join(f"file '{os.path.abspath(x)}'\n" for x in files))
    r = proc_run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", lst,
                  "-c", "copy", reel, "-loglevel", "error"], timeout=300, check=False)
    if r.returncode != 0:
        proc_run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", lst,
                  "-c:v", "libx264", "-c:a", "aac", reel, "-loglevel", "error"],
                 timeout=600, check=True)
    if progress_callback:
        progress_callback(100, f"built reel with {len(files)} moments")
    return reel
