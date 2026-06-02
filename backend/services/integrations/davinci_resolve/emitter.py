"""Project IR -> FCPXML 1.10 for DaVinci Resolve 20.x.

Resolve quirks the emitter routes around:
  - FCPXML transform keyframes mis-translate values (center-origin math). Reframe
    is pre-baked into the V1 source layer instead of emitted as keyframes.
  - Composite-mode / blend-mode attributes are silently dropped. Alpha is carried
    by the asset itself (ProRes 4444); Resolve auto-detects and composites normal.
  - PNG image sequences import as one-frame-per-clip. Use single video files.
  - Avoid any Studio-only effect node — Resolve 20.2 watermarks the timeline
    if it sees one, even if unused.
  - Pin to FCPXML 1.10; 1.11+ have intermittent import failures.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from fractions import Fraction
from pathlib import Path

from .._shared import fcpxml as fx
from .._shared.timeline_ir import Project


def emit(project: Project, out_path: Path) -> Path:
    fmt_id = "r1"
    resources: list[ET.Element] = [
        fx.make_format(fmt_id, project.fps, project.width, project.height),
    ]
    compounds: list[tuple[str, str, Fraction]] = []

    next_asset = 2
    next_media = 1000

    for short in project.shorts:
        src_id = f"r{next_asset}"
        next_asset += 1
        resources.append(fx.make_asset(
            asset_id=src_id,
            name=f"{short.title} — source",
            media_path=short.source.path,
            frames=short.source.duration_frames,
            fps=short.source.fps,
            format_id=fmt_id,
            has_video=True,
            has_audio=short.source.has_audio,
            audio_channels=short.source.audio_channels,
        ))

        v2: tuple[str, Fraction] | None = None
        if short.captions:
            cid = f"r{next_asset}"
            next_asset += 1
            resources.append(fx.make_asset(
                asset_id=cid,
                name=f"{short.title} — captions",
                media_path=short.captions.path,
                frames=short.captions.duration_frames,
                fps=short.captions.fps,
                format_id=fmt_id,
                has_video=True,
                has_audio=False,
            ))
            v2 = (cid, fx.frames_to_seconds(short.captions.duration_frames, short.captions.fps))

        v3: tuple[str, Fraction] | None = None
        if short.logo:
            lid = f"r{next_asset}"
            next_asset += 1
            resources.append(fx.make_asset(
                asset_id=lid,
                name=f"{short.title} — logo",
                media_path=short.logo.path,
                frames=short.logo.duration_frames,
                fps=short.logo.fps,
                format_id=fmt_id,
                has_video=True,
                has_audio=False,
            ))
            v3 = (lid, fx.frames_to_seconds(short.logo.duration_frames, short.logo.fps))

        cmpd_id = f"r{next_media}"
        next_media += 1
        source_duration = fx.frames_to_seconds(short.source.duration_frames, short.source.fps)
        compounds.append((cmpd_id, short.title, source_duration))
        resources.append(fx.make_compound_media(
            media_id=cmpd_id,
            name=short.title,
            format_id=fmt_id,
            fps=project.fps,
            source_duration=source_duration,
            v1_asset_id=src_id,
            v1_has_audio=short.source.has_audio,
            v2=v2,
            v3=v3,
        ))

    library = fx.make_project_library(
        project_name=project.name,
        event_name="podcli",
        format_id=fmt_id,
        fps=project.fps,
        compounds=compounds,
    )

    fx.write_fcpxml(out_path, resources, library, version="1.10")
    return out_path
