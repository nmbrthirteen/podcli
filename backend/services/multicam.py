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

DEFAULT_CUT = {"min_shot": 2.0, "max_shot": 30.0, "wide_insert": 4.0}


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

    @property
    def synced(self) -> bool:
        return self.offset is not None

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
    cut_settings: dict = field(default_factory=lambda: dict(DEFAULT_CUT))
    cuts: list[dict] = field(default_factory=list)
    speaker_map: dict = field(default_factory=dict)
    activity_key: str = ""
    outputs: dict = field(default_factory=dict)
    skipped: list[str] = field(default_factory=list)

    def path(self) -> Path:
        return _sessions_dir() / f"{self.session_id}.json"

    def save(self) -> None:
        tmp = self.path().with_suffix(".json.tmp")
        tmp.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")
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
        """(source, channel or -1 for a mono mixdown, person) for each personal mic feed."""
        feeds = []
        people = set(self.person_ids())
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
        id=hashlib.sha1(os.path.abspath(path).encode()).hexdigest()[:10],
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
        if {s.path for s in session.sources} | set(session.skipped) == wanted:
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
    raw_people = [p.get("name", "") if isinstance(p, dict) else str(p or "") for p in (people or [])]
    person_names = [n.strip() for n in raw_people if n.strip()]

    existing = find_session(found)
    if existing:
        if person_names and person_names != [p.name for p in existing.people]:
            existing = rename_people(existing, person_names)
        _emit(progress_callback, 100, f"Reopened the edit for these {len(found)} files")
        return existing

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

    person_names = person_names or ["Host", "Guest"]
    session = MulticamSession(
        session_id=uuid.uuid4().hex[:12],
        name=name or (Path(folder).name if folder else Path(found[0]).stem),
        folder=os.path.abspath(folder) if folder else "",
        people=[Person(id=_person_id(n, i), name=n) for i, n in enumerate(person_names)],
        sources=sources,
        skipped=skipped,
    )
    guess_roles(session)
    session.save()
    _emit(progress_callback, 100, f"Found {len(sources)} sources")
    return session


def needs_sync(session: MulticamSession) -> bool:
    return any(s.role != "ignore" and not s.synced for s in session.sources)


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
        for i, entry in enumerate(params["people"] or []):
            name = str(entry.get("name") if isinstance(entry, dict) else entry).strip()
            if not name:
                continue
            pid = (entry.get("id") if isinstance(entry, dict) else None) or _person_id(name, i)
            if pid == "wide" or pid in {p.id for p in people}:
                pid = f"{pid}-{i + 1}"
            people.append(Person(id=pid, name=name))
        if not people:
            raise ValueError("Add at least one person")
        session.people = people
        valid = set(session.person_ids())
        for s in session.sources:
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
        if "offset" in edit and edit["offset"] is not None:
            s.offset = float(edit["offset"])
            s.sync = {**s.sync, "status": "manual"}
        if "nudge" in edit:
            if s.offset is None:
                raise ValueError(f"{os.path.basename(s.path)} isn't synced yet. Set its offset first.")
            s.offset = float(s.offset) + float(edit["nudge"])
            s.sync = {**s.sync, "status": "manual"}
        s.guessed = False

    if "range_start" in params:
        session.range_start = None if params["range_start"] is None else max(0.0, float(params["range_start"]))
    if "range_end" in params:
        session.range_end = None if params["range_end"] is None else float(params["range_end"])
    if "look" in params:
        if params["look"] not in LOOKS:
            raise ValueError(f"Unknown look {params['look']!r}. Use: {', '.join(LOOKS)}")
        session.look = params["look"]
    if "cut_settings" in params:
        merged = {**session.cut_settings, **(params["cut_settings"] or {})}
        session.cut_settings = {
            "min_shot": max(0.5, min(10.0, float(merged["min_shot"]))),
            "max_shot": max(0.0, min(600.0, float(merged["max_shot"]))),
            "wide_insert": max(1.0, min(15.0, float(merged["wide_insert"]))),
        }
    if "speaker_map" in params:
        session.speaker_map = {str(k): v for k, v in (params["speaker_map"] or {}).items() if v in valid}
    if _cut_inputs(session) != before:
        session.cuts = []
    session.save()
    return session


