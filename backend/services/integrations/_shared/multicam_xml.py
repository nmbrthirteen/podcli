"""Editor timelines for a planned multicam session.

write_xmeml emits FCP7 XML (xmeml v4), which Premiere Pro imports natively and
DaVinci Resolve also reads. write_fcpxml emits FCPXML 1.10 for Final Cut Pro and
Resolve. Both reference the original camera and mic files, so nothing is
re-encoded: V1 carries the camera cuts, and each person's mic sits on its own
audio track.

Both follow the same render plan as the MP4, so removed stretches drop out of
picture and mics at the same frames. Clock drift can't be expressed as a clip
property in either format, so a drifting mic is split at every shot and each
piece gets its own in-point, which keeps it within a millisecond of picture.
"""
from __future__ import annotations

import os
import xml.etree.ElementTree as ET
from fractions import Fraction
from pathlib import Path

from . import fcpxml as fx


def _pathurl(path: str) -> str:
    return "file://localhost" + Path(path).resolve().as_uri()[len("file://"):]


def _rate(parent: ET.Element, fps: float) -> None:
    rate = ET.SubElement(parent, "rate")
    ET.SubElement(rate, "timebase").text = str(int(round(fps)))
    ET.SubElement(rate, "ntsc").text = "TRUE" if abs(fps - round(fps)) > 0.001 else "FALSE"


def _text(parent: ET.Element, tag: str, value) -> ET.Element:
    el = ET.SubElement(parent, tag)
    el.text = str(value)
    return el


def _audio_pieces(source, plan, fps: float) -> list[tuple[int, int, float]]:
    """(output_frame_in, output_frame_out, source_seconds_at_in) for one mic across the episode.

    Follows the render plan, so removed stretches drop out of the mics exactly
    where they drop out of the picture. Pieces that continue each other on the
    timeline are joined; a drifting mic is left split at every shot, so each
    piece gets its own in-point and stays within a millisecond of picture.
    """
    pieces: list[list] = []
    joinable = abs(source.speed - 1.0) <= 1e-6
    last_end = None
    for p in plan:
        a = p.tl_start
        b = a + p.frames / fps
        lo, hi = max(a, source.timeline_start()), min(b, source.timeline_end())
        if hi <= lo:
            continue
        f0 = p.out_frame + round((lo - a) * fps)
        f1 = p.out_frame + round((hi - a) * fps)
        if f1 <= f0:
            continue
        if joinable and pieces and pieces[-1][1] == f0 and last_end is not None and abs(last_end - lo) < 1e-3:
            pieces[-1][1] = f1
        else:
            pieces.append([f0, f1, source.source_time(p.tl_start + (f0 - p.out_frame) / fps)])
        last_end = hi
    return [tuple(x) for x in pieces]


def _label(session, source, channel: int, person: str) -> str:
    names = {p.id: p.name for p in session.people}
    base = names.get(person, "Room")
    return f"{base} ({os.path.basename(source.path)}{'' if channel < 0 else f' ch{channel + 1}'})"


def _mic_tracks(session, *, split_stereo: bool = False) -> list[tuple[object, int, str]]:
    """(source, channel, person) per audio track; channel -1 is the whole file.

    xmeml addresses one channel per track, so a stereo room mic becomes an L
    and an R track there instead of losing its right side.
    """
    feeds = [(s, ch, pid) for s, ch, pid in session.person_mics() if s.synced]
    if not feeds:
        rooms = [s for s in session.sources if s.role == "mic" and s.synced]
        room = max(rooms, key=lambda s: s.duration) if rooms else session.source(session.reference_id)
        feeds = [(room, -1, "")]
    if not split_stereo:
        return feeds
    out = []
    for s, ch, pid in feeds:
        out += [(s, 0, pid), (s, 1, pid)] if ch < 0 and s.audio_channels >= 2 else [(s, max(ch, 0), pid)]
    return out


