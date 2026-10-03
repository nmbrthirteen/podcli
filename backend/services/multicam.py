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
from fractions import Fraction
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
    file_size: int = 0  # size at probe time; a mismatch on reopen means the file changed underneath us
    file_mtime_ns: int = 0  # mtime at probe time, nanosecond resolution
    fps_warning: str = ""  # set when the container's frame rate looks variable or had to be guessed
    audio_stream_count: int = 1  # separate audio streams in the container (not channels within one stream)
    audio_stream_index: int = 0  # which audio stream to use, for MXF-style cameras with one mono stream per mic
    audio_stream_channels: list[int] = field(default_factory=list)  # channel count per audio stream, by index
    role: str = "ignore"  # "camera" | "mic" | "ignore"
    # camera: a person id or "wide"; mic: a person id, or "" for a shared room mic
    person: str = ""
    # mic only: one person id per channel when a recorder puts two people on L/R
    channel_people: list[str] = field(default_factory=list)
    input_lut: str = ""  # camera only: absolute path to a 3D .cube LUT applied before the look
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

    def source_in(self, timeline_seconds: float, span: float) -> float:
        """Source second to start reading a `span`-second piece at, played at real speed.

        A drifting clock slips |speed - 1| * span across a piece; pinning the
        piece's middle instead of its start halves the worst slip.
        """
        middle = timeline_seconds + span / 2
        return self.source_time(middle) - span / 2


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


_TIMECODE_RE = re.compile(r"^(\d{2})([:;.])(\d{2})([:;.])(\d{2})([:;.])(\d{2})$")


def _ntsc_frame_duration(fps: float, r_frame_rate: str = "") -> tuple[float, int]:
    """Exact frame duration and the nominal (rounded) frame rate for an NTSC-pulldown fps.

    29.97 is really 30000/1001 fps, so each frame lasts 1001/30000 s, not 1/30 s. The
    same pulldown ratio applies to 59.94 and 23.976. Integer rates (25, 24, 30 exactly)
    have no pulldown and use a plain 1/fps duration.

    avg_frame_rate (what `fps` usually comes from) is measured, not declared, and a
    steady 25 fps camera can read 24.98 by measurement noise alone, which a bare
    closeness check mistakes for pulldown. r_frame_rate is the stream's exact time
    base; every real pulldown rate reduces to a fraction with denominator 1001, so
    when it's available that decides, not the noisy float.
    """
    if r_frame_rate:
        try:
            exact = Fraction(str(r_frame_rate))
        except (ValueError, ZeroDivisionError):
            exact = None
        if exact and exact > 0:
            rounded = round(float(exact))
            if rounded <= 0:
                return 0.0, 0
            if exact.denominator == 1001:
                return 1001.0 / (rounded * 1000.0), rounded
            return 1.0 / rounded, rounded
    rounded = round(fps)
    if rounded <= 0:
        return 0.0, 0
    if abs(fps - rounded) > 1e-3:
        return 1001.0 / (rounded * 1000.0), rounded
    return 1.0 / rounded, rounded


def _timecode_seconds(info: dict, fps: float, sample_rate: int, r_frame_rate: str = "") -> float:
    """Embedded start timecode (pro cameras) or BWF time reference (field recorders), in seconds.

    Honors drop-frame timecode (separator ';' before the frame field): drop-frame
    counters skip frame numbers :00 and :01 at the start of every minute except every
    tenth, so the raw H:M:S:F reading overstates elapsed time unless those skipped
    counts are added back before converting to seconds.
    """
    tags = [info.get("format", {}).get("tags") or {}] + [s.get("tags") or {} for s in info.get("streams", [])]
    for t in tags:
        tc = t.get("timecode") or t.get("TIMECODE")
        if tc and fps > 0:
            m = _TIMECODE_RE.match(str(tc).strip())
            if m:
                h, m1, mi, m2, sec, m3, frames = m.groups()
                h, mi, sec, frames = int(h), int(mi), int(sec), int(frames)
                drop_frame = m3 == ";"
                frame_duration, fps_round = _ntsc_frame_duration(fps, r_frame_rate)
                if fps_round <= 0:
                    continue
                total_frames = fps_round * 3600 * h + fps_round * 60 * mi + fps_round * sec + frames
                if drop_frame:
                    drop_per_min = 2 if fps_round == 30 else (4 if fps_round == 60 else 0)
                    total_minutes = 60 * h + mi
                    total_frames -= drop_per_min * (total_minutes - total_minutes // 10)
                return total_frames * frame_duration
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
    audio_streams = [s for s in streams if s.get("codec_type") == "audio"]
    audio = audio_streams[0] if audio_streams else None
    durations = [float(info.get("format", {}).get("duration") or 0)]
    durations += [float(s.get("duration") or 0) for s in streams]
    duration = max(durations)
    if duration <= 0:
        raise RuntimeError(f"Could not read the duration of {os.path.basename(path)}")
    fps = 0.0
    fps_warning = ""
    if video:
        def rate(field: str) -> float:
            num, _, den = str(video.get(field) or "0/1").partition("/")
            try:
                return float(num) / float(den or 1) if float(den or 1) else 0.0
            except ValueError:
                return 0.0

        avg_fps, r_fps = rate("avg_frame_rate"), rate("r_frame_rate")
        fps = avg_fps or r_fps
        # r_frame_rate is the stream's time base, a ceiling on how fast frames
        # could appear; avg_frame_rate is what actually played out. A material
        # gap between them means some frames held longer than others, so a shot
        # cut can't assume a fixed grid: the frame at a given timestamp drifts.
        if avg_fps and r_fps and abs(avg_fps - r_fps) / r_fps > 0.01:
            fps_warning = (f"Variable frame rate detected ({avg_fps:.3g} fps average, "
                            f"{r_fps:.3g} fps max): cuts may drift by a frame or more.")
        if not 1 <= fps <= 240:
            fps_warning = f"Could not read a usable frame rate ({fps or 'none'}); assuming 30 fps."
            fps = 30.0
    sample_rate = int(audio.get("sample_rate") or 0) if audio else 0
    stat = os.stat(path)
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
        timecode=round(_timecode_seconds(info, fps, sample_rate, video.get("r_frame_rate") if video else ""), 6),
        file_size=stat.st_size,
        file_mtime_ns=stat.st_mtime_ns,
        fps_warning=fps_warning,
        audio_stream_count=len(audio_streams),
        audio_stream_channels=[int(a.get("channels") or 0) for a in audio_streams],
    )


def _file_identity(s: Source) -> str:
    """Short fingerprint of the file state a source was last probed against.

    Used to key derived caches (extracted sync audio, activity) so that a
    file silently changing underneath its source (replaced, re-exported,
    re-encoded) can't serve stale cached work keyed only on the source id,
    which is built from basename and size and so does not change.
    """
    return hashlib.sha1(f"{s.file_size}:{s.file_mtime_ns}".encode()).hexdigest()[:8]


def _source_changed_on_disk(s: Source) -> bool:
    """True if the file at `s.path` no longer matches what was probed.

    A session saved before file identity was tracked has file_size and
    file_mtime_ns both at their dataclass default of 0. That is not evidence
    the file shrank to nothing; it means we never recorded an identity for
    it. Treat that pair as unknown rather than as a mismatch, so an old
    session doesn't look "changed" on every single source the first time it
    reopens under the new code.
    """
    if s.file_size == 0 and s.file_mtime_ns == 0:
        return False
    try:
        stat = os.stat(s.path)
    except OSError:
        return False
    return stat.st_size != s.file_size or stat.st_mtime_ns != s.file_mtime_ns


def backfill_file_identity(session: "MulticamSession") -> bool:
    """Stamp file_size/file_mtime_ns on sources saved before identity tracking existed.

    Stat-only, and never resets sync, cuts, or range: an old session without
    this fingerprint hasn't necessarily changed, it just predates the field.
    """
    changed = False
    for s in session.sources:
        if s.virtual or not s.path or (s.file_size or s.file_mtime_ns):
            continue
        try:
            stat = os.stat(s.path)
        except OSError:
            continue
        s.file_size, s.file_mtime_ns = stat.st_size, stat.st_mtime_ns
        changed = True
    return changed


