"""Timeline IR consumed by editor exporters."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class MediaClip:
    path: Path
    fps: float
    duration_frames: int
    width: int
    height: int
    has_audio: bool = False
    audio_channels: int = 0


@dataclass
class CaptionLayer:
    path: Path
    fps: float
    duration_frames: int


@dataclass
class Marker:
    time_seconds: float
    name: str
    note: str = ""
    color: str = "blue"


@dataclass
class Short:
    title: str
    source: MediaClip
    captions: Optional[CaptionLayer] = None
    logo: Optional[CaptionLayer] = None
    markers: list[Marker] = field(default_factory=list)


@dataclass
class Project:
    name: str
    fps: float = 30.0
    width: int = 1080
    height: int = 1920
    shorts: list[Short] = field(default_factory=list)