def write_xmeml(session, out_path: Path, *, width: int, height: int, fps: float) -> None:
    from services.multicam import render_plan

    plan = render_plan(session, fps)
    total = sum(p.frames for p in plan)

    root = ET.Element("xmeml", {"version": "4"})
    seq = ET.SubElement(root, "sequence", {"id": "sequence-1"})
    _text(seq, "name", session.name)
    _text(seq, "duration", total)
    _rate(seq, fps)
    media = ET.SubElement(seq, "media")
    video = ET.SubElement(media, "video")
    fmt = ET.SubElement(video, "format")
    chars = ET.SubElement(fmt, "samplecharacteristics")
    _rate(chars, fps)
    _text(chars, "width", width)
    _text(chars, "height", height)
    _text(chars, "pixelaspectratio", "square")
    vtrack = ET.SubElement(video, "track")

    declared: set[str] = set()
    counter = [0]

    def file_el(parent: ET.Element, source) -> None:
        fid = f"file-{source.id}"
        if fid in declared:
            ET.SubElement(parent, "file", {"id": fid})
            return
        declared.add(fid)
        f = ET.SubElement(parent, "file", {"id": fid})
        _text(f, "name", os.path.basename(source.path))
        _text(f, "pathurl", _pathurl(source.path))
        _rate(f, source.fps or fps)
        _text(f, "duration", round(source.duration * (source.fps or fps)))
        fm = ET.SubElement(f, "media")
        if source.kind == "video":
            v = ET.SubElement(fm, "video")
            sc = ET.SubElement(v, "samplecharacteristics")
            _rate(sc, source.fps or fps)
            _text(sc, "width", source.width)
            _text(sc, "height", source.height)
        if source.has_audio:
            a = ET.SubElement(fm, "audio")
            _text(a, "channelcount", max(1, source.audio_channels))

    def clipitem(track: ET.Element, source, f0: int, f1: int, src_seconds: float,
                 *, name: str, audio_channel: int | None = None) -> None:
        counter[0] += 1
        item = ET.SubElement(track, "clipitem", {"id": f"clipitem-{counter[0]}"})
        _text(item, "name", name)
        _text(item, "enabled", "TRUE")
        _text(item, "duration", round(source.duration * fps))
        _rate(item, fps)
        _text(item, "start", f0)
        _text(item, "end", f1)
        src_in = max(0, round(src_seconds * fps))
        _text(item, "in", src_in)
        _text(item, "out", src_in + (f1 - f0))
        file_el(item, source)
        if audio_channel is not None:
            st = ET.SubElement(item, "sourcetrack")
            _text(st, "mediatype", "audio")
            _text(st, "trackindex", max(1, audio_channel + 1))

    for p in plan:
        if not p.source_id:
            continue
        cam = session.source(p.source_id)
        clipitem(vtrack, cam, p.out_frame, p.out_frame + p.frames, cam.source_time(p.tl_start), name=os.path.basename(cam.path))

    audio = ET.SubElement(media, "audio")
    _text(audio, "numOutputChannels", 2)
    for source, channel, person in _mic_tracks(session, split_stereo=True):
        track = ET.SubElement(audio, "track")
        label = _label(session, source, channel, person)
        for f0, f1, src_seconds in _audio_pieces(source, plan, fps):
            clipitem(track, source, f0, f1, src_seconds, name=label, audio_channel=channel)
        _text(track, "enabled", "TRUE")

    ET.indent(ET.ElementTree(root), space="  ")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE xmeml>\n'
        + ET.tostring(root, encoding="unicode") + "\n",
        encoding="utf-8",
    )


