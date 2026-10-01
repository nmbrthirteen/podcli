"""Multicam podcast editing sessions: map sources, sync, cut, and deliver.

A session holds every camera and microphone file for one recording, who each
one belongs to, and how each sits on a shared timeline. The flow is:

  new (scan + guess roles) -> map (user fixes roles) -> sync -> plan -> render / export

Sessions persist as JSON under .podcli/multicam so the UI, MCP, and CLI all edit
the same state. Heavy intermediates (8 kHz sync audio, previews) live under the
working dir and are safe to delete.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import tempfile
import threading
import uuid
import wave
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Optional

import numpy as np

from config.paths import paths
from services import multicam_signal as sig
from services.media_probe import get_video_info
from utils.proc import run as proc_run

ProgressCallback = Optional[Callable[[int, str], None]]

SYNC_RATE = 8000
MEDIA_EXTS = {".mp4", ".mov", ".mkv", ".m4v", ".avi", ".mts", ".mxf", ".webm",
              ".wav", ".mp3", ".m4a", ".aac", ".flac", ".aif", ".aiff"}

LOOKS = {
    "none": "",
    "natural": "eq=contrast=1.04:saturation=1.06",
    "warm": "colorbalance=rs=0.05:bs=-0.05:rm=0.03:bm=-0.03,eq=contrast=1.03:saturation=1.08",
    "contrast": "curves=preset=medium_contrast,eq=saturation=1.1",
}

# How a show is cut. Studio: everyone has a camera, cut to whoever talks.
# Remote (measured on Deeptech Decoded's call recordings): questions and short
# answers sit on the split screen, a guest goes full frame 4 s into any answer
# of 8 s or more and stays there, and hosts never get a solo shot.
STYLES = {
    "studio": {"min_shot": 2.0, "max_shot": 30.0, "wide_insert": 4.0, "backchannel": 1.2, "hold_guest": True,
               "host_solo": True, "guest_min": 0.0, "guest_delay": 0.0},
    "remote": {"min_shot": 4.0, "max_shot": 0.0, "wide_insert": 4.0, "backchannel": 1.5, "hold_guest": True,
               "host_solo": False, "guest_min": 8.0, "guest_delay": 4.0},
}
CUT_LIMITS = {
    "min_shot": (0.5, 10.0), "max_shot": (0.0, 600.0), "wide_insert": (1.0, 15.0), "backchannel": (0.0, 5.0),
    "guest_min": (0.0, 60.0), "guest_delay": (0.0, 30.0),
}


def resolved_style(session: "MulticamSession") -> str:
    """The cut style in force: a call layout (panes or a split screen) cuts like a remote show."""
    style = session.cut_settings.get("style", "auto")
    if style in STYLES:
        return style
    return "remote" if any(c.virtual for c in session.cameras()) else "studio"


def effective_cut(session: "MulticamSession") -> dict:
    tweaks = {k: v for k, v in session.cut_settings.items() if k in STYLES["studio"]}
    return {**STYLES[resolved_style(session)], **tweaks}


def _emit(callback: ProgressCallback, percent: float, message: str) -> None:
    if callback:
        callback(max(0, min(100, int(percent))), message)


def _sessions_dir() -> Path:
    d = Path(os.path.dirname(paths["packed"])) / "multicam"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _work_dir(session_id: str) -> Path:
    d = Path(paths["working"]) / "multicam" / session_id
    d.mkdir(parents=True, exist_ok=True)
    return d


@dataclass
class Person:
    id: str
    name: str
    role: str = "host"  # "host" | "guest": a guest's answers are held on their camera


@dataclass
class Source:
    id: str
    path: str
    kind: str  # "video" | "audio"
    duration: float
    has_audio: bool
    audio_channels: int = 0
    width: int = 0
    height: int = 0
    fps: float = 0.0
    timecode: float = 0.0  # embedded start timecode in seconds; editors address media from here
    role: str = "ignore"  # "camera" | "mic" | "ignore"
    # camera: a person id or "wide"; mic: a person id, or "" for a shared room mic
    person: str = ""
    # mic only: one person id per channel when a recorder puts two people on L/R
    channel_people: list[str] = field(default_factory=list)
    guessed: bool = True
    offset: Optional[float] = None  # timeline seconds where source time 0 sits
    speed: float = 1.0  # timeline seconds per source second (clock drift)
    sync: dict = field(default_factory=dict)
    # Virtual cameras need no file of their own: a pane cropped out of a call
    # recording (parent + crop), or a split screen of several people (members).
    parent: str = ""
    crop: list[float] = field(default_factory=list)  # [x, y, w, h] as fractions of the parent's frame
    members: list[str] = field(default_factory=list)  # camera ids laid side by side, left to right

    @property
    def synced(self) -> bool:
        return self.offset is not None

    @property
    def virtual(self) -> bool:
        return bool(self.parent or self.members)

    def timeline_start(self) -> float:
        return float(self.offset or 0.0)

    def timeline_end(self) -> float:
        return self.timeline_start() + self.duration * self.speed

    def source_time(self, timeline_seconds: float) -> float:
        return (timeline_seconds - self.timeline_start()) / self.speed


@dataclass
class MulticamSession:
    session_id: str
    name: str
    folder: str = ""
    people: list[Person] = field(default_factory=list)
    sources: list[Source] = field(default_factory=list)
    reference_id: str = ""
    range_start: Optional[float] = None
    range_end: Optional[float] = None
    look: str = "none"
    cut_settings: dict = field(default_factory=lambda: {"style": "auto"})  # the style, plus any tweaks to it
    cuts: list[dict] = field(default_factory=list)
    speaker_map: dict = field(default_factory=dict)
    activity_key: str = ""
    outputs: dict = field(default_factory=dict)
    skipped: list[str] = field(default_factory=list)
    preview: dict = field(default_factory=dict)
    removals: list[dict] = field(default_factory=list)
    cloud: dict = field(default_factory=dict)  # {"id", "url"} once sent to the podcli cloud editor

    def path(self) -> Path:
        return _sessions_dir() / f"{self.session_id}.json"

    def save(self) -> None:
        tmp = self.path().with_suffix(".json.tmp")
        tmp.write_text(json.dumps(asdict(self), indent=2, allow_nan=False), encoding="utf-8")
        os.replace(tmp, self.path())

    @classmethod
    def load(cls, session_id: str) -> "MulticamSession":
        if not re.fullmatch(r"[a-f0-9]{6,32}", session_id or ""):
            raise ValueError(f"invalid session id {session_id!r}")
        p = _sessions_dir() / f"{session_id}.json"
        if not p.exists():
            raise FileNotFoundError(f"No multicam session {session_id}")
        data = json.loads(p.read_text(encoding="utf-8"))
        data["people"] = [Person(**x) for x in data.get("people", [])]
        data["sources"] = [Source(**x) for x in data.get("sources", [])]
        return cls(**data)

    def source(self, source_id: str) -> Source:
        for s in self.sources:
            if s.id == source_id:
                return s
        raise ValueError(f"No file with id {source_id!r} in this edit")

    def person_ids(self) -> list[str]:
        return [p.id for p in self.people]

    def cameras(self) -> list[Source]:
        return [s for s in self.sources if s.role == "camera" and s.kind == "video"]

    def person_mics(self) -> list[tuple[Source, int, str]]:
        """(source, channel or -1 for a mono mixdown, person) for each personal mic feed.

        In a remote recording each person's own file carries their own mic, so
        a person camera with sound counts as that person's mic when no
        dedicated mic is mapped to them.
        """
        feeds = []
        people = set(self.person_ids())
        if any(s.members and s.role != "ignore" for s in self.sources):
            mic_people = {s.person for s in self.sources if s.role == "mic"} | {
                p for s in self.sources if s.role == "mic" for p in s.channel_people}
            feeds += [
                (s, -1, s.person) for s in self.sources
                if s.role == "camera" and not s.virtual and s.has_audio and s.person in people - mic_people
            ]
        for s in self.sources:
            if s.role != "mic":
                continue
            if s.channel_people:
                for ch, pid in enumerate(s.channel_people[: max(1, s.audio_channels)]):
                    if pid in people:
                        feeds.append((s, ch, pid))
            elif s.person in people:
                feeds.append((s, -1, s.person))
        return feeds

    def timeline_duration(self) -> float:
        synced = [s for s in self.sources if s.role != "ignore" and s.synced]
        return max((s.timeline_end() for s in synced), default=0.0)


# ---------------------------------------------------------------------------
# Scanning and role guessing
# ---------------------------------------------------------------------------

MAX_SOURCES = 40
MAX_SCANNED_ENTRIES = 20_000
MAX_SECONDS = 24 * 3600
MAX_REMOVALS = 20_000
MAX_SHOTS = 50_000


def _number(value, what: str, lo: float, hi: float) -> float:
    """A finite number in [lo, hi], or a ValueError naming the field."""
    try:
        x = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{what} must be a number") from None
    if not math.isfinite(x) or not lo <= x <= hi:
        raise ValueError(f"{what} must be between {lo:g} and {hi:g}")
    return x


def scan_folder(folder: str) -> list[str]:
    """Media files under one episode folder, stopping early on folders that are clearly too big."""
    root = Path(folder).expanduser()
    if not root.is_dir():
        raise FileNotFoundError(f"Folder not found: {folder}")
    found: list[str] = []
    seen = 0
    for dirpath, dirnames, filenames in os.walk(root, onerror=lambda _e: None):
        # Skip hidden folders and podcli's own renders if someone points at the output folder.
        dirnames[:] = sorted(d for d in dirnames if not d.startswith(".") and not d.endswith("_multicam_podcli"))
        for name in sorted(filenames):
            seen += 1
            if seen > MAX_SCANNED_ENTRIES or len(found) > MAX_SOURCES:
                raise ValueError(
                    f"{folder} holds too many files for one episode. Pick the folder with just this recording's "
                    f"cameras and mics ({MAX_SOURCES} media files at most)."
                )
            if name.startswith(".") or Path(name).suffix.lower() not in MEDIA_EXTS or "_silence_removed_podcli" in name:
                continue
            found.append(str((Path(dirpath) / name).resolve()))
    return found


def _timecode_seconds(info: dict, fps: float, sample_rate: int) -> float:
    """Embedded start timecode (pro cameras) or BWF time reference (field recorders), in seconds."""
    tags = [info.get("format", {}).get("tags") or {}] + [s.get("tags") or {} for s in info.get("streams", [])]
    for t in tags:
        tc = t.get("timecode") or t.get("TIMECODE")
        if tc and fps > 0:
            parts = re.split(r"[:;.]", tc)
            if len(parts) == 4 and all(p.isdigit() for p in parts):
                h, m, sec, frames = (int(p) for p in parts)
                return h * 3600 + m * 60 + sec + frames / round(fps)
    for t in tags:
        ref = t.get("time_reference")
        if ref and str(ref).isdigit() and sample_rate > 0:
            return int(ref) / sample_rate
    return 0.0


def probe_source(path: str) -> Source:
    info = get_video_info(path)
    streams = info.get("streams", [])
    video = next(
        (s for s in streams if s.get("codec_type") == "video"
         and not (s.get("disposition") or {}).get("attached_pic")),
        None,
    )
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    durations = [float(info.get("format", {}).get("duration") or 0)]
    durations += [float(s.get("duration") or 0) for s in streams]
    duration = max(durations)
    if duration <= 0:
        raise RuntimeError(f"Could not read the duration of {os.path.basename(path)}")
    fps = 0.0
    if video:
        num, _, den = str(video.get("avg_frame_rate") or video.get("r_frame_rate") or "0/1").partition("/")
        try:
            fps = float(num) / float(den or 1) if float(den or 1) else 0.0
        except ValueError:
            fps = 0.0
        if not 1 <= fps <= 240:
            fps = 30.0
    sample_rate = int(audio.get("sample_rate") or 0) if audio else 0
    return Source(
        # Built from name and size so the same recording gets the same id
        # on another machine and a saved edit or cut list still points at it.
        id=hashlib.sha1(f"{os.path.basename(path)}:{os.path.getsize(path)}".encode()).hexdigest()[:10],
        path=os.path.abspath(path),
        kind="video" if video else "audio",
        duration=round(duration, 3),
        has_audio=audio is not None,
        audio_channels=int(audio.get("channels") or 0) if audio else 0,
        width=int(video.get("width") or 0) if video else 0,
        height=int(video.get("height") or 0) if video else 0,
        fps=round(fps, 3),
        timecode=round(_timecode_seconds(info, fps, sample_rate), 6),
    )


_WIDE = re.compile(r"(^|[^a-z])(wide|master|main|both|all|group|ws|two.?shot|2.?shot|overview)([^a-z]|$)")
_MIX = re.compile(r"(^|[^a-z])(mix|lr|stereo|master.?mix|program)([^a-z]|$)")
_TRACK = re.compile(r"(?:tr|track|ch|channel|in|input|mic)[ _-]?0*(\d{1,2})(?!\d)")


def _tokens(path: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", Path(path).stem.lower()).strip()


def guess_roles(session: MulticamSession) -> None:
    """Fill in role/person for sources the user hasn't mapped yet.

    Filenames that mention a person or 'wide' win. Otherwise cameras and mic
    tracks go to people in filename order, which matches how most recorders
    number their files. Everything guessed stays flagged so the UI can ask.
    """
    names = {p.id: _tokens(p.name) for p in session.people}
    people = session.person_ids()

    def named(stem: str) -> str:
        for pid, name in names.items():
            if name and re.search(rf"(^|\s){re.escape(name)}(\s|$)", stem):
                return pid
            if re.search(rf"(^|\s){re.escape(pid)}(\s|$)", stem):
                return pid
        return ""

    videos = [s for s in session.sources if s.kind == "video" and s.guessed]
    audios = [s for s in session.sources if s.kind == "audio" and s.guessed]

    unnamed_cams = []
    for s in videos:
        stem = _tokens(s.path)
        s.role = "camera"
        if _WIDE.search(stem):
            s.person = "wide"
        elif named(stem):
            s.person = named(stem)
        else:
            unnamed_cams.append(s)
    if len(session.cameras()) == 1 and unnamed_cams:
        unnamed_cams[0].person = "wide"
    else:
        taken = {s.person for s in videos if s.person}
        free = [p for p in people if p not in taken]
        for s in unnamed_cams:
            s.person = free.pop(0) if free else "wide"

    unnamed_mics = []
    for s in audios:
        stem = _tokens(s.path)
        if not s.has_audio:
            s.role = "ignore"
            continue
        s.role = "mic"
        who = named(stem)
        if who:
            s.person = who
        elif _MIX.search(stem):
            s.person = ""
        elif s.audio_channels == 2 and len(people) >= 2 and len(audios) == 1:
            # A single stereo recorder file usually carries one person per side.
            s.channel_people = people[:2]
        else:
            unnamed_mics.append(s)

    def track_no(s: Source) -> int:
        m = _TRACK.search(Path(s.path).stem.lower())
        return int(m.group(1)) if m else 999

    unnamed_mics.sort(key=lambda s: (track_no(s), s.path))
    taken = {s.person for s in audios if s.person} | {p for s in audios for p in s.channel_people}
    free = [p for p in people if p not in taken]
    for s in unnamed_mics:
        s.person = free.pop(0) if free else ""


# ---------------------------------------------------------------------------
# Call layouts: panes inside one recording, or a split screen of several
# ---------------------------------------------------------------------------

def detect_panes(source: Source, samples: int = 8) -> list[list[float]]:
    """Tiles of a video-call gallery recording, as [x, y, w, h] fractions, left to right.

    Call apps separate tiles with a gutter (a strip of flat colour) or at least
    a hard straight edge. Both show up as columns or rows that look the same
    in every sampled frame; a single camera's picture has no such line. Empty
    when the recording isn't a gallery.
    """
    width = 320
    frames = []
    with tempfile.TemporaryDirectory(prefix="podcli_panes_") as tmp:
        for i in range(samples):
            out = Path(tmp) / f"{i}.gray"
            proc_run([
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                "-ss", f"{source.duration * (i + 1) / (samples + 1):.2f}", "-i", source.path,
                "-frames:v", "1", "-vf", f"scale={width}:-2,format=gray", "-f", "rawvideo", str(out),
            ], timeout=60, check=False)
            data = np.fromfile(out, dtype=np.uint8) if out.exists() else np.zeros(0, dtype=np.uint8)
            if len(data) and len(data) % width == 0:
                frames.append(data.reshape(-1, width).astype(np.float32))
    if len(frames) < 3:
        return []
    stack = np.stack(frames)

    def borders(block: np.ndarray, axis: int) -> list[tuple[int, int]]:
        """Runs of columns (axis 2) or rows (axis 1) that are app chrome between or around tiles."""
        span = block.shape[axis]
        other = 1 if axis == 2 else 2
        # App chrome (gutters, margins) is one solid colour; even a plain wall in a
        # downscaled, compressed camera picture varies more than this.
        flat = block.std(axis=other).max(axis=0) < 3
        diff = np.abs(np.diff(block, axis=axis)).mean(axis=other).min(axis=0)  # a hard edge in every frame
        edge = np.concatenate([diff > 40, [False]])
        runs, k = [], 0
        while k < span:
            if flat[k] or edge[k]:
                j = k
                while j < span and (flat[j] or edge[j]):
                    j += 1
                runs.append((k, j))
                k = j
            else:
                k += 1
        margins = [r for r in runs if r[0] == 0 or r[1] == span]
        # A gutter between tiles is thin; a wide plain gap is backdrop between two people in one shot.
        inner = [r for r in runs if r not in margins and r[1] - r[0] <= span * 0.05]
        # Tiles are equal sizes, so the inner borders must sit at every split of
        # one tiling (1/2; or 1/3 and 2/3; ...). A static edge in a picture, a
        # door frame or a dark wall, rarely lands on all of them at once.
        for n in (4, 3, 2):
            chosen = []
            for k in range(1, n):
                at = span * k / n
                near = [r for r in inner if r[0] - span * 0.03 <= at <= r[1] + span * 0.03]
                if not near:
                    break
                chosen.append(min(near, key=lambda r: abs((r[0] + r[1]) / 2 - at)))
            else:
                return sorted(margins + chosen)
        return margins

    def segments(lines: list[tuple[int, int]], span: int) -> list[tuple[float, float]]:
        bounds, cursor = [], 0
        for a, b in lines:
            if a > cursor:
                bounds.append((cursor, a))
            cursor = max(cursor, b)
        if cursor < span:
            bounds.append((cursor, span))
        return [(a / span, (b - a) / span) for a, b in bounds if (b - a) / span >= 0.15]

    height = stack.shape[1]
    panes = []
    # Rows first, then columns inside each row band: a 2+1 gallery has two
    # tiles on top and one centred below, so columns differ from band to band.
    for y, h in segments(borders(stack, 1), height):
        band = stack[:, int(y * height):int((y + h) * height), :]
        panes += [[round(x, 4), round(y, 4), round(w, 4), round(h, 4)] for x, w in segments(borders(band, 2), stack.shape[2])]
    return panes if len(panes) >= 2 else []


def add_call_panes(session: MulticamSession) -> None:
    """One call recording with several tiles: each tile becomes a person's camera.

    The whole frame stays as the wide shot. Pane-to-person follows the people
    order left to right, flagged as a guess the user can fix.
    """
    people = session.person_ids()
    real = [s for s in session.cameras() if not s.virtual]
    if len(real) != 1 or len(people) < 2 or any(s.parent for s in session.sources):
        return
    whole = real[0]
    panes = detect_panes(whole)
    if len(panes) < 2:
        return
    whole.person = "wide"
    for i, crop in enumerate(panes):
        session.sources.append(Source(
            id=hashlib.sha1(f"{whole.id}:{crop}".encode()).hexdigest()[:10], path=whole.path, kind="video",
            duration=whole.duration, has_audio=False, width=int(whole.width * crop[2]),
            height=int(whole.height * crop[3]), fps=whole.fps,
            role="camera" if i < len(people) else "ignore", person=people[i] if i < len(people) else "",
            parent=whole.id, crop=crop,
        ))


def _sounds_remote(session: MulticamSession, cams: list[Source]) -> bool:
    """True when each person's file hears mostly themselves, as call recordings do.

    Studio cameras all hear the same room, so their sound levels rise and fall
    together. Compared only while both files were recording; a camera without
    sound can't tell, so that counts as a studio.
    """
    if not all(c.has_audio for c in cams):
        return False
    length = int(np.ceil(session.timeline_duration() / sig.FRAME_SECONDS))
    levels = {c.id: _timeline_levels(session, c, -1, length) for c in cams}
    for i, a in enumerate(cams):
        for b in cams[i + 1:]:
            lo = max(0, int(max(a.timeline_start(), b.timeline_start()) / sig.FRAME_SECONDS))
            hi = max(0, int(min(a.timeline_end(), b.timeline_end()) / sig.FRAME_SECONDS))
            la, lb = levels[a.id][lo:hi], levels[b.id][lo:hi]
            heard = (la > -90) | (lb > -90)
            if heard.sum() < int(10 / sig.FRAME_SECONDS):
                return False
            alike = np.corrcoef(la[heard], lb[heard])[0, 1]
            if not np.isfinite(alike) or alike > 0.6:
                return False
    return True


def add_split_if_remote(session: MulticamSession) -> None:
    """One file per person and no wide camera: lay the people side by side as the wide shot.

    Done for a remote recording, told apart by sound, or whenever the remote
    style is chosen. The split holds people rather than files, so a person whose
    recording came in parts keeps one column, drawn from whichever part is
    rolling at each moment.
    """
    people = session.person_ids()
    cams = [s for s in session.cameras() if not s.virtual and s.synced and s.person in people]
    seated = [p for p in people if any(c.person == p for c in cams)]
    if len(seated) < 2 or any(s.person == "wide" for s in session.cameras()) or any(s.members for s in session.sources):
        return
    chosen = session.cut_settings.get("style") == "remote"
    one_each = [next(c for c in cams if c.person == p) for p in seated]
    if not chosen and not _sounds_remote(session, one_each):
        return
    first = one_each[0]
    session.sources.append(Source(
        id=hashlib.sha1(f"split:{seated}".encode()).hexdigest()[:10], path="", kind="video",
        duration=first.duration, has_audio=False, width=first.width, height=first.height, fps=first.fps,
        role="camera", person="wide", members=seated, guessed=False,
    ))


def place_views(session: MulticamSession) -> None:
    """Put virtual cameras on the timeline from the files they come from."""
    for v in session.sources:
        if v.parent:
            parent = session.source(v.parent)
            v.offset, v.speed, v.duration, v.sync = parent.offset, parent.speed, parent.duration, dict(parent.sync)
        elif v.members:
            files = [c for c in session.cameras() if not c.virtual and c.synced and c.person in v.members]
            if files:
                start = min(c.timeline_start() for c in files)
                end = max(c.timeline_end() for c in files)
                v.offset, v.speed, v.duration = round(start, 4), 1.0, round(end - start, 3)
                v.sync = {"status": "ok"}
            else:
                v.offset, v.sync = None, {"status": "failed", "message": "Nobody in the split has a synced camera."}


def collect_files(folder: str = "", files: Optional[list] = None) -> list[str]:
    if files is not None and not (isinstance(files, list) and all(isinstance(f, str) for f in files)):
        raise ValueError("files must be a list of file paths")
    found = list(files or [])
    if folder:
        found += scan_folder(folder)
    found = list(dict.fromkeys(os.path.abspath(p) for p in found))
    if not found:
        raise ValueError("No video or audio files found. Pick the folder that holds one episode's recordings.")
    if len(found) > MAX_SOURCES:
        raise ValueError(f"Found {len(found)} media files. Point at one episode's folder ({MAX_SOURCES} files at most).")
    return found


def find_session(found: list[str]) -> Optional[MulticamSession]:
    """The saved edit for exactly these files, including ones that couldn't be read."""
    wanted = set(found)
    for p in sorted(_sessions_dir().glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True):
        try:
            session = MulticamSession.load(p.stem)
        except (ValueError, OSError, TypeError, json.JSONDecodeError):
            continue
        if {s.path for s in session.sources if not s.members} | set(session.skipped) == wanted:
            return session
    return None