def _cut_inputs(session: MulticamSession) -> str:
    return json.dumps([
        [(s.id, s.role, s.person, s.channel_people, s.offset) for s in session.sources],
        session.person_ids(), session.range_start, session.range_end, session.cut_settings, session.speaker_map,
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
        # shift belongs to the window's middle, not its start.
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
    candidates = [s for s in session.sources if s.role != "ignore" and s.has_audio]
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

    for s in session.sources:
        if s.role != "ignore" and not s.has_audio and s not in manual:
            s.offset = None
            s.sync = {"status": "failed", "message": "This camera recorded no audio. Set its offset by hand."}

    synced = [s for s in session.sources if s.synced and s.role != "ignore"]
    shift = min(s.timeline_start() for s in synced)
    for s in synced:
        s.offset = round(float(s.offset) - shift, 4)
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


def speaker_labels(session: MulticamSession, progress_callback: ProgressCallback = None) -> np.ndarray:
    """Per-10ms timeline labels (person index, SILENT, BOTH), cached per mapping."""
    length = int(np.ceil(session.timeline_duration() / sig.FRAME_SECONDS))
    cache = _work_dir(session.session_id) / "activity.npy"
    key = _activity_key(session)
    if session.activity_key == key and cache.exists():
        labels = np.load(cache)
        if len(labels) == length:
            return labels

    feeds = [(s, ch, pid) for s, ch, pid in session.person_mics() if s.synced]
    if feeds:
        people = session.person_ids()
        per_person: dict[str, np.ndarray] = {}
        for i, (s, ch, pid) in enumerate(feeds):
            _emit(progress_callback, 10 + 70 * i / len(feeds), f"Listening to {os.path.basename(s.path)}")
            level = _timeline_levels(session, s, ch, length)
            per_person[pid] = np.maximum(per_person[pid], level) if pid in per_person else level
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
        range_start=start, range_end=end, **session.cut_settings,
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


def set_cut(session: MulticamSession, index: int, source_id: str) -> MulticamSession:
    """Swap one shot to another camera; neighbours on the same camera merge."""
    if not 0 <= index < len(session.cuts):
        raise IndexError(f"No shot {index + 1}")
    cam = session.source(source_id)
    shot = session.cuts[index]
    if cam.role != "camera" or not cam.synced:
        raise ValueError("Pick a synced camera")
    if cam.timeline_start() > shot["start"] + 1e-3 or cam.timeline_end() < shot["end"] - 1e-3:
        raise ValueError(f"{os.path.basename(cam.path)} wasn't recording for this whole shot")
    shot["source_id"] = source_id
    merged: list[dict] = []
    for c in session.cuts:
        if merged and merged[-1]["source_id"] == c["source_id"]:
            merged[-1]["end"] = c["end"]
        else:
            merged.append(dict(c))
    session.cuts = merged
    session.save()
    return session


# ---------------------------------------------------------------------------
# Previews
# ---------------------------------------------------------------------------

def _frame(source: Source, at: float, out: Path, look: str = "none", width: int = 480) -> Path:
    vf = ",".join(x for x in [LOOKS.get(look, ""), f"scale={width}:-2"] if x)
    proc_run([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-ss", f"{max(0.0, at):.3f}", "-i", source.path, "-frames:v", "1",
        "-vf", vf, "-q:v", "4", str(out),
    ], timeout=60, check=True)
    return out


def previews(session: MulticamSession, *, looks: bool = False, at: Optional[float] = None) -> dict:
    """One still per camera, and optionally one still per look from the first camera."""
    work = _work_dir(session.session_id)
    frames: dict = {"cameras": {}, "looks": {}}
    for s in session.sources:
        if s.kind != "video":
            continue
        t = s.duration * 0.3 if at is None or not s.synced else s.source_time(at)
        t = min(max(0.0, t), max(0.0, s.duration - 0.5))
        out = work / f"frame-{s.id}-{int(t * 10)}.jpg"
        if not out.exists():
            _frame(s, t, out)
        frames["cameras"][s.id] = str(out)
    if looks:
        cams = session.cameras() or [s for s in session.sources if s.kind == "video"]
        if cams:
            cam = next((c for c in cams if c.person == "wide"), cams[0])
            t = cam.duration * 0.3
            for name in LOOKS:
                out = work / f"look-{cam.id}-{name}.jpg"
                if not out.exists():
                    _frame(cam, t, out, look=name, width=640)
                frames["looks"][name] = str(out)
    return frames


# ---------------------------------------------------------------------------
# Render
# ---------------------------------------------------------------------------

def _output_format(session: MulticamSession) -> tuple[int, int, float]:
    cams = [s for s in session.cameras() if s.synced]
    width = max(s.width for s in cams)
    height = max(s.height for s in cams)
    rates = [_standard_fps(s.fps) for s in cams]
    return width - width % 2, height - height % 2, max(set(rates), key=rates.count)


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


def _render_shot(cam: Optional[Source], out: Path, tl_start: float, frames: int,
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
    vf = [
        f"scale={width}:{height}:force_original_aspect_ratio=decrease",
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2",
        "setsar=1",
        f"fps={fps:.6f}",
        # A camera that stops a few frames early would shorten the shot and slip
        # every later shot against the audio; holding its last frame prevents that.
        "tpad=stop_mode=clone:stop=-1",
    ]
    if LOOKS.get(look):
        vf.append(LOOKS[look])
    proc_run([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-ss", f"{max(0.0, cam.source_time(tl_start)):.4f}", "-i", cam.path,
        "-map", "0:v:0", "-vf", ",".join(vf), *common,
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


def _render_audio(session: MulticamSession, out: Path, start: float, duration: float) -> None:
    args: list[str] = []
    chains: list[str] = []
    for i, (s, ch) in enumerate(_audio_inputs(session)):
        inp, steps = _aligned_input(s, ch, start, duration)
        args += inp
        chains.append(f"[{i}:a:0]{','.join(steps + ['dynaudnorm=f=250:g=15:p=0.9'])}[a{i}]")
    n = len(chains)
    mix = "".join(f"[a{i}]" for i in range(n)) + f"amix=inputs={n}:normalize=0," if n > 1 else "[a0]"
    graph = ";".join(chains) + f";{mix}loudnorm=I=-16:TP=-1.5:LRA=11,aformat=channel_layouts=stereo[out]"
    proc_run([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *args,
        "-filter_complex", graph, "-map", "[out]", "-ar", "48000",
        "-c:a", "aac", "-b:a", "192k", str(out),
    ], timeout=7200, check=True)


def _render_stems(session: MulticamSession, out_dir: Path, start: float, duration: float) -> list[str]:
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
        proc_run([
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *args,
            "-filter_complex", graph, "-map", "[out]", "-ar", "48000", "-c:a", "pcm_s24le", str(out),
        ], timeout=3600, check=True)
        stems.append(str(out))
    return stems


def shot_frames(session: MulticamSession, fps: float) -> list[tuple[Optional[str], int, int]]:
    """(source_id or None for a gap, first frame, frame count) on one grid from the first cut.

    One grid over the whole episode keeps shot lengths from accumulating
    rounding drift against the audio.
    """
    start = session.cuts[0]["start"]
    out: list[tuple[Optional[str], int, int]] = []
    cursor = 0
    for shot in session.cuts:
        f0 = round((shot["start"] - start) * fps)
        f1 = round((shot["end"] - start) * fps)
        if f0 > cursor:
            out.append((None, cursor, f0 - cursor))
        if f1 > max(f0, cursor):
            out.append((shot["source_id"], max(f0, cursor), f1 - max(f0, cursor)))
            cursor = f1
    return out


def _render_key(session: MulticamSession, stems: bool) -> str:
    """Everything the MP4 depends on, so an unchanged edit isn't rendered twice."""
    used = {c["source_id"] for c in session.cuts} | {s.id for s, _ in _audio_inputs(session)}
    files = [(s.id, s.offset, s.speed, s.channel_people, s.person, os.path.getmtime(s.path))
             for s in session.sources if s.id in used and os.path.exists(s.path)]
    blob = json.dumps([session.cuts, session.look, stems, files], sort_keys=True, default=str)
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
        jobs = shot_frames(session, fps)
        total_frames = sum(n for _, _, n in jobs)
        duration = total_frames / fps
        done = [0, 0]
        lock = threading.Lock()

        def run(item):
            i, (source_id, f0, n) = item
            chunk = work / f"shot-{i:05d}.mp4"
            cam = session.source(source_id) if source_id else None
            _render_shot(cam, chunk, start + f0 / fps, n, width, height, fps, session.look)
            with lock:
                done[0] += n
                done[1] += 1
                _emit(progress_callback, 3 + 80 * done[0] / total_frames, f"Cut {done[1]} of {len(jobs)} shots")
            return chunk

        workers = max(1, min(4, (os.cpu_count() or 2) // 2))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            chunks = list(pool.map(run, enumerate(jobs)))

        _emit(progress_callback, 84, "Mixing microphones")
        audio = work / "audio.m4a"
        _render_audio(session, audio, start, duration)

        _emit(progress_callback, 92, "Joining shots")
        listing = work / "shots.txt"
        listing.write_text("".join(f"file '{c.as_posix()}'\n" for c in chunks), encoding="utf-8")
        partial = work / "episode.mp4"
        proc_run([
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-f", "concat", "-safe", "0", "-i", str(listing), "-i", str(audio),
            "-map", "0:v:0", "-map", "1:a:0", "-c", "copy", "-shortest",
            "-movflags", "+faststart", str(partial),
        ], timeout=3600, check=True)
        video = out_dir / "episode.mp4"
        # shutil.move falls back to copy+delete when work and output sit on different volumes.
        shutil.move(str(partial), str(video))

        stem_paths = []
        if stems:
            _emit(progress_callback, 96, "Writing separate mic tracks")
            stem_paths = _render_stems(session, out_dir, start, duration)

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
            "sources": len(s.sources),
            "cameras": len(s.cameras()),
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


def payload(session: MulticamSession) -> dict:
    data = asdict(session)
    data["timeline_duration"] = round(session.timeline_duration(), 3)
    data["stats"] = cut_stats(session)
    data["looks"] = list(LOOKS)
    data["has_person_mics"] = bool(session.person_mics())
    data["outputs"] = {
        k: v for k, v in session.outputs.items()
        if not isinstance(v, str) or not os.path.isabs(v) or os.path.exists(v)
    }
    for s in data["sources"]:
        s["name"] = os.path.basename(s["path"])
        s["timeline_start"] = s["offset"]
        s["timeline_end"] = None if s["offset"] is None else round(s["offset"] + s["duration"] * s["speed"], 3)
    return data