def refresh_stale_sources(session: "MulticamSession") -> bool:
    """Re-probe any source whose file changed since it was last probed.

    A saved session reopens by matching the set of file paths alone, so a
    camera file swapped out for a re-export with the same name silently kept
    its old sync, offset and caches. Re-probe it, reset everything derived
    from its old bytes, and let the normal flows (sync, activity, previews)
    regenerate against the new file.
    """
    affected_in_use = False
    for s in session.sources:
        if s.virtual or not s.path or not _source_changed_on_disk(s):
            continue
        try:
            fresh = probe_source(s.path)
        except Exception:
            continue
        s.duration, s.has_audio, s.audio_channels = fresh.duration, fresh.has_audio, fresh.audio_channels
        s.width, s.height, s.fps, s.timecode = fresh.width, fresh.height, fresh.fps, fresh.timecode
        s.file_size, s.file_mtime_ns = fresh.file_size, fresh.file_mtime_ns
        s.fps_warning = fresh.fps_warning
        s.audio_stream_count = fresh.audio_stream_count
        s.audio_stream_channels = fresh.audio_stream_channels
        s.audio_stream_index = min(s.audio_stream_index, max(0, fresh.audio_stream_count - 1))
        s.offset, s.speed, s.sync = None, 1.0, {
            "status": "failed",
            "message": "This file changed on disk since it was last synced. Sync again.",
        }
        affected_in_use = affected_in_use or _in_use(session, s)
    if affected_in_use:
        session.activity_key = ""
        session.cuts = []
        session.range_start = session.range_end = None
        place_views(session)
    return affected_in_use


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


def open_session(session_id: str) -> MulticamSession:
    """Load a session by id, catching up on file-identity bookkeeping once.

    `new_session` already does this for the folder-reopen path. Everything
    that instead jumps straight to a known session id (the MCP tool, the CLI,
    the podcli cloud resolver) went through plain `MulticamSession.load` and
    so never noticed a camera file that changed or was replaced on disk.
    """
    session = MulticamSession.load(session_id)
    backfilled = backfill_file_identity(session)
    refreshed = refresh_stale_sources(session)
    if backfilled or refreshed:
        session.save()
    return session


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


def session_for_render(video_path: str) -> Optional[MulticamSession]:
    """The saved edit whose rendered episode is this file, if any."""
    target = os.path.realpath(video_path)
    for p in sorted(_sessions_dir().glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True):
        try:
            session = MulticamSession.load(p.stem)
        except (ValueError, OSError, TypeError, json.JSONDecodeError):
            continue
        video = session.outputs.get("video")
        if video and os.path.realpath(video) == target:
            return session
    return None


def render_to_timeline(session: MulticamSession, seconds: float) -> Optional[float]:
    """The timeline second shown at this second of the rendered episode, or None past its end.

    The render starts at the first cut and leaves the removed stretches out.
    """
    if not session.cuts or seconds < 0:
        return None
    left = seconds
    for a, b in kept_segments(session):
        if left < b - a:
            return a + left
        left -= b - a
    return None


def person_still(session: MulticamSession, person_id: str, timeline_seconds: float, out: Path,
                 width: int = 1280) -> Optional[dict]:
    """A still from this person's own camera at a timeline second.

    Returns {"path", "camera", "camera_time"}, or None when no camera of theirs
    is rolling then. A split screen is skipped: it shows everyone at once.
    """
    for cam in session.cameras():
        if cam.person != person_id or cam.members:
            continue
        src = session.source(cam.parent) if cam.parent else cam
        if not (src.synced and src.timeline_start() <= timeline_seconds < src.timeline_end()):
            continue
        _still(session, cam, timeline_seconds, out, width=width)
        return {"path": str(out), "camera": src.path,
                "camera_time": round(src.source_time(timeline_seconds), 3)}
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
        backfilled = backfill_file_identity(existing)
        refreshed = refresh_stale_sources(existing)
        if backfilled or refreshed:
            existing.save()
        if refreshed:
            _emit(progress_callback, 100, f"Reopened the edit for these {len(found)} files; "
                                           "one or more changed on disk and need syncing again")
        else:
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


def sync_basis_signature(session: MulticamSession) -> str:
    """Fingerprint of where every in-use source currently sits on the timeline.

    A cut or removal made in an external editor (the podcli cloud editor)
    references timeline seconds measured against this placement. If sync,
    a nudge, or a re-map moves anything after the edit was sent out, applying
    that cut back silently lands on the wrong footage: this lets callers
    detect that before it happens.
    """
    feeds = sorted((s.id, s.offset, s.speed) for s in session.sources if _in_use(session, s) and not s.virtual)
    return hashlib.sha1(json.dumps(feeds, default=str).encode()).hexdigest()[:16]


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
        if "audio_stream_index" in edit:
            idx = edit["audio_stream_index"]
            if not isinstance(idx, int) or not 0 <= idx < max(1, s.audio_stream_count):
                raise ValueError(f"{os.path.basename(s.path)} has {s.audio_stream_count} audio "
                                 f"stream(s); audio_stream_index must be between 0 and {s.audio_stream_count - 1}")
            s.audio_stream_index = idx
            if idx < len(s.audio_stream_channels):
                s.audio_channels = s.audio_stream_channels[idx]
            s.channel_people = []
        if "input_lut" in edit:
            if s.kind != "video" or s.virtual:
                raise ValueError("Only a camera file takes an input LUT; tiles and split screens use their files' LUTs")
            s.input_lut = _check_cube(edit["input_lut"]) if edit["input_lut"] else ""
        if (("offset" in edit and edit["offset"] is not None) or "nudge" in edit or "anchors" in edit) and s.virtual:
            raise ValueError("A tile or split screen moves with the files it comes from. Move those instead.")
        if "anchors" in edit:
            pairs, fit = _anchor_fit(s, edit["anchors"])
            s.offset, s.speed = fit.offset, fit.speed
            s.sync = {
                "status": "manual", "method": "manual", "anchors": pairs,
                "residual_ms": round(fit.residual_ms, 1), "drift_ppm": round((fit.speed - 1.0) * 1e6, 1),
            }
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


MAX_LUT_BYTES = 64 * 1024 * 1024


def _check_cube(path) -> str:
    """The path of a well-formed 3D .cube LUT, or a ValueError saying what's wrong with it."""
    if not isinstance(path, str) or not os.path.isabs(os.path.expanduser(path)):
        raise ValueError("input_lut must be an absolute path to a .cube file")
    p = Path(path).expanduser()
    if p.suffix.lower() != ".cube" or not p.is_file():
        raise ValueError(f"{path} isn't a .cube file")
    if p.stat().st_size > MAX_LUT_BYTES:
        raise ValueError(f"{p.name} is too big to be a LUT")
    size, entries = 0, 0
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        words = line.split()
        if not words or words[0].startswith("#"):
            continue
        key = words[0].upper()
        if key == "LUT_1D_SIZE":
            raise ValueError(f"{p.name} is a 1D LUT. A camera needs a 3D LUT (LUT_3D_SIZE).")
        if key == "LUT_3D_SIZE":
            size = int(_number(words[1] if len(words) > 1 else "", "LUT_3D_SIZE", 2, 256))
        elif key[0].isdigit() or key[0] in "+-.":
            if len(words) != 3:
                raise ValueError(f"{p.name} has a LUT entry without exactly three values: {line.strip()[:40]}")
            for w in words:
                _number(w, f"a value in {p.name}", -1e6, 1e6)
            entries += 1
    if not size:
        raise ValueError(f"{p.name} has no LUT_3D_SIZE, so it isn't a 3D LUT")
    if entries != size ** 3:
        raise ValueError(f"{p.name} declares a {size}-point cube ({size ** 3} entries) but holds {entries}")
    return str(p)


MAX_ANCHORS = 100