def rename_people(session: MulticamSession, names: list[str]) -> MulticamSession:
    """Rename people in order, keeping ids so every file mapped to them stays mapped."""
    ids = [p.id for p in session.people]
    return update_mapping(session, {"people": [
        {"id": ids[i], "name": n} if i < len(ids) else {"name": n} for i, n in enumerate(names)
    ]})


def new_session(
    *,
    folder: str = "",
    files: Optional[list] = None,
    people: Optional[list] = None,
    name: str = "",
    progress_callback: ProgressCallback = None,
) -> MulticamSession:
    """Scan and probe one episode's files, or reopen the saved edit for the same files."""
    found = collect_files(folder, files)
    # Reopening returns the saved edit untouched; renaming people is an edit the
    # caller makes after, under the same lock as any other.
    existing = find_session(found)
    if existing:
        _emit(progress_callback, 100, f"Reopened the edit for these {len(found)} files")
        return existing
    named = [(str(p.get("name", "")).strip(), p.get("role")) if isinstance(p, dict) else (str(p or "").strip(), None)
             for p in (people or [])]
    named = [(n, r) for n, r in named if n]

    sources, skipped = [], []
    for i, p in enumerate(found):
        _emit(progress_callback, 5 + 90 * i / len(found), f"Reading {os.path.basename(p)}")
        try:
            sources.append(probe_source(p))
        except Exception as e:  # unreadable sidecar files shouldn't block the rest
            skipped.append(p)
            _emit(progress_callback, 5 + 90 * i / len(found), f"Skipped {os.path.basename(p)}: {e}")
    if not sources:
        raise ValueError("None of the files could be read as audio or video.")
    seen: dict[str, int] = {}
    for src in sources:
        # Two cameras can write the same file name at the same size (C0001.MP4).
        n = seen.get(src.id, 0)
        seen[src.id] = n + 1
        if n:
            src.id = hashlib.sha1(f"{src.id}:{n}".encode()).hexdigest()[:10]

    named = named or [("Host", None), ("Guest", None)]
    session = MulticamSession(
        session_id=uuid.uuid4().hex[:12],
        name=name or (Path(folder).name if folder else Path(found[0]).stem),
        folder=os.path.abspath(folder) if folder else "",
        # Most shows list the hosts first, so unless told, the last person is the guest.
        people=[
            Person(id=_person_id(n, i), name=n,
                   role=r if r in ("host", "guest") else "guest" if i == len(named) - 1 and i > 0 else "host")
            for i, (n, r) in enumerate(named)
        ],
        sources=sources,
        skipped=skipped,
    )
    guess_roles(session)
    add_call_panes(session)
    session.save()
    _emit(progress_callback, 100, f"Found {len(sources)} sources")
    return session