def write_fcpxml(session, out_path: Path, *, width: int, height: int, fps: float) -> None:
    from services.multicam import render_plan

    plan = render_plan(session, fps)
    total = sum(p.frames for p in plan)

    def t(frame_count: int) -> str:
        return fx.rational_time(frame_count, fps)

    def src_t(source, seconds: float) -> str:
        # Asset times start at the file's own timecode. Video lands on its frame
        # grid; audio stays sample-accurate so two mics never sit a frame apart.
        seconds = max(0.0, seconds) + source.timecode
        if source.kind == "audio":
            return fx.seconds_to_time(Fraction(round(seconds * 48000), 48000))
        rate = source.fps or fps
        return fx.rational_time(round(seconds * rate), rate)

    fmt_id = "r1"
    resources = [fx.make_format(fmt_id, fps, width, height)]
    asset_ids: dict[str, str] = {}
    formats: dict[str, str] = {}
    used = {p.source_id for p in plan if p.source_id} | {s.id for s, _, _ in _mic_tracks(session)}
    for i, source in enumerate(s for s in session.sources if s.id in used):
        aid = f"a{i + 1}"
        asset_ids[source.id] = aid
        if source.kind == "video":
            key = f"{source.width}x{source.height}@{round(source.fps or fps, 3)}"
            if key not in formats:
                formats[key] = f"r{len(formats) + 2}"
                resources.append(fx.make_format(formats[key], source.fps or fps, source.width, source.height))
            resources.append(fx.make_asset(
                asset_id=aid,
                name=os.path.basename(source.path),
                media_path=Path(source.path),
                frames=round(source.duration * (source.fps or fps)),
                fps=source.fps or fps,
                format_id=formats[key],
                has_video=True,
                has_audio=source.has_audio,
                audio_channels=source.audio_channels,
                start=src_t(source, 0.0),
            ))
        else:
            asset = ET.Element("asset", {
                "id": aid,
                "name": os.path.basename(source.path),
                "start": src_t(source, 0.0),
                "duration": fx.seconds_to_time(Fraction(round(source.duration * 48000), 48000)),
                "hasVideo": "0",
                "hasAudio": "1",
                "audioSources": "1",
                "audioChannels": str(max(1, source.audio_channels)),
                "audioRate": "48000",
            })
            ET.SubElement(asset, "media-rep", {"kind": "original-media", "src": fx.file_uri(Path(source.path))})
            resources.append(asset)

    library = ET.Element("library")
    event = ET.SubElement(library, "event", {"name": session.name})
    project = ET.SubElement(event, "project", {"name": f"{session.name} multicam"})
    seq = ET.SubElement(project, "sequence", {
        "format": fmt_id, "tcStart": "0s", "tcFormat": fx.tc_format(fps), "duration": t(total),
    })
    spine = ET.SubElement(seq, "spine")
    # A full-length gap anchors everything: picture on lane 1, mics on negative
    # lanes, so every clip is positioned against the same zero.
    gap = ET.SubElement(spine, "gap", {"name": "Episode", "offset": "0s", "start": "0s", "duration": t(total)})

    for p in plan:
        if not p.source_id:
            continue
        cam = session.source(p.source_id)
        ET.SubElement(gap, "asset-clip", {
            "ref": asset_ids[p.source_id],
            "lane": "1",
            "offset": t(p.out_frame),
            "start": src_t(cam, cam.source_time(p.tl_start)),
            "duration": t(p.frames),
            "name": os.path.basename(cam.path),
            "srcEnable": "video",
        })

    for lane, (source, channel, person) in enumerate(_mic_tracks(session), start=1):
        for f0, f1, src_seconds in _audio_pieces(source, plan, fps):
            clip = ET.SubElement(gap, "asset-clip", {
                "ref": asset_ids[source.id],
                "lane": str(-lane),
                "offset": t(f0),
                "start": src_t(source, src_seconds),
                "duration": t(f1 - f0),
                "name": _label(session, source, channel, person),
                "audioRole": "dialogue",
                "srcEnable": "audio",
            })
            if channel >= 0:
                ET.SubElement(clip, "audio-channel-source", {"srcCh": str(channel + 1), "role": "dialogue"})

    fx.write_fcpxml(out_path, resources, library)