def _anchor_fit(s: Source, anchors) -> tuple[list[dict], sig.ClockFit]:
    """Offset and speed through hand-picked {timeline, source} second pairs, by least squares.

    One pair sets the offset alone. More fit drift too, within the bound an
    audio sync accepts. Every pair counts: unlike sync checkpoints, nobody
    picks an anchor by accident, so none is dropped as an outlier.
    """
    name = os.path.basename(s.path)
    if not isinstance(anchors, list) or not 1 <= len(anchors) <= MAX_ANCHORS:
        raise ValueError(f"anchors must be a list of 1 to {MAX_ANCHORS} {{timeline, source}} pairs in seconds")
    pairs = []
    for a in anchors:
        if not isinstance(a, dict) or not {"timeline", "source"} <= a.keys():
            raise ValueError("Each anchor needs timeline and source, in seconds")
        pairs.append({"timeline": _number(a["timeline"], "anchor timeline", -MAX_SECONDS, MAX_SECONDS),
                      "source": _number(a["source"], f"anchor source second in {name}", 0, s.duration)})
    pairs.sort(key=lambda p: p["source"])
    for a, b in zip(pairs, pairs[1:]):
        if b["source"] - a["source"] < 1e-3 or b["timeline"] <= a["timeline"]:
            raise ValueError("Anchors must move forward together: a later second in the file needs a later "
                             "second on the timeline, and no two anchors may share a file second")
    fit = sig.fit_clock([(p["source"], p["timeline"], 1.0) for p in pairs], max_residual=math.inf)
    if fit is None or fit.speed_fallback:
        first, last = pairs[0], pairs[-1]
        ppm = ((last["timeline"] - first["timeline"]) / (last["source"] - first["source"]) - 1.0) * 1e6
        raise ValueError(f"Those anchors put {name}'s clock {ppm:+.0f} ppm off the timeline; real drift stays within "
                         f"{sig.MAX_DRIFT * 1e6:.0f} ppm. Check each pair.")
    return pairs, fit


def _cut_inputs(session: MulticamSession) -> str:
    return json.dumps([
        [(s.id, s.role, s.person, s.channel_people, s.audio_stream_index, s.offset, s.speed) for s in session.sources],
        [(p.id, p.role) for p in session.people], session.range_start, session.range_end,
        session.cut_settings, session.speaker_map,
    ], default=str)


# ---------------------------------------------------------------------------
# Audio extraction
# ---------------------------------------------------------------------------

