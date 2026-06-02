"""FCPXML 1.10 primitives shared by FCPXML-consuming editors (Resolve, FCP, Premiere)."""
from __future__ import annotations

import urllib.parse
import xml.etree.ElementTree as ET
from fractions import Fraction
from pathlib import Path
from typing import Optional


_NTSC_FRACTIONS = {
    23.976: Fraction(24000, 1001),
    29.97: Fraction(30000, 1001),
    47.952: Fraction(48000, 1001),
    59.94: Fraction(60000, 1001),
    119.88: Fraction(120000, 1001),
}


def fps_fraction(fps: float) -> Fraction:
    # NTSC rates have exact rational forms FCPXML/Resolve key the timeline rate
    # off; limit_denominator(1000) yields non-canonical values (29.97 -> 2997/100)
    # that Resolve silently snaps or rejects on import.
    return _NTSC_FRACTIONS.get(round(fps, 3)) or Fraction(fps).limit_denominator(1000000)


def tc_format(fps: float) -> str:
    return "DF" if round(fps, 2) in (29.97, 59.94) else "NDF"


def frames_to_seconds(frames: int, fps: float) -> Fraction:
    return Fraction(frames) / fps_fraction(fps)


def seconds_to_time(seconds: Fraction) -> str:
    if seconds == 0:
        return "0s"
    if seconds.denominator == 1:
        return f"{seconds.numerator}s"
    return f"{seconds.numerator}/{seconds.denominator}s"


def rational_time(frames: int, fps: float) -> str:
    return seconds_to_time(frames_to_seconds(frames, fps))


def file_uri(p: Path) -> str:
    return "file://" + urllib.parse.quote(str(p.resolve()))


def make_format(format_id: str, fps: float, width: int, height: int) -> ET.Element:
    fps_frac = fps_fraction(fps)
    return ET.Element("format", {
        "id": format_id,
        "name": f"FFVideoFormat{width}x{height}p{int(round(fps))}",
        "frameDuration": f"{fps_frac.denominator}/{fps_frac.numerator}s",
        "width": str(width),
        "height": str(height),
        "colorSpace": "1-1-1 (Rec. 709)",
    })


def make_asset(
    *,
    asset_id: str,
    name: str,
    media_path: Path,
    frames: int,
    fps: float,
    format_id: str,
    has_video: bool = True,
    has_audio: bool = False,
    audio_channels: int = 0,
) -> ET.Element:
    attrs = {
        "id": asset_id,
        "name": name,
        "start": "0s",
        "duration": rational_time(frames, fps),
        "hasVideo": "1" if has_video else "0",
        "format": format_id,
    }
    if has_audio:
        attrs["hasAudio"] = "1"
        attrs["audioSources"] = "1"
        attrs["audioChannels"] = str(audio_channels or 2)
        attrs["audioRate"] = "48000"
    asset = ET.Element("asset", attrs)
    ET.SubElement(asset, "media-rep", {
        "kind": "original-media",
        "src": file_uri(media_path),
    })
    return asset


def make_compound_media(
    *,
    media_id: str,
    name: str,
    format_id: str,
    fps: float,
    source_duration: Fraction,
    v1_asset_id: str,
    v1_has_audio: bool,
    v2: Optional[tuple[str, Fraction]] = None,
    v3: Optional[tuple[str, Fraction]] = None,
) -> ET.Element:
    media = ET.Element("media", {"id": media_id, "name": name})
    seq = ET.SubElement(media, "sequence", {
        "format": format_id,
        "tcStart": "0s",
        "tcFormat": tc_format(fps),
        "duration": seconds_to_time(source_duration),
    })
    spine = ET.SubElement(seq, "spine")
    main_attrs = {
        "ref": v1_asset_id,
        "offset": "0s",
        "start": "0s",
        "duration": seconds_to_time(source_duration),
        "name": name,
        "format": format_id,
    }
    if v1_has_audio:
        main_attrs["audioRole"] = "dialogue"
    main_clip = ET.SubElement(spine, "asset-clip", main_attrs)

    for lane, overlay, label in (
        (1, v2, "captions"),
        (2, v3, "logo"),
    ):
        if overlay is None:
            continue
        overlay_id, overlay_duration = overlay
        clamped = min(overlay_duration, source_duration)
        ET.SubElement(main_clip, "video", {
            "ref": overlay_id,
            "lane": str(lane),
            "offset": "0s",
            "start": "0s",
            "duration": seconds_to_time(clamped),
            "name": f"{name} {label}",
        })
    return media


def make_project_library(
    *,
    project_name: str,
    event_name: str,
    format_id: str,
    fps: float,
    compounds: list[tuple[str, str, Fraction]],
) -> ET.Element:
    total = sum((d for _, _, d in compounds), Fraction(0))
    library = ET.Element("library")
    event = ET.SubElement(library, "event", {"name": event_name})
    project = ET.SubElement(event, "project", {"name": project_name})
    seq = ET.SubElement(project, "sequence", {
        "format": format_id,
        "tcStart": "0s",
        "tcFormat": tc_format(fps),
        "duration": seconds_to_time(total),
    })
    spine = ET.SubElement(seq, "spine")
    offset = Fraction(0)
    for media_id, name, duration in compounds:
        ET.SubElement(spine, "ref-clip", {
            "ref": media_id,
            "offset": seconds_to_time(offset),
            "start": "0s",
            "duration": seconds_to_time(duration),
            "name": name,
        })
        offset += duration
    return library


def write_fcpxml(
    out_path: Path,
    resources: list[ET.Element],
    library: ET.Element,
    version: str = "1.10",
) -> None:
    root = ET.Element("fcpxml", {"version": version})
    res_el = ET.SubElement(root, "resources")
    for r in resources:
        res_el.append(r)
    root.append(library)

    ET.indent(ET.ElementTree(root), space="  ")
    xml_str = ET.tostring(root, encoding="unicode")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        f'<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE fcpxml>\n{xml_str}\n',
        encoding="utf-8",
    )