def apply_state(session: MulticamSession, state: dict) -> MulticamSession:
    """Restore a prepared edit (a saved --json payload) onto freshly opened files.

    A cloud worker is stateless: it downloads the recordings, opens them, and
    applies the state another worker prepared, so it can render without
    syncing again. Files are matched by id, which is stable across machines.
    Every field goes through the same checks as an edit made by hand.
    """
    if not isinstance(state, dict) or not isinstance(state.get("sources"), list) or not isinstance(state.get("people"), list):
        raise ValueError("A saved edit needs its people and sources")
    saved_sources = [x for x in state["sources"] if isinstance(x, dict) and isinstance(x.get("id"), str)]
    real = [s for s in session.sources if not s.virtual]
    real_ids = {s.id for s in real}
    # Tiles and split screens come from the saved edit as they were; detecting them again could differ.
    views = []
    for saved in saved_sources:
        parent, members = saved.get("parent") or "", saved.get("members") or []
        if not parent and not members:
            continue
        if parent and parent not in real_ids:
            raise ValueError(f"The saved edit has a tile cut from a file that isn't here ({parent})")
        crop = [_number(x, "crop", 0, 1) for x in (saved.get("crop") or [])]
        if parent and len(crop) != 4:
            raise ValueError("A saved tile needs a crop of x, y, width and height")
        base = next((s for s in real if s.id == parent), None)
        views.append(Source(
            id=saved["id"], path=base.path if base else "", kind="video", duration=base.duration if base else 0.0,
            has_audio=False, width=int(saved.get("width") or 0), height=int(saved.get("height") or 0),
            fps=float(saved.get("fps") or 0), parent=parent, crop=crop,
            members=[str(m) for m in members if isinstance(m, str)],
        ))
    session.sources = real + views
    ids = {s.id for s in session.sources}
    update_mapping(session, {
        "people": state["people"],
        "sources": [
            {k: x[k] for k in ("id", "role", "person", "channel_people") if k in x}
            for x in saved_sources if x["id"] in ids
        ],
    })
    for saved in saved_sources:
        if saved["id"] not in ids or saved.get("parent") or saved.get("members"):
            continue
        src = session.source(saved["id"])
        offset = saved.get("offset")
        src.offset = None if offset is None else _number(offset, "offset", -MAX_SECONDS, MAX_SECONDS)
        src.speed = _number(saved.get("speed", 1.0), "speed", 0.99, 1.01)
        src.sync = dict(saved.get("sync") or {})
    place_views(session)
    session.name = str(state.get("name") or session.name)
    if state.get("reference_id") in ids:
        session.reference_id = state["reference_id"]
    overrides = state.get("cut_overrides")
    if not isinstance(overrides, dict):
        overrides = state.get("cut_settings") if isinstance(state.get("cut_settings"), dict) else {}
    update_mapping(session, {
        "range_start": state.get("range_start"), "range_end": state.get("range_end"),
        "look": state.get("look") or "none", "cut_settings": overrides,
        "speaker_map": state.get("speaker_map") or {},
    })
    cuts = state.get("cuts") or []
    if cuts and all(isinstance(c, dict) and c.get("source_id") in ids for c in cuts):
        set_cuts(session, cuts)
    set_removals(session, state.get("removals") or [])
    session.activity_key = ""
    session.save()
    return session


def needs_sync(session: MulticamSession) -> bool:
    return any(_in_use(session, s) and not s.virtual and not s.synced for s in session.sources)


def _in_use(session: MulticamSession, s: Source) -> bool:
    """Mapped to something, or the recording a tile in use is cut from (even when the whole frame is ignored)."""
    return s.role != "ignore" or any(v.parent == s.id and v.role != "ignore" for v in session.sources)


def _owners(session: MulticamSession, s: Source) -> set[str]:
    """Whose voice or face a file carries, when it belongs to particular people."""
    people = set(session.person_ids())
    return ({s.person} & people) | (set(s.channel_people) & people)


def _person_id(name: str, index: int) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or f"person-{index + 1}"