def _extract(source: Source, out: Path, channel: int = -1, rate: int = SYNC_RATE) -> Path:
    # Keyed on the probed file identity, not a live mtime check: a replaced
    # file can land within the same mtime second, and the id in `out`'s name
    # (basename:size) doesn't change either, so neither alone is reliable.
    out = out.with_name(f"{out.stem}-{_file_identity(source)}{out.suffix}")
    if out.exists():
        return out
    tmp = out.with_suffix(".tmp.wav")
    pick = "pan=mono|c0=c0" if source.audio_channels < 2 else "pan=mono|c0=0.5*c0+0.5*c1"
    if channel >= 0:
        pick = f"pan=mono|c0=c{channel}"
    # Anchoring the first sample at container time zero keeps sync in the same
    # clock the render and editors use; audio often starts 20-100 ms after video.
    proc_run([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-i", source.path, "-vn", "-map", f"0:a:{source.audio_stream_index}",
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
    overlap_seconds = min(len(ref), len(src)) / SYNC_RATE
    reasons = _sync_review_reasons(fit, match, overlap_seconds)
    report = {
        "status": "review" if reasons else "ok",
        "score": round(match.score, 1),
        "checkpoints": fit.checkpoints,
        "residual_ms": round(fit.residual_ms, 1),
        "residual_all_ms": round(fit.residual_all_ms, 1),
        "drift_ppm": round((fit.speed - 1.0) * 1e6, 1),
    }
    if reasons:
        report["reasons"] = reasons
        report["message"] = "Sync may be off: " + "; ".join(reasons) + ". Check it before rendering."
    return fit, report


def _sync_review_reasons(fit: sig.ClockFit, match: sig.CoarseMatch, overlap_seconds: float) -> list[str]:
    """Why a fit that otherwise looks like a clean sync should get a second look.

    A fit can report a tidy inlier residual while still being wrong: outliers
    get dropped before the residual is measured, drift can be forced back to
    speed 1 when it looked implausible, or there just weren't enough
    checkpoints to trust a long overlap's drift estimate.
    """
    reasons = []
    # residual_all_ms includes the checkpoints the fit already rejected as
    # outliers, so one bad checkpoint correctly thrown out would otherwise
    # flag an inlier fit that's actually clean. Judge the fit on the
    # residual it was actually built from; residual_all_ms is still reported.
    if fit.residual_ms > 30.0:
        reasons.append(f"residual {fit.residual_ms:.0f} ms")
    if fit.speed_fallback:
        reasons.append("drift fit was implausible, so speed was forced back to 1.0")
    dropped = fit.total_checkpoints - fit.checkpoints
    if fit.total_checkpoints and dropped / fit.total_checkpoints > 0.4:
        reasons.append(f"dropped {dropped} of {fit.total_checkpoints} checkpoints as outliers")
    if overlap_seconds > 600.0 and fit.checkpoints < 3:
        reasons.append("fewer than 3 checkpoints over a 10+ minute overlap")
    if match.peak_ratio > 0.5:
        reasons.append("weak correlation peak")
    return reasons


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
    feeds = [(s.id, _file_identity(s), s.audio_stream_index, ch, pid, s.offset, s.speed)
             for s, ch, pid in session.person_mics()]
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

def _picture(session: MulticamSession, cam: Source, tl: float, width: int, height: int,
             span: float = 0.0) -> tuple[list[str], str]:
    """ffmpeg inputs and a filter graph drawing `cam` from timeline second tl into [pic], width x height.

    A real camera is fitted to the frame; a pane is cropped out of its call
    recording first; a split screen fills one equal column per person. `span`
    is how long the piece plays, so its middle lands in sync on a drifting file.
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
                args += ["-ss", f"{max(0.0, rolling.source_in(tl, span)):.4f}", "-i", rolling.path]
            else:
                args += ["-f", "lavfi", "-i", f"color=c=black:s={pane}x{height}"]
            lut = _lut_step(rolling) if rolling else ""
            chains.append(f"[{i}:v:0]{lut}scale={pane}:{height}:force_original_aspect_ratio=increase,"
                          f"crop={pane}:{height},setsar=1[p{i}]")
        joined = "".join(f"[p{i}]" for i in range(n))
        return args, ";".join(chains) + f";{joined}hstack=inputs={n},pad={width}:{height}:(ow-iw)/2:(oh-ih)/2[pic]"
    src = session.source(cam.parent) if cam.parent else cam
    crop = ""
    if cam.crop:
        x, y, w, h = cam.crop
        crop = f"crop=iw*{w:.4f}:ih*{h:.4f}:iw*{x:.4f}:ih*{y:.4f},"
    return ["-ss", f"{max(0.0, src.source_in(tl, span)):.4f}", "-i", src.path], f"[0:v:0]{crop}{_lut_step(src)}{fit}[pic]"


def _filter_path(path: str) -> str:
    """A file path escaped for an option value inside an ffmpeg filter graph.

    Two parsers read it: the graph splits on , ; [ ] and the filter splits its
    options on :, and each strips one level of backslashes.
    """
    option = path.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")
    return "".join(f"\\{ch}" if ch in "\\'[],;" else ch for ch in option)


def _lut_step(s: Source) -> str:
    """The file's input LUT as a filter step ending in a comma, or ''.

    It runs before scaling and padding, so it maps the camera's own pixels and never tints the bars.
    """
    return f"lut3d=file={_filter_path(s.input_lut)}," if s.input_lut else ""


def _check_luts_exist(session: MulticamSession) -> None:
    """Fail now, naming the camera, rather than deep inside an ffmpeg worker later.

    A LUT set at map time can get moved or deleted on disk by the time a
    render or a preview actually reads it; without this, ffmpeg's own error
    surfaces from inside a shot or a still with no indication of which
    camera (or which of several in a split) it was for.
    """
    missing = [s for s in session.sources if s.input_lut and not os.path.isfile(s.input_lut)]
    if missing:
        names = ", ".join(f"{source_label(session, s)} ({s.input_lut})" for s in missing)
        raise ValueError(f"The LUT set for {names} is missing. Pick it again or clear it.")


def _still(session: MulticamSession, cam: Source, tl: float, out: Path, look: str = "none", width: int = 480) -> Path:
    args, graph = _picture(session, cam, tl, width, (width * 9 // 16) & ~1)
    tail = f";[pic]{LOOKS[look]}[still]" if LOOKS.get(look) else ";[pic]null[still]"
    proc_run([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *args,
        "-filter_complex", graph + tail, "-map", "[still]", "-frames:v", "1", "-q:v", "4", str(out),
    ], timeout=60, check=True)
    return out


def _still_basis(session: MulticamSession, s: Source) -> str:
    """Short fingerprint of the offset, speed and input LUT of every file a still reads.

    Stills are cached to disk by filename. The source's mapping from timeline
    time to source time (`source_time`) depends on offset and speed, so a
    cache key that only captures the timeline moment `at` goes stale the
    instant a nudge or re-sync changes that mapping: the filename looks the
    same but would now decode a different source frame. A new LUT recolors it.
    """
    files = _picture_files(session, s)
    raw = json.dumps([(f.offset, f.speed, _fingerprint(f.input_lut) if f.input_lut else None) for f in files])
    return hashlib.sha1(raw.encode()).hexdigest()[:8]


def previews(session: MulticamSession, *, looks: bool = False, at: Optional[float] = None) -> dict:
    """One still per camera, and optionally one still per look per camera, each through its input LUT."""
    _check_luts_exist(session)
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
        out = work / f"frame-{s.id}-{int(t * 10)}-{_still_basis(session, s)}.jpg"
        if not out.exists():
            # A nudge or re-sync changes the basis (and often the moment), so the
            # old filename never matches again; delete it rather than let every
            # edit leave one more still behind.
            for stale in work.glob(f"frame-{s.id}-*.jpg"):
                if stale != out:
                    stale.unlink(missing_ok=True)
            _still(session, s, t, out)
        frames["cameras"][s.id] = str(out)
    if looks:
        # Cameras rarely match out of the box, so each one shows every look on its own picture.
        candidates = session.cameras() or [s for s in session.sources if s.kind == "video"]
        # Once a cut exists, a camera that never actually appears in it (an
        # alternate angle left mapped but unused) doesn't need four look
        # renders; before any cut exists there's nothing to filter by yet.
        used = {c["source_id"] for c in session.cuts}
        for cam in [c for c in candidates if not used or c.id in used]:
            t = moment(cam)
            frames["looks"][cam.id] = {}
            for name in LOOKS:
                out = work / f"look-{cam.id}-{name}-{int(t * 10)}-{_still_basis(session, cam)}.jpg"
                if not out.exists():
                    for stale in work.glob(f"look-{cam.id}-{name}-*.jpg"):
                        if stale != out:
                            stale.unlink(missing_ok=True)
                    _still(session, cam, t, out, look=name, width=640)
                frames["looks"][cam.id][name] = str(out)
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


_GEORGIAN_RANGES = ((0x10A0, 0x10FF), (0x1C90, 0x1CBF))


def _looks_like_sentence_opener(text: str) -> bool:
    """True if `text` could start a new sentence, by case or by having none.

    `str.isupper()` alone misses caseless scripts: CJK, Arabic, Thai, and
    Hebrew letters are never upper or lower (`ch.upper() == ch.lower()`
    catches those). Georgian is a special case: Unicode still carries a
    Mtavruli uppercase mapping for it, so `str.isupper()`/`islower()` report
    it as cased, but real Georgian text is written only in the lowercase
    Mkhedruli form and never uses that case distinction, so every opener was
    silently dropped. Treat Georgian letters as potential openers too.
    """
    ch = text[:1]
    if not ch.isalpha():
        return False
    if ch.isupper() or ch.upper() == ch.lower():
        return True
    cp = ord(ch)
    return any(lo <= cp <= hi for lo, hi in _GEORGIAN_RANGES)


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
                and prev["text"].endswith(_SENTENCE_END) and _looks_like_sentence_opener(word["text"])):
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


# Bump when the shot pipeline changes in a way its cached files can't show,
# so a rerun re-encodes instead of reusing shots built the old way.
RENDER_VERSION = 1
SHOT_ENCODE = ("-c:v", "libx264", "-crf", "18", "-preset", "fast", "-pix_fmt", "yuv420p",
               "-video_track_timescale", "90000")


def _shot_command(session: MulticamSession, cam: Optional[Source], tl_start: float, frames: int,
                  width: int, height: int, fps: float, look: str) -> list[str]:
    """The ffmpeg command, minus its output path, that encodes exactly `frames` frames from timeline second tl_start.

    cam None renders black, which keeps picture and sound aligned across a
    stretch no camera covered.
    """
    common = ["-frames:v", str(frames), "-an", *SHOT_ENCODE]
    if cam is None:
        return ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                "-f", "lavfi", "-i", f"color=c=black:s={width}x{height}:r={fps:.6f}", *common]
    args, graph = _picture(session, cam, tl_start, width, height, frames / fps)
    # A camera that stops a few frames early would shorten the shot and slip
    # every later shot against the audio; holding its last frame prevents that.
    tail = [f"fps={fps:.6f}", "tpad=stop_mode=clone:stop=-1", *([LOOKS[look]] if LOOKS.get(look) else [])]
    return ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *args,
            "-filter_complex", f"{graph};[pic]{','.join(tail)}[v]", "-map", "[v]", *common]


def _picture_files(session: MulticamSession, cam: Source) -> list[Source]:
    """The recordings a camera's picture is read from: itself, its call recording, or each split member's files."""
    if cam.parent:
        return [session.source(cam.parent)]
    if cam.members:
        return [c for c in session.cameras() if not c.virtual and c.person in cam.members]
    return [cam]


def _shot_key(session: MulticamSession, cam: Optional[Source], command: list[str]) -> str:
    """Names a cached shot by everything that decides its pixels.

    The command carries the seek point, frame range, crop, look and encoder
    settings; the files it reads are fingerprinted live so a re-export under
    the same name can't serve an old shot.
    """
    files = [(_fingerprint(f.path), f.offset, f.speed, _fingerprint(f.input_lut) if f.input_lut else None)
             for f in (_picture_files(session, cam) if cam else [])]
    blob = json.dumps([RENDER_VERSION, command, files], default=str)
    return hashlib.sha1(blob.encode()).hexdigest()[:20]


def _fingerprint(path: str) -> tuple:
    """A file's path, size and mtime as they are on disk right now."""
    try:
        st = os.stat(path)
        return path, st.st_size, st.st_mtime_ns
    except OSError:
        return path, None, None


def _probe_value(path: Path, *args: str) -> str:
    res = proc_run(["ffprobe", "-v", "error", *args, "-of", "csv=p=0", str(path)], timeout=1800, check=False)
    lines = res.stdout.strip().splitlines() if res.returncode == 0 else []
    return lines[0].strip().rstrip(",") if lines else ""


def _frame_count(path: Path) -> int:
    """Video frames actually in a file, counted packet by packet rather than trusting the header."""
    value = _probe_value(path, "-select_streams", "v:0", "-count_packets", "-show_entries", "stream=nb_read_packets")
    return int(value) if value.isdigit() else -1


def _media_duration(path: Path, stream: str = "") -> float:
    value = _probe_value(path, *(["-select_streams", stream, "-show_entries", "stream=duration"] if stream
                                 else ["-show_entries", "format=duration"]))
    try:
        return float(value)
    except ValueError:
        return 0.0


LOUDNESS_TARGET = -16.0
TRUE_PEAK_CEILING = -1.5


def _loudness(path: Path) -> tuple[Optional[float], Optional[float]]:
    """Integrated loudness (LUFS) and true peak (dBTP) of a file's first audio stream, by EBU R128."""
    res = proc_run([
        "ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-map", "0:a:0",
        "-af", "ebur128=peak=true", "-f", "null", "-",
    ], timeout=3600, check=False)
    summary = res.stderr[res.stderr.rfind("Summary:"):] if "Summary:" in res.stderr else ""
    lufs = re.search(r"I:\s+(-?[\d.]+|-inf) LUFS", summary)
    peak = re.search(r"True peak:\s+Peak:\s+(-?[\d.]+|-inf) dBFS", summary)

    def number(m) -> Optional[float]:
        return float(m.group(1)) if m and m.group(1) != "-inf" else None

    return number(lufs), number(peak)


def _decode_errors(path: Path, *, full: bool = False) -> str:
    """Whatever ffmpeg complains about while decoding the episode, or '' when clean.

    A full decode is exhaustive but costs minutes per hour of 1080p on every
    single render, almost all of it spent re-confirming frames nothing
    touched. By default this instead decodes the first and last 10 s plus
    three points spread through the middle: enough to catch what a render
    is actually at risk of (a bad concat seam between shots, a broken mux,
    a codec fault at the point it happened) without paying for the whole
    file. Pass full=True (render_session(..., validate="full")) for the
    exhaustive check when that trade-off isn't acceptable.
    """
    if full:
        res = proc_run(["ffmpeg", "-hide_banner", "-v", "error", "-i", str(path), "-f", "null", "-"],
                       timeout=7200, check=False)
        return res.stderr.strip() or (f"ffmpeg exited with {res.returncode}" if res.returncode else "")
    duration = _media_duration(path) or 0.0
    spans = [0.0]
    if duration > 10.0:
        spans.append(max(0.0, duration - 10.0))
    for frac in (0.25, 0.5, 0.75):
        t = duration * frac
        if all(abs(t - s) > 10.0 for s in spans):
            spans.append(t)
    errors = []
    for t in spans:
        res = proc_run(["ffmpeg", "-hide_banner", "-v", "error", "-ss", f"{t:.3f}", "-i", str(path),
                        "-t", "10", "-f", "null", "-"], timeout=600, check=False)
        err = res.stderr.strip() or (f"ffmpeg exited with {res.returncode}" if res.returncode else "")
        if err:
            errors.append(err)
    return "; ".join(errors)


def _ran_out(session: MulticamSession, cam: Source, piece: "Piece", fps: float, ends: dict[str, float]) -> list[str]:
    """Warnings for files that stop before a piece does, so tpad holds their last frame."""
    out = []
    span = piece.frames / fps
    files = _picture_files(session, cam)
    if cam.members:
        files = [f for f in files if f.timeline_start() <= piece.tl_start < f.timeline_end()]
    for f in files:
        if f.path not in ends:
            ends[f.path] = _media_duration(Path(f.path), "v:0") or f.duration
        short = f.source_in(piece.tl_start, span) + span - ends[f.path]
        if short > 0.5 / fps:
            out.append(f"{os.path.basename(f.path)} ran out {short:.2f} s before the end of the shot at "
                       f"{piece.tl_start:.1f} s; its last frame is held.")
    return out


def _audio_inputs(session: MulticamSession) -> list[tuple[Source, int]]:
    feeds = [(s, ch) for s, ch, _ in session.person_mics() if s.synced]
    if feeds:
        return feeds
    rooms = [s for s in session.sources if s.role == "mic" and s.synced]
    if rooms:
        return [(max(rooms, key=lambda s: s.duration), -1)]
    # The reference is only chosen by the first sync, so a fresh edit has no mix yet.
    if not session.reference_id:
        return []
    return [(session.source(session.reference_id), -1)]


MIX_RATE = 48000
# asetrate only takes whole rates, so at 48 kHz the nearest one can be 10 ppm
# off the drift; labelling the audio 1000 times faster first lets one resample
# step land within 0.01 ppm, and the resample itself stays near 1:1.
DRIFT_LABEL_SCALE = 1000


def _drift_steps(speed: float) -> list[str]:
    """Filter steps stretching MIX_RATE audio by `speed` through a resample, keeping its length exact."""
    fast = MIX_RATE * DRIFT_LABEL_SCALE
    return [f"asetrate={fast}", f"aresample={round(fast * speed)}", f"asetrate={MIX_RATE}"]


def _aligned_input(s: Source, channel: int, start: float, duration: float) -> tuple[list[str], list[str]]:
    """ffmpeg input args and mono MIX_RATE filter steps that place a source on [start, start + duration]."""
    src_start = s.source_time(start)
    steps = [f"aresample={MIX_RATE}:async=1:first_pts=0"]
    if channel >= 0:
        steps.append(f"pan=mono|c0=c{channel}")
    elif s.audio_channels >= 2:
        steps.append("pan=mono|c0=0.5*c0+0.5*c1")
    else:
        steps.append("pan=mono|c0=c0")
    if abs(s.speed - 1.0) > 1e-7:
        steps += _drift_steps(s.speed)
    if src_start < 0:
        steps.append(f"adelay={round(-src_start * s.speed * MIX_RATE)}S")
    steps.append(f"apad,atrim=0:{duration:.6f}")
    return ["-ss", f"{max(0.0, src_start):.6f}", "-i", s.path], steps


def _write_mix(session: MulticamSession, out: Path, start: float, duration: float, *,
               per_feed: tuple[str, ...] = (), after: str = "anull", encode: tuple[str, ...],
               feeds: Optional[list[tuple[Source, int]]] = None) -> None:
    """Mix every mic (or the room, or just `feeds`) onto [start, start + duration] of the timeline."""
    args: list[str] = []
    chains: list[str] = []
    for i, (s, ch) in enumerate(feeds if feeds is not None else _audio_inputs(session)):
        inp, steps = _aligned_input(s, ch, start, duration)
        args += inp
        chains.append(f"[{i}:a:{s.audio_stream_index}]{','.join([*steps, *per_feed])}[a{i}]")
    n = len(chains)
    mix = "".join(f"[a{i}]" for i in range(n)) + f"amix=inputs={n}:normalize=0," if n > 1 else "[a0]"
    proc_run([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *args,
        "-filter_complex", ";".join(chains) + f";{mix}{after}[out]", "-map", "[out]", *encode, str(out),
    ], timeout=7200, check=True)


def _mix_key(session: MulticamSession) -> str:
    """Changes whenever which mics are mixed, or where they sit, changes."""
    feeds = [(s.id, s.audio_stream_index, ch, s.offset, s.speed, os.path.getmtime(s.path))
             for s, ch in _audio_inputs(session)]
    return hashlib.sha1(json.dumps(feeds, default=str).encode()).hexdigest()[:12]


# Rumble and handling noise sit under 70 Hz; a gentle high-pass takes them
# out before leveling would lift them along with the voice.
VOICE_CHAIN = ("highpass=f=70:poles=2", "dynaudnorm=f=250:g=15:p=0.9")


def _normalization(path: Path) -> tuple[float, bool]:
    """The linear gain (dB) that brings a mix to LOUDNESS_TARGET, and whether peaks then need limiting."""
    from services.audio_normalize import _parse_loudnorm_stats

    res = proc_run([
        "ffmpeg", "-hide_banner", "-nostats", "-i", str(path),
        "-af", f"loudnorm=I={LOUDNESS_TARGET}:TP={TRUE_PEAK_CEILING}:LRA=11:print_format=json", "-f", "null", "-",
    ], timeout=3600, check=False)
    stats = _parse_loudnorm_stats(res.stderr)
    if not stats:
        return 0.0, False
    gain = LOUDNESS_TARGET - float(stats["input_i"])
    return gain, float(stats["input_tp"]) + gain > TRUE_PEAK_CEILING


def _render_audio(session: MulticamSession, out: Path, start: float, duration: float,
                   splice: Optional[list[tuple[float, float]]] = None) -> float:
    """The episode mix: every mic high-passed and leveled, then one measured gain to LOUDNESS_TARGET.

    Returns that gain in dB so the stems can carry it too. Only peaks the gain
    would push past TRUE_PEAK_CEILING meet a limiter, and only on the mix.
    """
    premix = out.with_name(f"{out.stem}.premix.wav")
    # Float keeps a sum of hot mics from clipping before the gain brings it down.
    _write_mix(session, premix, start, duration, per_feed=VOICE_CHAIN, encode=("-c:a", "pcm_f32le"))
    if splice:
        # Measure loudness on what actually airs: a retake or a dead patch
        # removed from the episode shouldn't pull the target gain around,
        # whether it's silence (would ask for too much gain) or loud
        # cross-talk no one hears in the final cut (would ask for too little).
        kept = out.with_name(f"{out.stem}.premix-kept.wav")
        _splice_audio(premix, kept, splice, ["-c:a", "pcm_f32le"])
        premix.unlink(missing_ok=True)
        premix = kept
    gain, limit = _normalization(premix)
    steps = [f"volume={gain:.3f}dB"]
    if limit:
        # alimiter reads sample peaks; half a dB under the ceiling leaves room for the peaks between samples.
        ceiling = 10 ** ((TRUE_PEAK_CEILING - 0.5) / 20)
        steps.append(f"alimiter=limit={ceiling:.4f}:level=0:latency=1")
    proc_run([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(premix),
        "-af", ",".join([*steps, "aformat=channel_layouts=stereo"]), "-ar", str(MIX_RATE), "-c:a", "pcm_s16le", str(out),
    ], timeout=3600, check=True)
    premix.unlink(missing_ok=True)
    return gain


def _render_stems(
    session: MulticamSession, out_dir: Path, start: float, duration: float,
    splice: Optional[list[tuple[float, float]]] = None, gain: float = 0.0,
) -> list[str]:
    """One WAV per person, processed like the mix and carrying its gain, so the stems sum to it.

    A person recorded across several files (recorder splits) gets them mixed
    in. Float samples keep a stem the mix's limiter caught from clipping here.
    """
    stems = []
    for pid in session.person_ids():
        feeds = [(s, ch) for s, ch, who in session.person_mics() if who == pid and s.synced]
        if not feeds:
            continue
        args, chains = [], []
        for i, (s, ch) in enumerate(feeds):
            inp, steps = _aligned_input(s, ch, start, duration)
            args += inp
            chains.append(f"[{i}:a:{s.audio_stream_index}]{','.join([*steps, *VOICE_CHAIN])}[a{i}]")
        mix = "".join(f"[a{i}]" for i in range(len(feeds)))
        level = f"volume={gain:.3f}dB"
        graph = ";".join(chains) + (f";{mix}amix=inputs={len(feeds)}:normalize=0,{level}[out]" if len(feeds) > 1
                                    else f";[a0]{level}[out]")
        # Person ids are unique slugs, so two people never share a file name.
        out = out_dir / f"{pid}.wav"
        full = out.with_name(f"{pid}.full.wav") if splice else out
        proc_run([
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *args,
            "-filter_complex", graph, "-map", "[out]", "-ar", str(MIX_RATE), "-c:a", "pcm_f32le", str(full),
        ], timeout=3600, check=True)
        if splice:
            _splice_audio(full, out, splice, ["-c:a", "pcm_f32le"])
            full.unlink(missing_ok=True)
        stems.append(str(out))
    return stems


@dataclass
class Piece:
    source_id: Optional[str]  # None renders black: no camera covered this stretch
    out_frame: int  # first frame in the finished episode
    frames: int
    tl_start: float  # timeline second shown on that first frame
    removal: Optional[dict] = None  # set only in a review plan, where removed stretches stay in place


def _segments(session: MulticamSession) -> list[tuple[float, float, Optional[dict]]]:
    """The episode range in order, split at removals: (start, end, the removal or None)."""
    start, end = session.cuts[0]["start"], session.cuts[-1]["end"]
    out, cursor = [], start
    for r in session.removals:
        a, b = max(start, r["start"]), min(end, r["end"])
        if b <= cursor or a >= end:
            continue
        if a > cursor:
            out.append((cursor, a, None))
        out.append((max(a, cursor), b, r))
        cursor = b
    if cursor < end:
        out.append((cursor, end, None))
    return out


def kept_segments(session: MulticamSession) -> list[tuple[float, float]]:
    """The episode range minus the removed stretches, in timeline seconds."""
    return [(a, b) for a, b, r in _segments(session) if r is None]


def drift_parts(seconds: float, drift: float, fps: float) -> int:
    """How many pieces a stretch splits into so a clock off by `drift` (|speed - 1|) slips at most half a frame in each.

    One frame of slack covers pieces landing a frame longer on the output grid.
    """
    return max(1, math.ceil(drift * (seconds + 1 / fps) * 2 * fps - 1e-9))


def _camera_drift(session: MulticamSession, source_id: str) -> float:
    """|speed - 1| of the worst-drifting file a camera draws its picture from."""
    cam = next((s for s in session.sources if s.id == source_id), None)
    if cam is None:
        return 0.0
    return max((abs(f.speed - 1.0) for f in _picture_files(session, cam)), default=0.0)


def render_plan(session: MulticamSession, fps: float, *, review: bool = False) -> list[Piece]:
    """Every shot, split at removals, laid end to end on one output frame grid.

    One grid over the whole episode keeps piece lengths from accumulating
    rounding drift against the audio; the MP4, the stems, and both editor
    timelines are all built from this list so they agree frame for frame.
    A review plan keeps the removed stretches in place, tagged with their removal.
    A shot on a drifting camera is split into back-to-back pieces on that
    camera, each short enough that the clock slips at most half a frame across
    it; the cut list itself is unchanged.
    """
    pieces: list[Piece] = []
    out_t, out_f = 0.0, 0
    drift = {c["source_id"]: _camera_drift(session, c["source_id"]) for c in session.cuts}

    def emit(source_id: Optional[str], a: float, b: float, removal: Optional[dict]) -> None:
        nonlocal out_t, out_f
        out_end = out_t + (b - a)
        f_end = round(out_end * fps)
        if f_end > out_f:
            pieces.append(Piece(source_id, out_f, f_end - out_f, a + (out_f / fps - out_t), removal))
            out_f = f_end
        out_t = out_end

    for a, b, removal in _segments(session):
        if removal is not None and not review:
            continue
        t = a
        for c in session.cuts:
            if c["end"] <= t:
                continue
            if c["start"] >= b:
                break
            if c["start"] > t:
                emit(None, t, c["start"], removal)
                t = c["start"]
            e = min(c["end"], b)
            parts = drift_parts(e - t, drift[c["source_id"]], fps)
            for k in range(parts):
                emit(c["source_id"], t + (e - t) * k / parts, t + (e - t) * (k + 1) / parts, removal)
            t = e
        if t < b:
            emit(None, t, b, removal)
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
    files = [(s.id, s.offset, s.speed, s.channel_people, s.audio_stream_index, s.person, os.path.getmtime(s.path),
              _fingerprint(s.input_lut) if s.input_lut else None)
             for s in session.sources if s.id in used and os.path.exists(s.path)]
    # A tile or split screen takes its picture, and its LUT, from other files.
    files += [(f.id, _fingerprint(f.input_lut)) for c in session.cameras() if c.id in used and c.virtual
              for f in _picture_files(session, c) if f.input_lut]
    audio = [VOICE_CHAIN, LOUDNESS_TARGET, TRUE_PEAK_CEILING]
    blob = json.dumps([session.cuts, session.removals, session.look, stems, files, audio], sort_keys=True, default=str)
    return hashlib.sha1(blob.encode()).hexdigest()[:16]


def render_session(
    session: MulticamSession,
    *,
    stems: bool = True,
    validate: str = "sample",
    progress_callback: ProgressCallback = None,
) -> dict:
    if validate not in ("sample", "full"):
        raise ValueError("validate must be 'sample' or 'full'")
    if not session.cuts:
        raise ValueError("Plan the cuts before rendering")
    _check_luts_exist(session)
    key = _render_key(session, stems)
    video = session.outputs.get("video")
    # A validate="full" request is for checking an existing render, not for
    # getting a cached answer back unexamined, so it always re-earns its check.
    if validate != "full" and session.outputs.get("render_key") == key and video and os.path.exists(video):
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
        shots_dir = _work_dir(session.session_id) / "shots"
        warnings: list[str] = []
        video_ends: dict[str, float] = {}

        def run(item):
            i, piece = item
            cam = session.source(piece.source_id) if piece.source_id else None
            command = _shot_command(session, cam, piece.tl_start, piece.frames, width, height, fps, session.look)
            chunk = shots_dir / _shot_key(session, cam, command) / "shot.mp4"
            if not (chunk.exists() and _frame_count(chunk) == piece.frames):
                chunk.parent.mkdir(parents=True, exist_ok=True)
                tmp = chunk.with_name(f"shot.{i}.tmp.mp4")
                proc_run([*command, str(tmp)], timeout=600 if cam is None else 3600, check=True)
                got = _frame_count(tmp)
                if abs(got - piece.frames) > 1:
                    raise RuntimeError(f"Shot {i + 1} came out {got} frames long instead of {piece.frames}.")
                os.replace(tmp, chunk)
                if got != piece.frames:
                    with lock:
                        warnings.append(f"Shot {i + 1} came out {got} frames long instead of {piece.frames}.")
            held = _ran_out(session, cam, piece, fps, video_ends) if cam is not None else []
            with lock:
                warnings.extend(held)
                done[0] += piece.frames
                done[1] += 1
                _emit(progress_callback, 3 + 78 * done[0] / total_frames, f"Cut {done[1]} of {len(jobs)} shots")
            return chunk

        workers = max(1, min(4, (os.cpu_count() or 2) // 2))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            chunks = list(pool.map(run, enumerate(jobs)))

        _emit(progress_callback, 82, "Mixing microphones")
        audio = work / "audio.wav"
        gain = _render_audio(session, audio, start, end - start, splice)
        short = duration - _media_duration(audio)
        if short >= 1 / fps:
            warnings.append(f"The mixed audio is {short:.2f} s shorter than the picture; the end plays silent.")

        _emit(progress_callback, 88, "Joining shots")
        listing = work / "shots.txt"
        listing.write_text("".join(f"file '{c.as_posix()}'\n" for c in chunks), encoding="utf-8")
        partial = work / "episode.mp4"
        # Capped at the plan's length rather than -shortest, which would quietly
        # cut the picture to fit a short mix instead of letting the check above see it.
        proc_run([
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-f", "concat", "-safe", "0", "-i", str(listing), "-i", str(audio),
            "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
            "-t", f"{duration:.6f}", "-movflags", "+faststart", str(partial),
        ], timeout=3600, check=True)

        _emit(progress_callback, 91, "Checking the episode")
        validation = _validate(partial, total_frames, fps, warnings, full=validate == "full")

        stem_paths = []
        if stems:
            _emit(progress_callback, 96, "Writing separate mic tracks")
            # Built into `work`, not `out_dir`: if this fails, nothing at the
            # canonical output path changes, and the video built above is
            # discarded along with it instead of being published alone.
            stem_paths = _render_stems(session, work, start, end - start, splice, gain)

        # Every piece rendered and staged beside its final name, so publishing
        # is a run of same-volume renames that takes milliseconds. Paths stay
        # stable across renders; a crash inside that window can still pair a
        # new video with old stems, which the next render overwrites.
        video = out_dir / "episode.mp4"
        tmp_video = video.with_name(video.name + ".publishing")
        # shutil.move falls back to copy+delete when work and output sit on different volumes.
        shutil.move(str(partial), str(tmp_video))
        pending_stems = []
        for stem in stem_paths:
            dest = out_dir / Path(stem).name
            tmp_stem = dest.with_name(dest.name + ".publishing")
            shutil.move(stem, str(tmp_stem))
            pending_stems.append((tmp_stem, dest))

        os.replace(str(tmp_video), str(video))
        for tmp_stem, dest in pending_stems:
            os.replace(str(tmp_stem), str(dest))
        # A person dropped from the edit would otherwise keep last render's stem.
        current = {str(dest) for _, dest in pending_stems}
        for old in session.outputs.get("stems") or []:
            if old not in current and Path(old).parent == out_dir and os.path.exists(old):
                os.remove(old)

        session.outputs = {
            **session.outputs,
            "video": str(video),
            "stems": [str(dest) for _, dest in pending_stems],
            "duration": round(duration, 3),
            "render_key": key,
            "validation": validation,
        }
        session.save()
        # Shots this edit no longer uses would pile up across re-cuts; keep only this render's.
        used = {c.parent for c in chunks}
        for d in shots_dir.iterdir() if shots_dir.exists() else []:
            if d not in used:
                shutil.rmtree(d, ignore_errors=True)
        _emit(progress_callback, 100, "Episode ready" if not validation["warnings"]
              else f"Episode ready with {len(validation['warnings'])} warning(s)")
        return session.outputs
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _validate(video: Path, frames_expected: int, fps: float, warnings: list[str], *, full: bool = False) -> dict:
    """Check the muxed episode against its plan; raises on a broken file, warns on everything else.

    A decode error or a picture more than a frame off the plan means the file
    can't be trusted at all. Loudness off target or a held frame still makes
    a usable episode, so those are reported, not fatal.
    """
    errors = _decode_errors(video, full=full)
    if errors:
        raise RuntimeError(f"The rendered episode doesn't decode cleanly: {errors[:400]}")
    frames_actual = _frame_count(video)
    if abs(frames_actual - frames_expected) > 1:
        raise RuntimeError(f"The rendered episode has {frames_actual} frames; the cut needs {frames_expected}.")
    warnings = list(warnings)
    if frames_actual != frames_expected:
        warnings.append(f"The episode has {frames_actual} frames; the cut planned {frames_expected}.")
    duration = _media_duration(video)
    if abs(duration - frames_expected / fps) > 1.5 / fps:
        warnings.append(f"The episode runs {duration:.3f} s; the cut planned {frames_expected / fps:.3f} s.")
    lufs, true_peak = _loudness(video)
    if lufs is None:
        warnings.append("The episode's audio is silent.")
    elif abs(lufs - LOUDNESS_TARGET) > 1.0:
        warnings.append(f"Loudness is {lufs:.1f} LUFS; the target is {LOUDNESS_TARGET:.0f} LUFS.")
    if true_peak is not None and true_peak > TRUE_PEAK_CEILING + 0.5:
        warnings.append(f"True peak is {true_peak:.1f} dBTP, above the {TRUE_PEAK_CEILING} dBTP ceiling.")
    return {
        "frames_expected": frames_expected,
        "frames_actual": frames_actual,
        "duration": round(duration, 3),
        "lufs": lufs,
        "true_peak": true_peak,
        "warnings": warnings,
    }


def export_xml(session: MulticamSession, fmt: str, *, review: bool = False) -> str:
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
    tag = "-review" if review else ""
    if fmt == "premiere":
        out = out_dir / f"episode{tag}-premiere.xml"
        multicam_xml.write_xmeml(session, out, width=width, height=height, fps=fps, review=review)
    else:
        out = out_dir / f"episode{tag}.fcpxml"
        multicam_xml.write_fcpxml(session, out, width=width, height=height, fps=fps, review=review)
    handoff = _write_color_handoff(session, out_dir)
    session.outputs = {**session.outputs, f"{fmt}_review" if review else fmt: str(out), "color_handoff": str(handoff)}
    session.save()
    return str(out)


def _write_color_handoff(session: MulticamSession, out_dir: Path) -> Path:
    """Which LUT each camera file needs, beside the exported timeline.

    Neither xmeml nor FCPXML carries a LUT an editor applies reliably, so the
    colorist gets this list instead of a half-applied grade.
    """
    out = out_dir / "color_handoff.json"
    names = {p.id: p.name for p in session.people}
    cameras = [{
        "source_id": c.id,
        "file": c.path,
        "person": names.get(c.person, c.person),
        "input_lut": c.input_lut or None,
    } for c in session.cameras() if c.synced and not c.virtual]
    tmp = out.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({"session": session.name, "look": session.look, "cameras": cameras}, indent=2),
                   encoding="utf-8")
    os.replace(tmp, out)
    return out


MAX_TIMELINE_BYTES = 200 * 1024 * 1024


def _xml_fps(el) -> Optional[float]:
    rate = el.find("rate")
    if rate is None or not (rate.findtext("timebase") or "").strip():
        return None
    base = float(rate.findtext("timebase"))
    return base * 1000 / 1001 if (rate.findtext("ntsc") or "").strip().upper() == "TRUE" else base


def _xml_path(pathurl: str) -> str:
    from urllib.parse import unquote, urlparse

    url = urlparse(pathurl)
    path = unquote(url.path)
    if re.match(r"^/[A-Za-z]:/", path):
        path = path[1:]
    return os.path.normcase(os.path.realpath(path))


def _merged(cuts: list[dict]) -> list[dict]:
    out: list[dict] = []
    for c in cuts:
        if out and out[-1]["source_id"] == c["source_id"]:
            out[-1]["end"] = c["end"]
        else:
            out.append(dict(c))
    return out


def import_timeline(session: MulticamSession, path: str) -> dict:
    """Take the cut and removals from an edited FCP 7 XML timeline (Premiere, or Resolve's FCP 7 XML export).

    Picture decides the edit: at every frame the topmost enabled clip of one of
    this edit's cameras is on air, and episode time that no clip shows is
    removed. Clips of other files (b-roll, titles) and the audio tracks are
    left out, because podcli rebuilds the mics from the picture.
    """
    import xml.etree.ElementTree as ET

    file = Path(path).expanduser()
    if file.suffix.lower() != ".xml" or not file.is_file():
        raise ValueError("Pass an FCP 7 XML timeline (.xml). In Resolve: File > Export > Timeline > FCP 7 XML.")
    if file.stat().st_size > MAX_TIMELINE_BYTES:
        raise ValueError("That timeline file is too big to be one episode")
    try:
        root = ET.parse(file).getroot()
    except ET.ParseError as e:
        raise ValueError(f"{file.name} isn't valid XML: {e}") from None
    seq = root.find("sequence") if root.tag == "xmeml" else None
    if seq is None:
        seq = next(root.iter("sequence"), None)
    if root.tag != "xmeml" or seq is None or seq.find("media/video") is None:
        raise ValueError(f"{file.name} isn't an FCP 7 XML timeline with video")
    seq_fps = _xml_fps(seq)
    if not seq_fps:
        raise ValueError("The timeline has no frame rate")

    files = {}
    for f in root.iter("file"):
        if f.get("id") and f.findtext("pathurl"):
            files[f.get("id")] = _xml_path(f.findtext("pathurl"))
    cams = [c for c in session.cameras() if c.synced and not c.virtual]
    by_path = {os.path.normcase(os.path.realpath(c.path)): c for c in cams}
    by_name: dict[str, list[Source]] = {}
    for c in cams:
        by_name.setdefault(os.path.basename(c.path).lower(), []).append(c)

    def camera_of(path: str) -> Optional[Source]:
        if path in by_path:
            return by_path[path]
        same = by_name.get(os.path.basename(path).lower(), [])
        return same[0] if len(same) == 1 else None

    clips, skipped = [], 0
    for level, track in enumerate(seq.findall("media/video/track")):
        if (track.findtext("enabled") or "TRUE").strip().upper() == "FALSE":
            continue
        for item in track.findall("clipitem"):
            if (item.findtext("enabled") or "TRUE").strip().upper() == "FALSE":
                continue
            ref = item.find("file")
            cam = camera_of(files.get(ref.get("id"), "")) if ref is not None else None
            if cam is None:
                skipped += 1
                continue
            start, end = int(item.findtext("start", "-1")), int(item.findtext("end", "-1"))
            src_in, src_out = int(item.findtext("in", "-1")), int(item.findtext("out", "-1"))
            if start < 0 or end < 0:
                raise ValueError("A camera clip touches a transition. Remove the transitions and export again.")
            if end <= start:
                continue
            clip_fps = _xml_fps(item) or seq_fps
            if abs((src_out - src_in) / clip_fps - (end - start) / seq_fps) > 1.5 / seq_fps:
                raise ValueError(f"{os.path.basename(cam.path)} is sped up or slowed down at frame {start}. "
                                 "podcli keeps the conversation at real speed, so remove the speed change.")
            clips.append((start, end, level, cam, src_in / clip_fps))
    if not clips:
        raise ValueError("No clip on the timeline uses this edit's camera files")

    clips.sort(key=lambda c: c[0])
    edges = sorted({f for c in clips for f in (c[0], c[1])})
    shots: list[list] = []
    active: list[tuple] = []
    k = 0
    for a, b in zip(edges, edges[1:]):
        while k < len(clips) and clips[k][0] <= a:
            active.append(clips[k])
            k += 1
        active = [c for c in active if c[1] > a]
        if not active:
            continue
        start, _, _, cam, src_seconds = max(active, key=lambda c: c[2])
        tl_a = cam.timeline_start() + (src_seconds + (a - start) / seq_fps) * cam.speed
        tl_b = tl_a + (b - a) / seq_fps
        if shots and shots[-1][2] is cam and abs(shots[-1][1] - tl_a) < 1e-3:
            shots[-1][1] = tl_b
        else:
            shots.append([tl_a, tl_b, cam])

    tolerance = 1.5 / seq_fps
    for (_, prev_end, _), (start, _, _) in zip(shots, shots[1:]):
        if start < prev_end - tolerance:
            raise ValueError(f"The timeline plays {start:.1f}s of the conversation again or out of order. "
                             "podcli keeps it in order, so move that clip back or delete it.")

    reasons = [r for r in session.removals if r.get("reason")]
    removals = []
    for (_, end, _), (nxt, _, _) in zip(shots, shots[1:]):
        if nxt - end > tolerance:
            known = next((r for r in reasons if r["start"] < nxt and r["end"] > end), None)
            removals.append({"start": end, "end": nxt, **({"reason": known["reason"]} if known else {})})

    def covers(cam: Source, a: float, b: float) -> bool:
        return cam.timeline_start() <= a + 1e-3 and cam.timeline_end() >= b - 1e-3

    cuts: list[dict] = []
    for start, end, cam in shots:
        start, end = max(start, cam.timeline_start()), min(end, cam.timeline_end())
        if cuts and start > cuts[-1]["end"]:
            prev = cuts[-1]
            prev["end"] = min(start, session.source(prev["source_id"]).timeline_end())
            start = max(prev["end"], cam.timeline_start())
            if start > prev["end"] + 1e-3:
                cover = next((c for c in cams if covers(c, prev["end"], start)), None)
                if cover is None:
                    raise ValueError(f"No camera was recording at {prev['end']:.1f}s, where the timeline removes "
                                     "a stretch. Keep a little more around it.")
                cuts.append({"start": prev["end"], "end": start, "source_id": cover.id})
        elif cuts:
            start = cuts[-1]["end"]
        cuts.append({"start": start, "end": end, "source_id": cam.id})
    for i, c in enumerate(cuts):
        if c["end"] - c["start"] >= 0.1:
            continue
        for other in (cuts[i - 1] if i else None, cuts[i + 1] if i + 1 < len(cuts) else None):
            if other and covers(session.source(other["source_id"]), c["start"], c["end"]):
                c["source_id"] = other["source_id"]
                break

    session.removals = []
    set_cuts(session, _merged(cuts))
    set_removals(session, removals)
    return {"shots": len(session.cuts), "removals": len(session.removals), "skipped_clips": skipped}


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
