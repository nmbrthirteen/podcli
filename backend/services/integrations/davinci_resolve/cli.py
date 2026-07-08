"""Spike CLI — emit a Resolve FCPXML referencing one or more shorts on disk.

Usage:
    python -m services.integrations.davinci_resolve.cli \
        --title "podcli spike" \
        --source path/to/short.mp4 \
        [--captions path/to/short_captions.mov] \
        [--out path/to/project.fcpxml]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "backend"))

from services.integrations.davinci_resolve import emitter
from utils.text import safe_filename
from services.integrations._shared.media_probe import probe_media
from services.integrations._shared.timeline_ir import (
    CaptionLayer,
    MediaClip,
    Project,
    Short,
)


def _build_short(title: str, source: Path, captions: Path | None) -> Short:
    src_info = probe_media(source)
    src_clip = MediaClip(
        path=source.resolve(),
        fps=src_info["fps"],
        duration_frames=src_info["duration_frames"],
        width=src_info["width"],
        height=src_info["height"],
        has_audio=src_info["has_audio"],
        audio_channels=src_info["audio_channels"],
    )
    cap_layer: CaptionLayer | None = None
    if captions:
        cap_info = probe_media(captions)
        cap_layer = CaptionLayer(
            path=captions.resolve(),
            fps=cap_info["fps"],
            duration_frames=cap_info["duration_frames"],
        )
    return Short(title=title, source=src_clip, captions=cap_layer)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--title", default="podcli spike")
    p.add_argument("--source", required=True, type=Path)
    p.add_argument("--captions", type=Path, default=None)
    p.add_argument("--out", type=Path, default=None)
    p.add_argument("--fps", type=float, default=None)
    p.add_argument("--width", type=int, default=None)
    p.add_argument("--height", type=int, default=None)
    args = p.parse_args()

    if not args.source.exists():
        print(f"error: source not found: {args.source}", file=sys.stderr)
        return 1
    if args.captions and not args.captions.exists():
        print(f"error: captions not found: {args.captions}", file=sys.stderr)
        return 1

    out = args.out or (
        REPO_ROOT / "data" / "export" / "davinci_resolve" / f"{safe_filename(args.title)}.fcpxml"
    )

    short = _build_short(args.title, args.source, args.captions)
    project = Project(
        name=args.title,
        fps=args.fps if args.fps is not None else short.source.fps,
        width=args.width if args.width is not None else short.source.width,
        height=args.height if args.height is not None else short.source.height,
        shorts=[short],
    )

    emitter.emit(project, out)

    print(f"wrote: {out}")
    print(f"  source:    {short.source.path}  ({short.source.duration_frames}f @ {short.source.fps:.3f}fps)")
    if short.captions:
        print(f"  captions:  {short.captions.path}  ({short.captions.duration_frames}f)")
    else:
        print("  captions:  (none — re-run with --captions for the layered spike)")
    print()
    print("To verify in DaVinci Resolve (free or Studio, 20.x):")
    print("  1. Open Resolve → New Project")
    print("  2. File → Import → Timeline → select the .fcpxml above")
    print("  3. Confirm one compound clip per short appears on the timeline")
    print("  4. Double-click a compound clip — V1 source and V2 captions should be on separate tracks")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