def update_mapping(session: MulticamSession, params: dict) -> MulticamSession:
    """Apply user edits: people, per-source role/person/channels/offset, range, look, cut settings.

    Any edit that changes who is on which camera or mic, or where a file sits,
    drops the planned cut so the next cut reflects it.
    """
    before = _cut_inputs(session)
    if "people" in params:
        people = []
        roles = {p.id: p.role for p in session.people}
        for i, entry in enumerate(params["people"] or []):
            name = str(entry.get("name") if isinstance(entry, dict) else entry).strip()
            if not name:
                continue
            pid = (entry.get("id") if isinstance(entry, dict) else None) or _person_id(name, i)
            if pid == "wide" or pid in {p.id for p in people}:
                pid = f"{pid}-{i + 1}"
            role = (entry.get("role") if isinstance(entry, dict) else None) or roles.get(pid, "host")
            if role not in {"host", "guest"}:
                raise ValueError(f"A person's role is host or guest; got {role!r}")
            people.append(Person(id=pid, name=name, role=role))
        if not people:
            raise ValueError("Add at least one person")
        session.people = people
        valid = set(session.person_ids())
        for s in session.sources:
            if s.members:
                s.members = [m for m in s.members if m in valid]
            if s.role == "camera" and s.person not in valid | {"wide"}:
                s.person = "wide"
            if s.role == "mic" and s.person and s.person not in valid:
                s.person = ""
            s.channel_people = [p if p in valid else "" for p in s.channel_people]

    valid = set(session.person_ids())
    edits = params.get("sources") or []
    if not isinstance(edits, list) or not all(isinstance(e, dict) for e in edits):
        raise ValueError("sources must be a list of {id, role, person, ...} objects")
    for edit in edits:
        s = session.source(str(edit.get("id")))
        if "role" in edit:
            role = edit["role"]
            if role not in {"camera", "mic", "ignore"}:
                raise ValueError(f"Unknown role {role!r}")
            if role == "camera" and s.kind != "video":
                raise ValueError(f"{os.path.basename(s.path)} has no video, so it can't be a camera")
            if role == "mic" and not s.has_audio:
                raise ValueError(f"{os.path.basename(s.path)} has no audio, so it can't be a mic")
            s.role = role
        if "person" in edit:
            person = edit["person"] or ""
            allowed = valid | ({"wide"} if s.role == "camera" else {""})
            if person not in allowed:
                raise ValueError(f"Unknown person {person!r}")
            s.person = person
        if "channel_people" in edit:
            chans = [c if c in valid else "" for c in (edit["channel_people"] or [])]
            s.channel_people = chans[: max(0, s.audio_channels)] if any(chans) else []
        if (("offset" in edit and edit["offset"] is not None) or "nudge" in edit) and s.virtual:
            raise ValueError("A tile or split screen moves with the files it comes from. Move those instead.")
        if "offset" in edit and edit["offset"] is not None:
            s.offset = _number(edit["offset"], "offset", -MAX_SECONDS, MAX_SECONDS)
            s.sync = {**s.sync, "status": "manual"}
        if "nudge" in edit:
            if s.offset is None:
                raise ValueError(f"{os.path.basename(s.path)} isn't synced yet. Set its offset first.")
            s.offset = _number(float(s.offset) + _number(edit["nudge"], "nudge", -3600, 3600), "offset",
                               -MAX_SECONDS, MAX_SECONDS)
            s.sync = {**s.sync, "status": "manual"}
        s.guessed = False

    if "range_start" in params:
        session.range_start = None if params["range_start"] is None else _number(params["range_start"], "start", 0, MAX_SECONDS)
    if "range_end" in params:
        session.range_end = None if params["range_end"] is None else _number(params["range_end"], "end", 0, MAX_SECONDS)
    if "look" in params:
        if params["look"] not in LOOKS:
            raise ValueError(f"Unknown look {params['look']!r}. Use: {', '.join(LOOKS)}")
        session.look = params["look"]
    if "cut_settings" in params:
        incoming = params["cut_settings"] or {}
        style = incoming.get("style", session.cut_settings.get("style", "auto"))
        if style != "auto" and style not in STYLES:
            raise ValueError(f"Cut style is one of auto, {', '.join(STYLES)}; got {style!r}")
        # A new style starts from its own defaults rather than the old style's tweaks.
        same = style == session.cut_settings.get("style", "auto")
        overrides = {k: v for k, v in session.cut_settings.items() if k != "style"} if same else {}
        for key, value in incoming.items():
            if key in CUT_LIMITS:
                lo, hi = CUT_LIMITS[key]
                overrides[key] = max(lo, min(hi, _number(value, key, -MAX_SECONDS, MAX_SECONDS)))
            elif key in ("hold_guest", "host_solo"):
                if not isinstance(value, bool):
                    raise ValueError(f"{key} is true or false")
                overrides[key] = value
            elif key != "style":
                raise ValueError(f"Unknown cut setting {key!r}")
        session.cut_settings = {"style": style}
        # Only what differs from the style is a tweak; keeping the rest would pin today's defaults.
        defaults = STYLES[resolved_style(session)]
        session.cut_settings.update({k: v for k, v in overrides.items() if v != defaults[k]})
    if "removals" in params:
        set_removals(session, params["removals"] or [])
    if "speaker_map" in params:
        session.speaker_map = {str(k): v for k, v in (params["speaker_map"] or {}).items() if v in valid}
    place_views(session)
    if _cut_inputs(session) != before:
        session.cuts = []
    session.save()
    return session


def _cut_inputs(session: MulticamSession) -> str:
    return json.dumps([
        [(s.id, s.role, s.person, s.channel_people, s.offset) for s in session.sources],
        [(p.id, p.role) for p in session.people], session.range_start, session.range_end,
        session.cut_settings, session.speaker_map,
    ], default=str)


# ---------------------------------------------------------------------------
# Audio extraction
# ---------------------------------------------------------------------------

def _extract(source: Source, out: Path, channel: int = -1, rate: int = SYNC_RATE) -> Path:
    if out.exists() and out.stat().st_mtime >= os.path.getmtime(source.path):
        return out
    tmp = out.with_suffix(".tmp.wav")
    pick = "pan=mono|c0=c0" if source.audio_channels < 2 else "pan=mono|c0=0.5*c0+0.5*c1"
    if channel >= 0:
        pick = f"pan=mono|c0=c{channel}"
    # Anchoring the first sample at container time zero keeps sync in the same
    # clock the render and editors use; audio often starts 20-100 ms after video.
    proc_run([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-i", source.path, "-vn", "-map", "0:a:0",
        "-af", f"aresample=async=1:first_pts=0,{pick}",
        "-ar", str(rate), "-acodec", "pcm_s16le", str(tmp),
    ], timeout=3600, check=True)
    os.replace(tmp, out)
    return out


def _read_wav(p: Path) -> np.ndarray:
    with wave.open(str(p), "rb") as w:
        return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)


# ---------------------------------------------------------------------------
# Sync
# ---------------------------------------------------------------------------

FINE_WINDOW = 10.0
FINE_SEARCH = 1.0


def _fine_points(ref: np.ndarray, src: np.ndarray, lag: float) -> list[tuple[float, float, float]]:
    """GCC-PHAT checkpoints across the overlap, as (source_s, timeline_s, weight)."""
    src_len = len(src) / SYNC_RATE
    ref_len = len(ref) / SYNC_RATE
    lo = max(0.0, -lag) + FINE_SEARCH
    hi = min(src_len, ref_len - lag) - FINE_WINDOW - FINE_SEARCH
    if hi <= lo:
        return []
    span = hi - lo
    # One checkpoint per 5 minutes, but at least three when there's room, so
    # drift is measurable even on short recordings.
    count = int(max(1, min(9, span // 300 + 1), min(3, span // (FINE_WINDOW * 1.5))))
    centers = [lo + (hi - lo) * (i + 0.5) / count for i in range(count)]
    pts = []
    for a in centers:
        s0 = int(a * SYNC_RATE)
        s_win = src[s0:s0 + int(FINE_WINDOW * SYNC_RATE)].astype(np.float32)
        r0 = int((a + lag - FINE_SEARCH) * SYNC_RATE)
        r_win = ref[r0:r0 + int((FINE_WINDOW + 2 * FINE_SEARCH) * SYNC_RATE)].astype(np.float32)
        if r0 < 0 or len(r_win) < int((FINE_WINDOW + 2 * FINE_SEARCH) * SYNC_RATE) or np.abs(s_win).max() < 50:
            continue
        shift, prominence = sig.gcc_phat(r_win, s_win, SYNC_RATE, FINE_SEARCH)
        if prominence < 8:
            continue
        # A drifting clock smears the match across the window, so the measured
        # shift belongs to the window's middle.
        mid = a + FINE_WINDOW / 2
        pts.append((mid, mid + lag + shift, prominence))
    return pts


def _match(ref: np.ndarray, ref_env: np.ndarray, src: np.ndarray) -> tuple[Optional[sig.ClockFit], dict]:
    """Where src sits on ref's clock, plus the report shown next to the file."""
    match = sig.coarse_lag(ref_env, sig.onset_envelope(src, SYNC_RATE))
    if match is None or not match.ok:
        return None, {
            "status": "failed",
            "score": round(match.score, 1) if match else 0,
            "message": "No shared sound found. Check the file belongs to this episode, or set the offset by hand.",
        }
    fit = sig.fit_clock(_fine_points(ref, src, match.lag_seconds))
    # Two different voices can line up by chance in the coarse pass; a real
    # match nearly always gets confirmed sample by sample, so an unconfirmed one
    # has to be overwhelming to count.
    if fit is None and (match.score < 20 or match.peak_ratio > 0.6):
        return None, {
            "status": "failed",
            "score": round(match.score, 1),
            "message": "No shared sound found. Check the file belongs to this episode, or set the offset by hand.",
        }
    if fit is None:
        return sig.ClockFit(match.lag_seconds, 1.0, 10.0, 0), {
            "status": "rough", "score": round(match.score, 1),
            "message": "Synced to within 10 ms; fine alignment found no clear speech.",
        }
    return fit, {
        "status": "ok",
        "score": round(match.score, 1),
        "checkpoints": fit.checkpoints,
        "residual_ms": round(fit.residual_ms, 1),
        "drift_ppm": round((fit.speed - 1.0) * 1e6, 1),
    }


def sync_session(
    session: MulticamSession,
    *,
    force: bool = False,
    progress_callback: ProgressCallback = None,
) -> MulticamSession:
    """Place every mapped source on one timeline by matching its audio.

    Offsets set by hand survive a re-sync unless force is set. A file that
    shares no sound with the reference gets a second try against every file
    that did sync, which covers cameras split into parts with no continuous mic.
    """
    candidates = [s for s in session.sources if _in_use(session, s) and s.has_audio and not s.virtual]
    if not candidates:
        raise ValueError("Map at least one camera or mic that has audio before syncing")
    # The longest recording is the one most likely to overlap every other file;
    # a mic wins a tie because recorders rarely stop mid-episode.
    reference = max(candidates, key=lambda s: (s.duration, s.role == "mic"))
    manual = [
        s for s in session.sources
        if s.role != "ignore" and s.synced and s.sync.get("status") == "manual" and not force and s is not reference
    ]
    # Manual offsets were measured against the previous timeline zero; re-express
    # them against this reference before its offset resets to zero.
    anchor = reference.timeline_start() if reference.synced else 0.0
    for s in manual:
        s.offset = float(s.offset) - anchor

    session.reference_id = reference.id
    work = _work_dir(session.session_id)

    def wav(s: Source) -> np.ndarray:
        return _read_wav(_extract(s, work / f"{s.id}.sync.wav"))

    _emit(progress_callback, 2, f"Reading reference audio from {os.path.basename(reference.path)}")
    ref = wav(reference)
    ref_env = sig.onset_envelope(ref, SYNC_RATE)
    reference.offset, reference.speed = 0.0, 1.0
    reference.sync = {"status": "reference"}

    others = [s for s in candidates if s is not reference and s not in manual]
    for i, s in enumerate(others):
        _emit(progress_callback, 10 + 75 * i / len(others), f"Syncing {os.path.basename(s.path)}")
        fit, s.sync = _match(ref, ref_env, wav(s))
        s.offset, s.speed = (fit.offset, fit.speed) if fit else (None, 1.0)
    del ref

    failed = [s for s in others if not s.synced]
    anchors = [s for s in others if s.synced]
    for i, s in enumerate(failed):
        _emit(progress_callback, 85 + 10 * i / len(failed), f"Retrying {os.path.basename(s.path)} against the other files")
        src = wav(s)
        for a in anchors:
            a_audio = wav(a)
            fit, report = _match(a_audio, sig.onset_envelope(a_audio, SYNC_RATE), src)
            if fit:
                s.offset, s.speed = a.timeline_start() + a.speed * fit.offset, a.speed * fit.speed
                s.sync = {**report, "via": a.id}
                anchors.append(s)
                break

    # A remote recording made with headphones: each person's files share no sound
    # with anyone else's. Riverside and Zoom start every track together, so each
    # person's group is taken to start with the rest and synced within itself.
    remaining = [s for s in failed if not s.synced]
    heard = {p for s in [reference, *others] if s.synced for p in _owners(session, s)}
    if remaining and all(_owners(session, s) for s in remaining) and not (
            {p for s in remaining for p in _owners(session, s)} & heard):
        while remaining:
            lead = max(remaining, key=lambda s: s.duration)
            lead.offset, lead.speed = 0.0, 1.0
            lead.sync = {"status": "assumed", "message": "No sound in common with the others, so it is taken to "
                                                         "start with them, as call recorders do. Nudge it if it's off."}
            lead_audio = wav(lead)
            lead_env = sig.onset_envelope(lead_audio, SYNC_RATE)
            rest = []
            for s in remaining:
                if s is lead:
                    continue
                fit, report = _match(lead_audio, lead_env, wav(s))
                if fit:
                    s.offset, s.speed, s.sync = fit.offset, fit.speed, {**report, "via": lead.id}
                else:
                    rest.append(s)
            remaining = rest

    for s in session.sources:
        if _in_use(session, s) and not s.has_audio and not s.virtual and s not in manual:
            s.offset = None
            s.sync = {"status": "failed", "message": "This camera recorded no audio. Set its offset by hand."}

    synced = [s for s in session.sources if s.synced and _in_use(session, s) and not s.virtual]
    shift = min(s.timeline_start() for s in synced)
    for s in synced:
        s.offset = round(float(s.offset) - shift, 4)
    add_split_if_remote(session)
    place_views(session)
    session.range_start = session.range_end = None
    session.cuts = []
    session.activity_key = ""
    session.save()
    _emit(progress_callback, 100, "Sources synced")
    return session


# ---------------------------------------------------------------------------
# Who speaks when
# ---------------------------------------------------------------------------

def _activity_key(session: MulticamSession) -> str:
    feeds = [(s.id, ch, pid, s.offset, s.speed) for s, ch, pid in session.person_mics()]
    blob = json.dumps([feeds, session.speaker_map, session.person_ids()], sort_keys=True, default=str)
    return hashlib.sha1(blob.encode()).hexdigest()[:16]


def _timeline_levels(session: MulticamSession, s: Source, channel: int, length: int) -> np.ndarray:
    work = _work_dir(session.session_id)
    samples = _read_wav(_extract(s, work / f"{s.id}.{'sync' if channel < 0 else f'ch{channel}'}.wav", channel=channel))
    own = sig.level_db(samples, SYNC_RATE)
    t = np.arange(length) * sig.FRAME_SECONDS
    idx = (t - s.timeline_start()) / s.speed / sig.FRAME_SECONDS
    inside = (idx >= 0) & (idx < len(own) - 1)
    out = np.full(length, -100.0, dtype=np.float32)
    out[inside] = own[idx[inside].astype(np.int64)]
    return out


def _diarized_labels(session: MulticamSession, length: int, progress_callback: ProgressCallback) -> np.ndarray:
    from services.speaker_detection import run_diarization

    reference = session.source(session.reference_id)
    wav = _extract(reference, _work_dir(session.session_id) / f"{reference.id}.diarize.wav", rate=16000)
    turns = run_diarization(
        str(wav),
        num_speakers=len(session.people) or None,
        progress_callback=lambda p, m: _emit(progress_callback, 10 + p * 0.7, m),
    )
    order = list(dict.fromkeys(t["speaker"] for t in turns))
    people = session.person_ids()
    for i, label in enumerate(order):
        if label not in session.speaker_map and i < len(people):
            session.speaker_map[label] = people[i]
    segments = [
        {
            "start": reference.timeline_start() + t["start"] * reference.speed,
            "end": reference.timeline_start() + t["end"] * reference.speed,
            "person": session.speaker_map.get(t["speaker"]),
        }
        for t in turns
    ]
    return sig.segments_to_frames(segments, people, length)


def _person_levels(session: MulticamSession, length: int, progress_callback: ProgressCallback = None) -> dict[str, np.ndarray]:
    """One timeline-aligned level track (dB per 10 ms) per person with a mic of their own."""
    feeds = [(s, ch, pid) for s, ch, pid in session.person_mics() if s.synced]
    per_person: dict[str, np.ndarray] = {}
    for i, (s, ch, pid) in enumerate(feeds):
        _emit(progress_callback, 10 + 70 * i / len(feeds), f"Listening to {os.path.basename(s.path)}")
        level = _timeline_levels(session, s, ch, length)
        per_person[pid] = np.maximum(per_person[pid], level) if pid in per_person else level
    return per_person


def speaker_labels(session: MulticamSession, progress_callback: ProgressCallback = None) -> np.ndarray:
    """Per-10ms timeline labels (person index, SILENT, BOTH), cached per mapping."""
    length = int(np.ceil(session.timeline_duration() / sig.FRAME_SECONDS))
    cache = _work_dir(session.session_id) / "activity.npy"
    key = _activity_key(session)
    if session.activity_key == key and cache.exists():
        labels = np.load(cache)
        if len(labels) == length:
            return labels

    per_person = _person_levels(session, length, progress_callback)
    if per_person:
        people = session.person_ids()
        speaking = [p for p in people if p in per_person]
        raw = sig.speaker_frames([per_person[p] for p in speaking])
        index = np.array([people.index(p) for p in speaking], dtype=np.int32)
        labels = np.where(raw >= 0, index[np.clip(raw, 0, None)], raw).astype(np.int32)
    else:
        _emit(progress_callback, 10, "No personal mics mapped; detecting speakers from the shared audio")
        labels = _diarized_labels(session, length, progress_callback)

    np.save(cache, labels)
    session.activity_key = _activity_key(session)
    session.save()
    return labels


# ---------------------------------------------------------------------------
# Cut plan
# ---------------------------------------------------------------------------

def _camera_spans(session: MulticamSession) -> list[sig.Camera]:
    return [
        sig.Camera(s.id, s.person or "wide", s.timeline_start(), s.timeline_end())
        for s in session.cameras() if s.synced
    ]


def plan_session(session: MulticamSession, progress_callback: ProgressCallback = None) -> MulticamSession:
    cams = _camera_spans(session)
    if not cams:
        raise ValueError("Sync at least one camera before planning cuts")
    labels = speaker_labels(session, progress_callback)
    _emit(progress_callback, 85, "Planning camera cuts")

    cover_start = min(c.start for c in cams)
    cover_end = max(c.end for c in cams)
    if session.range_start is None or session.range_end is None:
        bounds = sig.speech_bounds(labels)
        a, b = (bounds[0] - 1.0, bounds[1] + 1.0) if bounds else (cover_start, cover_end)
        session.range_start = round(max(cover_start, a), 2) if session.range_start is None else session.range_start
        session.range_end = round(min(cover_end, b), 2) if session.range_end is None else session.range_end
    start = max(cover_start, float(session.range_start))
    end = min(cover_end, float(session.range_end))
    if end - start < 1.0:
        raise ValueError("The episode range doesn't overlap any camera. Widen the start and end.")

    session.cuts = sig.plan_cuts(
        labels, session.person_ids(), cams,
        range_start=start, range_end=end,
        guests=frozenset(p.id for p in session.people if p.role == "guest"),
        **effective_cut(session),
    )
    if not session.cuts:
        raise ValueError("No camera covers the episode range. Check the camera mapping and the start and end.")
    session.save()
    _emit(progress_callback, 100, f"{len(session.cuts)} shots planned")
    return session


def cut_stats(session: MulticamSession) -> dict:
    if not session.cuts:
        return {}
    total = sum(c["end"] - c["start"] for c in session.cuts)
    per: dict[str, float] = {}
    for c in session.cuts:
        per[c["source_id"]] = per.get(c["source_id"], 0.0) + c["end"] - c["start"]
    return {
        "shots": len(session.cuts),
        "duration": round(total, 2),
        "average_shot": round(total / len(session.cuts), 2),
        "share": {k: round(v / total, 3) for k, v in per.items()},
    }


def set_cuts(session: MulticamSession, cuts: list) -> MulticamSession:
    """Replace the cut with a hand-edited one; neighbouring shots on the same camera merge.

    Shots must run back to back, and every camera must have been recording for
    its whole shot, so a render never reaches past the end of a file.
    """
    if not isinstance(cuts, list) or not cuts or len(cuts) > MAX_SHOTS:
        raise ValueError(f"cuts must be a list of 1 to {MAX_SHOTS} {{start, end, source_id}} shots")
    clean: list[dict] = []
    for c in cuts:
        if not isinstance(c, dict) or not {"start", "end", "source_id"} <= c.keys():
            raise ValueError("Each shot needs start, end and source_id")
        start = _number(c["start"], "start", 0, MAX_SECONDS)
        end = _number(c["end"], "end", 0, MAX_SECONDS)
        cam = session.source(str(c["source_id"]))
        if end - start < 0.1:
            raise ValueError("A shot must be at least 0.1 s long")
        if clean and abs(clean[-1]["end"] - start) > 1e-3:
            raise ValueError("Shots must run back to back with no gaps or overlaps")
        if cam.role != "camera" or not cam.synced:
            raise ValueError(f"{os.path.basename(cam.path)} isn't a synced camera")
        if cam.timeline_start() > start + 1e-3 or cam.timeline_end() < end - 1e-3:
            raise ValueError(f"{os.path.basename(cam.path)} wasn't recording for the whole shot at {start:.1f}s")
        if clean and clean[-1]["source_id"] == cam.id:
            clean[-1]["end"] = round(end, 3)
        else:
            clean.append({"start": round(start, 3), "end": round(end, 3), "source_id": cam.id})
    previous, session.cuts = session.cuts, clean
    if not kept_segments(session):
        session.cuts = previous
        raise ValueError("The removals cover this whole cut. Restore some of the episode first.")
    session.save()
    return session


def set_cut(session: MulticamSession, index: int, source_id: str) -> MulticamSession:
    """Swap one shot to another camera."""
    if not 0 <= index < len(session.cuts):
        raise IndexError(f"No shot {index + 1}")
    cuts = [dict(c) for c in session.cuts]
    cuts[index]["source_id"] = source_id
    return set_cuts(session, cuts)


def activity(session: MulticamSession) -> dict:
    """Who speaks when, as spans per person, for drawing the timeline's speaking lanes."""
    labels = speaker_labels(session)
    people = session.person_ids()

    def spans(mask: np.ndarray) -> list[list[float]]:
        edges = np.flatnonzero(np.diff(np.concatenate([[0], mask.astype(np.int8), [0]])))
        out: list[list[float]] = []
        for a, b in zip(edges[::2], edges[1::2]):
            start, end = a * sig.FRAME_SECONDS, b * sig.FRAME_SECONDS
            # Breaths and short pauses would draw as noise; join them into one phrase.
            if out and start - out[-1][1] < 0.25:
                out[-1][1] = round(end, 2)
            elif end - start >= 0.15:
                out.append([round(start, 2), round(end, 2)])
        return out

    return {
        "people": {pid: spans((labels == i) | (labels == sig.BOTH)) for i, pid in enumerate(people)},
        "both": spans(labels == sig.BOTH),
    }


# ---------------------------------------------------------------------------
# Previews
# ---------------------------------------------------------------------------

def _picture(session: MulticamSession, cam: Source, tl: float, width: int, height: int) -> tuple[list[str], str]:
    """ffmpeg inputs and a filter graph drawing `cam` at timeline second tl into [pic], width x height.

    A real camera is fitted to the frame; a pane is cropped out of its call
    recording first; a split screen fills one equal column per person.
    """
    fit = f"scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1"
    if cam.members:
        n = len(cam.members)
        pane = (width // n) & ~1
        args, chains = [], []
        for i, person in enumerate(cam.members):
            # Each column is that person's file rolling at this moment, or black between parts.
            rolling = next((c for c in session.cameras() if not c.virtual and c.synced and c.person == person
                            and c.timeline_start() <= tl < c.timeline_end()), None)
            if rolling:
                args += ["-ss", f"{max(0.0, rolling.source_time(tl)):.4f}", "-i", rolling.path]
            else:
                args += ["-f", "lavfi", "-i", f"color=c=black:s={pane}x{height}"]
            chains.append(f"[{i}:v:0]scale={pane}:{height}:force_original_aspect_ratio=increase,crop={pane}:{height},setsar=1[p{i}]")
        joined = "".join(f"[p{i}]" for i in range(n))
        return args, ";".join(chains) + f";{joined}hstack=inputs={n},pad={width}:{height}:(ow-iw)/2:(oh-ih)/2[pic]"
    src = session.source(cam.parent) if cam.parent else cam
    crop = ""
    if cam.crop:
        x, y, w, h = cam.crop
        crop = f"crop=iw*{w:.4f}:ih*{h:.4f}:iw*{x:.4f}:ih*{y:.4f},"
    return ["-ss", f"{max(0.0, src.source_time(tl)):.4f}", "-i", src.path], f"[0:v:0]{crop}{fit}[pic]"


def _still(session: MulticamSession, cam: Source, tl: float, out: Path, look: str = "none", width: int = 480) -> Path:
    args, graph = _picture(session, cam, tl, width, (width * 9 // 16) & ~1)
    tail = f";[pic]{LOOKS[look]}[still]" if LOOKS.get(look) else ";[pic]null[still]"
    proc_run([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *args,
        "-filter_complex", graph + tail, "-map", "[still]", "-frames:v", "1", "-q:v", "4", str(out),
    ], timeout=60, check=True)
    return out


def previews(session: MulticamSession, *, looks: bool = False, at: Optional[float] = None) -> dict:
    """One still per camera, and optionally one still per look from the wide camera."""
    work = _work_dir(session.session_id)
    frames: dict = {"cameras": {}, "looks": {}}

    def moment(s: Source) -> float:
        if at is not None and s.synced and s.timeline_start() <= at < s.timeline_end():
            return at
        return s.timeline_start() + s.duration * s.speed * 0.3

    for s in session.sources:
        if s.kind != "video":
            continue
        t = moment(s)
        out = work / f"frame-{s.id}-{int(t * 10)}.jpg"
        if not out.exists():
            _still(session, s, t, out)
        frames["cameras"][s.id] = str(out)
    if looks:
        cams = session.cameras() or [s for s in session.sources if s.kind == "video"]
        if cams:
            cam = next((c for c in cams if c.person == "wide"), cams[0])
            for name in LOOKS:
                out = work / f"look-{cam.id}-{name}.jpg"
                if not out.exists():
                    _still(session, cam, moment(cam), out, look=name, width=640)
                frames["looks"][name] = str(out)
    return frames


def build_preview(session: MulticamSession, progress_callback: ProgressCallback = None) -> MulticamSession:
    """Small proxies of every recorded camera, one mic mix on the timeline, and a still per camera.

    This is what a browser editor (the podcli cloud editor) plays from. Browsers
    can't play 4K, MXF, or MTS smoothly or at all; 540p H.264 with a keyframe
    every second seeks instantly. Tiles and split screens get no proxy of their
    own: an editor draws them from their parent's proxy and crop, or from each
    member's. Cached until a source or the sync changes.
    """
    work = _work_dir(session.session_id)
    # Panes and split screens are drawn from these by their crop and members.
    cams = [s for s in session.cameras() if s.synced and not s.virtual]
    proxies: dict[str, str] = {}
    for i, cam in enumerate(cams):
        out = work / f"{cam.id}.proxy.mp4"
        if not (out.exists() and out.stat().st_mtime >= os.path.getmtime(cam.path)):
            _emit(progress_callback, 5 + 80 * i / max(1, len(cams)), f"Preparing preview of {os.path.basename(cam.path)}")
            tmp = out.with_suffix(".tmp.mp4")
            gop = str(max(1, round(cam.fps or 25)))
            proc_run([
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", cam.path, "-map", "0:v:0", "-an",
                "-vf", "scale=-2:540", "-c:v", "libx264", "-preset", "veryfast", "-crf", "28",
                "-g", gop, "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(tmp),
            ], timeout=7200, check=True)
            os.replace(tmp, out)
        proxies[cam.id] = str(out)

    audio = work / f"preview-{_mix_key(session)}.m4a"
    if not audio.exists():
        _emit(progress_callback, 88, "Mixing preview audio")
        tmp = audio.with_suffix(".tmp.m4a")
        _write_mix(session, tmp, 0.0, session.timeline_duration(),
                   encode=("-ac", "1", "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart"))
        os.replace(tmp, audio)
        for old in work.glob("preview-*.m4a"):
            if old != audio:
                old.unlink(missing_ok=True)
    # Edits keep landing while proxies encode, so write onto the latest saved copy.
    latest = MulticamSession.load(session.session_id)
    latest.preview = {"proxies": proxies, "audio": str(audio), "stills": previews(latest)["cameras"]}
    latest.save()
    _emit(progress_callback, 100, "Preview ready")
    return latest


def transcript(session: MulticamSession, *, model_size: str = "base", engine: Optional[str] = None,
               language: Optional[str] = None, progress_callback: ProgressCallback = None) -> dict:
    """Words on the timeline, each credited to the person who said it.

    The mics are mixed and transcribed once: transcribing each mic alone with
    the other voice silenced throws word timestamps seconds off. Each word goes
    to the mic that was loudest during it, relative to that mic's own speech
    level, then sentence edges settle what loose timestamps leave unclear at a
    turn change. Cached until the mix changes.
    """
    from services.transcription import transcribe_file

    work = _work_dir(session.session_id)
    # Who each word is credited to depends on the mic mapping as well as the mix.
    credit = [(s.id, ch, pid) for s, ch, pid in session.person_mics()]
    key = hashlib.sha1(json.dumps(
        [_mix_key(session), credit, session.speaker_map, model_size, engine, language], default=str,
    ).encode()).hexdigest()[:12]
    out = work / f"transcript-{key}.json"
    if out.exists():
        return json.loads(out.read_text(encoding="utf-8"))
    wav = work / "transcript-mix.wav"
    _emit(progress_callback, 2, "Mixing the mics for transcription")
    _write_mix(session, wav, 0.0, session.timeline_duration(), encode=("-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le"))
    try:
        result = transcribe_file(
            str(wav), model_size=model_size, engine=engine, language=language, enable_diarization=False,
            wav_path=str(wav), progress_callback=lambda p, m: _emit(progress_callback, 5 + p * 0.85, m),
        )
    finally:
        wav.unlink(missing_ok=True)
    labels = speaker_labels(session)
    people = session.person_ids()
    levels = {
        pid: level - np.percentile(level[level > -90], 95) if (level > -90).any() else level
        for pid, level in _person_levels(session, len(labels)).items()
    }

    def loudest(start: float, end: float) -> tuple[str, float]:
        """The credited person, and by how many dB their mic beat the next one."""
        a, b = max(0, int(start / sig.FRAME_SECONDS)), int(np.ceil(end / sig.FRAME_SECONDS))
        means = sorted(((float(np.mean(lv[a:b])), pid) for pid, lv in levels.items() if len(lv[a:b])), reverse=True)
        if means and means[0][0] > -30:
            return means[0][1], means[0][0] - (means[1][0] if len(means) > 1 else -100.0)
        return _speaker_at(labels, people, start, end), 0.0

    words, margins, last = [], [], ""
    for w in result.get("words") or []:
        start, end = float(w["start"]), float(w["end"])
        who, margin = loudest(start, end)
        last = who or last
        words.append({"start": round(start, 3), "end": round(end, 3), "text": str(w.get("word", "")).strip(), "person": last})
        margins.append(margin)
    _settle_turn_edges(words, margins)
    data = {"words": words, "language": result.get("language")}
    out.write_text(json.dumps(data), encoding="utf-8")
    _emit(progress_callback, 100, f"Transcribed {len(words)} words")
    return data


_SENTENCE_END = (".", "?", "!")


def _settle_turn_edges(words: list[dict], margins: list[float], clear: float = 6.0) -> None:
    """Fix credits that loose word timestamps get wrong around a turn change.

    Only words the mics couldn't tell apart (margin under `clear` dB) move. A
    lone word credited to someone else mid-sentence goes back to the person
    saying that sentence. The one word that opens a new sentence after the
    previous speaker's full stop goes to whoever speaks next: "...get home.
    Right," is the next speaker's "Right,".
    """
    def rejoin_strays() -> None:
        for i in range(1, len(words) - 1):
            prev, word, nxt = words[i - 1], words[i], words[i + 1]
            if (margins[i] < clear and word["person"] != prev["person"] and prev["person"] == nxt["person"]
                    and not prev["text"].endswith(_SENTENCE_END) and not word["text"].endswith(_SENTENCE_END)):
                word["person"] = prev["person"]

    rejoin_strays()
    for i in range(1, len(words) - 1):
        prev, word, nxt = words[i - 1], words[i], words[i + 1]
        if (margins[i] < clear and word["person"] == prev["person"] != nxt["person"]
                and prev["text"].endswith(_SENTENCE_END) and word["text"][:1].isupper()):
            word["person"] = nxt["person"]
    # Moving a sentence opener can leave the word after it stranded; rejoin it too.
    rejoin_strays()


def _speaker_at(labels: np.ndarray, people: list[str], start: float, end: float) -> str:
    """Whoever speaks most during a word, else around it within a second, else nobody."""
    for pad in (0.0, 1.0):
        a = max(0, int((start - pad) / sig.FRAME_SECONDS))
        b = min(len(labels), int(np.ceil((end + pad) / sig.FRAME_SECONDS)))
        span = labels[a:b]
        span = span[span >= 0]
        if len(span):
            return people[int(np.bincount(span).argmax())]
    return ""


# ---------------------------------------------------------------------------
# Render
# ---------------------------------------------------------------------------

def _output_format(session: MulticamSession) -> tuple[int, int, float]:
    """The episode's frame: the wide shot's, else the biggest landscape camera's.

    One camera's size is used whole, so a guest on a portrait phone never
    stretches a landscape show into a square.
    """
    cams = [s for s in session.cameras() if s.synced and s.width and s.height]
    wide = [c for c in cams if c.person == "wide"]
    pick = wide[0] if wide else max(cams, key=lambda c: (c.width >= c.height, c.width * c.height))
    rates = [_standard_fps(s.fps) for s in cams if not s.virtual] or [_standard_fps(pick.fps)]
    return pick.width - pick.width % 2, pick.height - pick.height % 2, max(set(rates), key=rates.count)


STANDARD_FPS = (24000 / 1001, 24.0, 25.0, 30000 / 1001, 30.0, 50.0, 60000 / 1001, 60.0)


def _standard_fps(fps: float) -> float:
    """Snap phone rates like 29.92 to the broadcast rate editors expect."""
    nearest = min(STANDARD_FPS, key=lambda r: abs(r - fps))
    return nearest if abs(nearest - fps) / nearest < 0.01 else fps


def _output_dir(session: MulticamSession) -> Path:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", session.name).strip("-") or "episode"
    d = Path(paths["output"]) / f"{slug}-{session.session_id[:6]}_multicam_podcli"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _render_shot(session: MulticamSession, cam: Optional[Source], out: Path, tl_start: float, frames: int,
                 width: int, height: int, fps: float, look: str) -> None:
    """Encode exactly `frames` frames starting at timeline second tl_start.

    cam None renders black, which keeps picture and sound aligned across a
    stretch no camera covered.
    """
    common = [
        "-frames:v", str(frames), "-an",
        "-c:v", "libx264", "-crf", "18", "-preset", "fast", "-pix_fmt", "yuv420p",
        "-video_track_timescale", "90000", str(out),
    ]
    if cam is None:
        proc_run([
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", f"color=c=black:s={width}x{height}:r={fps:.6f}", *common,
        ], timeout=600, check=True)
        return
    args, graph = _picture(session, cam, tl_start, width, height)
    # A camera that stops a few frames early would shorten the shot and slip
    # every later shot against the audio; holding its last frame prevents that.
    tail = [f"fps={fps:.6f}", "tpad=stop_mode=clone:stop=-1", *([LOOKS[look]] if LOOKS.get(look) else [])]
    proc_run([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *args,
        "-filter_complex", f"{graph};[pic]{','.join(tail)}[v]", "-map", "[v]", *common,
    ], timeout=3600, check=True)


def _audio_inputs(session: MulticamSession) -> list[tuple[Source, int]]:
    feeds = [(s, ch) for s, ch, _ in session.person_mics() if s.synced]
    if feeds:
        return feeds
    rooms = [s for s in session.sources if s.role == "mic" and s.synced]
    if rooms:
        return [(max(rooms, key=lambda s: s.duration), -1)]
    return [(session.source(session.reference_id), -1)]


def _aligned_input(s: Source, channel: int, start: float, duration: float) -> tuple[list[str], list[str]]:
    """ffmpeg input args and mono filter steps that place a source on [start, start + duration]."""
    src_start = s.source_time(start)
    steps = ["aresample=async=1:first_pts=0"]
    if channel >= 0:
        steps.append(f"pan=mono|c0=c{channel}")
    elif s.audio_channels >= 2:
        steps.append("pan=mono|c0=0.5*c0+0.5*c1")
    else:
        steps.append("pan=mono|c0=c0")
    if abs(s.speed - 1.0) > 1e-7:
        steps.append(f"atempo={1.0 / s.speed:.8f}")
    if src_start < 0:
        steps.append(f"adelay={int(round(-src_start * 1000))}")
    steps.append(f"apad,atrim=0:{duration:.4f}")
    return ["-ss", f"{max(0.0, src_start):.4f}", "-i", s.path], steps


def _write_mix(session: MulticamSession, out: Path, start: float, duration: float, *,
               per_feed: tuple[str, ...] = (), after: str = "anull", encode: tuple[str, ...],
               feeds: Optional[list[tuple[Source, int]]] = None) -> None:
    """Mix every mic (or the room, or just `feeds`) onto [start, start + duration] of the timeline."""
    args: list[str] = []
    chains: list[str] = []
    for i, (s, ch) in enumerate(feeds if feeds is not None else _audio_inputs(session)):
        inp, steps = _aligned_input(s, ch, start, duration)
        args += inp
        chains.append(f"[{i}:a:0]{','.join([*steps, *per_feed])}[a{i}]")
    n = len(chains)
    mix = "".join(f"[a{i}]" for i in range(n)) + f"amix=inputs={n}:normalize=0," if n > 1 else "[a0]"
    proc_run([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *args,
        "-filter_complex", ";".join(chains) + f";{mix}{after}[out]", "-map", "[out]", *encode, str(out),
    ], timeout=7200, check=True)


def _mix_key(session: MulticamSession) -> str:
    """Changes whenever which mics are mixed, or where they sit, changes."""
    feeds = [(s.id, ch, s.offset, s.speed, os.path.getmtime(s.path)) for s, ch in _audio_inputs(session)]
    return hashlib.sha1(json.dumps(feeds, default=str).encode()).hexdigest()[:12]


def _render_audio(session: MulticamSession, out: Path, start: float, duration: float) -> None:
    _write_mix(
        session, out, start, duration,
        per_feed=("dynaudnorm=f=250:g=15:p=0.9",),
        after="loudnorm=I=-16:TP=-1.5:LRA=11,aformat=channel_layouts=stereo",
        encode=("-ar", "48000", "-c:a", "pcm_s16le"),
    )


def _render_stems(
    session: MulticamSession, out_dir: Path, start: float, duration: float,
    splice: Optional[list[tuple[float, float]]] = None,
) -> list[str]:
    """One WAV per person; a person recorded across several files (recorder splits) gets them mixed in."""
    stems = []
    for pid in session.person_ids():
        feeds = [(s, ch) for s, ch, who in session.person_mics() if who == pid and s.synced]
        if not feeds:
            continue
        args, chains = [], []
        for i, (s, ch) in enumerate(feeds):
            inp, steps = _aligned_input(s, ch, start, duration)
            args += inp
            chains.append(f"[{i}:a:0]{','.join(steps)}[a{i}]")
        mix = "".join(f"[a{i}]" for i in range(len(feeds)))
        graph = ";".join(chains) + (f";{mix}amix=inputs={len(feeds)}:normalize=0[out]" if len(feeds) > 1 else ";[a0]anull[out]")
        # Person ids are unique slugs, so two people never share a file name.
        out = out_dir / f"{pid}.wav"
        full = out.with_name(f"{pid}.full.wav") if splice else out
        proc_run([
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *args,
            "-filter_complex", graph, "-map", "[out]", "-ar", "48000", "-c:a", "pcm_s24le", str(full),
        ], timeout=3600, check=True)
        if splice:
            _splice_audio(full, out, splice, ["-c:a", "pcm_s24le"])
            full.unlink(missing_ok=True)
        stems.append(str(out))
    return stems


@dataclass
class Piece:
    source_id: Optional[str]  # None renders black: no camera covered this stretch
    out_frame: int  # first frame in the finished episode
    frames: int
    tl_start: float  # timeline second shown on that first frame


def kept_segments(session: MulticamSession) -> list[tuple[float, float]]:
    """The episode range minus the removed stretches, in timeline seconds."""
    start, end = session.cuts[0]["start"], session.cuts[-1]["end"]
    kept, cursor = [], start
    for r in session.removals:
        a, b = max(start, r["start"]), min(end, r["end"])
        if b <= cursor or a >= end:
            continue
        if a > cursor:
            kept.append((cursor, a))
        cursor = max(cursor, b)
    if cursor < end:
        kept.append((cursor, end))
    return kept


def render_plan(session: MulticamSession, fps: float) -> list[Piece]:
    """Every shot, split at removals, laid end to end on one output frame grid.

    One grid over the whole episode keeps piece lengths from accumulating
    rounding drift against the audio; the MP4, the stems, and both editor
    timelines are all built from this list so they agree frame for frame.
    """
    pieces: list[Piece] = []
    out_t, out_f = 0.0, 0

    def emit(source_id: Optional[str], a: float, b: float) -> None:
        nonlocal out_t, out_f
        out_end = out_t + (b - a)
        f_end = round(out_end * fps)
        if f_end > out_f:
            pieces.append(Piece(source_id, out_f, f_end - out_f, a + (out_f / fps - out_t)))
            out_f = f_end
        out_t = out_end

    for a, b in kept_segments(session):
        t = a
        for c in session.cuts:
            if c["end"] <= t:
                continue
            if c["start"] >= b:
                break
            if c["start"] > t:
                emit(None, t, c["start"])
                t = c["start"]
            e = min(c["end"], b)
            emit(c["source_id"], t, e)
            t = e
        if t < b:
            emit(None, t, b)
    return pieces


SPLICE_BATCH = 60
FADE = 0.008


def _splice_audio(src: Path, dst: Path, segments: list[tuple[float, float]], codec: list[str]) -> None:
    """Keep only `segments` (seconds into src), with 8 ms fades so a cut never clicks."""
    work = dst.parent
    chunks = []
    for k in range(0, len(segments), SPLICE_BATCH):
        batch = segments[k:k + SPLICE_BATCH]
        parts = []
        for i, (a, b) in enumerate(batch):
            fade = min(FADE, (b - a) / 2)
            parts.append(
                f"[0:a]atrim=start={a:.5f}:end={b:.5f},asetpts=PTS-STARTPTS,"
                f"afade=t=in:d={fade:.4f},afade=t=out:st={b - a - fade:.5f}:d={fade:.4f}[s{i}]"
            )
        joined = "".join(f"[s{i}]" for i in range(len(batch)))
        chunk = work / f"{dst.stem}.part{k // SPLICE_BATCH:04d}.wav"
        proc_run([
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(src),
            "-filter_complex", ";".join(parts) + f";{joined}concat=n={len(batch)}:v=0:a=1[out]",
            "-map", "[out]", *codec, str(chunk),
        ], timeout=7200, check=True)
        chunks.append(chunk)
    listing = work / f"{dst.stem}.parts.txt"
    listing.write_text("".join(f"file '{c.as_posix()}'\n" for c in chunks), encoding="utf-8")
    proc_run([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "concat", "-safe", "0",
        "-i", str(listing), "-c", "copy", str(dst),
    ], timeout=3600, check=True)
    for c in chunks:
        c.unlink(missing_ok=True)
    listing.unlink(missing_ok=True)


def set_removals(session: MulticamSession, removals: list) -> MulticamSession:
    """Stretches cut out of the episode on every camera and mic, as [{start, end, reason?}]."""
    if not isinstance(removals, list) or len(removals) > MAX_REMOVALS:
        raise ValueError(f"removals must be a list of at most {MAX_REMOVALS} {{start, end}} stretches")
    if not all(isinstance(r, dict) and {"start", "end"} <= r.keys() for r in removals):
        raise ValueError("Each removal needs start and end")
    limit = session.timeline_duration() or MAX_SECONDS
    spans = sorted(
        ((_number(r["start"], "start", -MAX_SECONDS, MAX_SECONDS), _number(r["end"], "end", -MAX_SECONDS, MAX_SECONDS), r)
         for r in removals),
        key=lambda x: (x[0], x[1]),
    )
    clean: list[dict] = []
    for start, end, r in spans:
        a, b = max(0.0, start), min(limit, end)
        if b - a < 0.02:
            continue
        if clean and a <= clean[-1]["end"]:
            clean[-1]["end"] = round(max(clean[-1]["end"], b), 3)
        else:
            clean.append({"start": round(a, 3), "end": round(b, 3), **({"reason": str(r["reason"])} if r.get("reason") else {})})
    previous, session.removals = session.removals, clean
    if session.cuts and not kept_segments(session):
        session.removals = previous
        raise ValueError("That would remove the whole episode")
    session.save()
    return session


def _render_key(session: MulticamSession, stems: bool) -> str:
    """Everything the MP4 depends on, so an unchanged edit isn't rendered twice."""
    used = {c["source_id"] for c in session.cuts} | {s.id for s, _ in _audio_inputs(session)}
    files = [(s.id, s.offset, s.speed, s.channel_people, s.person, os.path.getmtime(s.path))
             for s in session.sources if s.id in used and os.path.exists(s.path)]
    blob = json.dumps([session.cuts, session.removals, session.look, stems, files], sort_keys=True, default=str)
    return hashlib.sha1(blob.encode()).hexdigest()[:16]


def render_session(
    session: MulticamSession,
    *,
    stems: bool = True,
    progress_callback: ProgressCallback = None,
) -> dict:
    if not session.cuts:
        raise ValueError("Plan the cuts before rendering")
    key = _render_key(session, stems)
    video = session.outputs.get("video")
    if session.outputs.get("render_key") == key and video and os.path.exists(video):
        _emit(progress_callback, 100, "Already rendered with these settings")
        return session.outputs
    width, height, fps = _output_format(session)
    start = session.cuts[0]["start"]
    out_dir = _output_dir(session)
    work = Path(tempfile.mkdtemp(prefix="podcli_multicam_", dir=paths["working"] if os.path.isdir(paths["working"]) else None))
    try:
        jobs = render_plan(session, fps)
        total_frames = sum(p.frames for p in jobs)
        duration = total_frames / fps
        end = session.cuts[-1]["end"]
        # Audio renders over the whole range, then the removed stretches come out.
        splice = [(a - start, b - start) for a, b in kept_segments(session)] if session.removals else None
        done = [0, 0]
        lock = threading.Lock()

        def run(item):
            i, piece = item
            chunk = work / f"shot-{i:05d}.mp4"
            cam = session.source(piece.source_id) if piece.source_id else None
            _render_shot(session, cam, chunk, piece.tl_start, piece.frames, width, height, fps, session.look)
            with lock:
                done[0] += piece.frames
                done[1] += 1
                _emit(progress_callback, 3 + 80 * done[0] / total_frames, f"Cut {done[1]} of {len(jobs)} shots")
            return chunk

        workers = max(1, min(4, (os.cpu_count() or 2) // 2))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            chunks = list(pool.map(run, enumerate(jobs)))

        _emit(progress_callback, 84, "Mixing microphones")
        audio = work / "audio.wav"
        _render_audio(session, audio, start, end - start)
        if splice:
            _splice_audio(audio, work / "kept.wav", splice, ["-c:a", "pcm_s16le"])
            audio = work / "kept.wav"

        _emit(progress_callback, 92, "Joining shots")
        listing = work / "shots.txt"
        listing.write_text("".join(f"file '{c.as_posix()}'\n" for c in chunks), encoding="utf-8")
        partial = work / "episode.mp4"
        proc_run([
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-f", "concat", "-safe", "0", "-i", str(listing), "-i", str(audio),
            "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-shortest",
            "-movflags", "+faststart", str(partial),
        ], timeout=3600, check=True)
        video = out_dir / "episode.mp4"
        # shutil.move falls back to copy+delete when work and output sit on different volumes.
        shutil.move(str(partial), str(video))

        stem_paths = []
        if stems:
            _emit(progress_callback, 96, "Writing separate mic tracks")
            stem_paths = _render_stems(session, out_dir, start, end - start, splice)

        session.outputs = {
            **session.outputs,
            "video": str(video),
            "stems": stem_paths,
            "duration": round(duration, 3),
            "render_key": key,
        }
        session.save()
        _emit(progress_callback, 100, "Episode ready")
        return session.outputs
    finally:
        shutil.rmtree(work, ignore_errors=True)


def export_xml(session: MulticamSession, fmt: str) -> str:
    from services.integrations._shared import multicam_xml

    if not session.cuts:
        raise ValueError("Plan the cuts before exporting")
    if fmt not in {"premiere", "fcpxml"}:
        raise ValueError("Export format must be 'premiere' or 'fcpxml'")
    if any(session.source(c["source_id"]).virtual for c in session.cuts):
        raise ValueError("Editor timelines can't carry call layouts (tiles cut from one recording, or a split "
                         "screen) yet. Render the MP4 instead.")
    width, height, fps = _output_format(session)
    out_dir = _output_dir(session)
    if fmt == "premiere":
        out = out_dir / "episode-premiere.xml"
        multicam_xml.write_xmeml(session, out, width=width, height=height, fps=fps)
    else:
        out = out_dir / "episode.fcpxml"
        multicam_xml.write_fcpxml(session, out, width=width, height=height, fps=fps)
    session.outputs = {**session.outputs, fmt: str(out)}
    session.save()
    return str(out)


# ---------------------------------------------------------------------------
# Listing and payloads
# ---------------------------------------------------------------------------

def list_sessions() -> list[dict]:
    items = []
    for p in sorted(_sessions_dir().glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True):
        try:
            s = MulticamSession.load(p.stem)
        except Exception:
            continue
        items.append({
            "session_id": s.session_id,
            "name": s.name,
            "sources": len([x for x in s.sources if not x.virtual]),
            "cameras": len([x for x in s.cameras() if not x.virtual]),
            "synced": all(x.synced for x in s.sources if x.role != "ignore"),
            "shots": len(s.cuts),
            "video": s.outputs.get("video") if s.outputs.get("video") and os.path.exists(s.outputs["video"]) else None,
        })
    return items


def delete_session(session_id: str) -> bool:
    s = MulticamSession.load(session_id)
    s.path().unlink(missing_ok=True)
    shutil.rmtree(Path(paths["working"]) / "multicam" / session_id, ignore_errors=True)
    return True


def source_label(session: MulticamSession, s: Source) -> str:
    """A file's name, or what a virtual camera shows."""
    if s.parent:
        tiles = [v.id for v in session.sources if v.parent == s.parent]
        return f"Tile {tiles.index(s.id) + 1} of {os.path.basename(session.source(s.parent).path)}"
    if s.members:
        names = {p.id: p.name for p in session.people}
        return "Split: " + " + ".join(names.get(m, m) for m in s.members)
    return os.path.basename(s.path)


def payload(session: MulticamSession) -> dict:
    data = asdict(session)
    data["timeline_duration"] = round(session.timeline_duration(), 3)
    data["stats"] = cut_stats(session)
    data["cut_settings"] = {**effective_cut(session), "style": session.cut_settings.get("style", "auto")}
    data["cut_overrides"] = dict(session.cut_settings)
    data["resolved_style"] = resolved_style(session)
    data["auto_style"] = "remote" if any(c.virtual for c in session.cameras()) else "studio"
    data["looks"] = list(LOOKS)
    data["has_person_mics"] = bool(session.person_mics())
    data["outputs"] = {
        k: v for k, v in session.outputs.items()
        if not isinstance(v, str) or not os.path.isabs(v) or os.path.exists(v)
    }
    proxies = {k: v for k, v in (session.preview.get("proxies") or {}).items() if os.path.exists(v)}
    audio = session.preview.get("audio")
    try:
        current = os.path.basename(audio or "") == f"preview-{_mix_key(session)}.m4a"
    except OSError:
        current = False
    # The mix file is named by the mic alignment, so a re-sync or remap leaves it stale.
    ready = current and os.path.exists(audio) and all(c.id in proxies for c in session.cameras() if c.synced and not c.virtual)
    stills = {k: v for k, v in (session.preview.get("stills") or {}).items() if os.path.exists(v)}
    data["preview"] = {"proxies": proxies, "audio": audio, "stills": stills} if ready else None
    for s in data["sources"]:
        s["name"] = source_label(session, session.source(s["id"]))
        s["timeline_start"] = s["offset"]
        s["timeline_end"] = None if s["offset"] is None else round(s["offset"] + s["duration"] * s["speed"], 3)
    return data
