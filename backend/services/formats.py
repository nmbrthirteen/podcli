"""Output format specifications — the single source of truth for clip dimensions.

Every aspect-ratio decision (crop target, caption geometry, duration bounds,
which scoring profile applies) derives from a FormatSpec so the render pipeline
is parameterized on format instead of hardcoding 1080x1920 per call site.
"""

import sys
from dataclasses import dataclass


@dataclass(frozen=True)
class FormatSpec:
    name: str
    width: int
    height: int
    reframe: bool
    caption_profile: str
    dur_min: int
    dur_max: int
    target_min: int
    target_max: int
    # How the selection prompt should frame the job. Substituting only the
    # numbers into a shorts-shaped prompt produces a self-contradicting ask:
    # "SHORTER IS BETTER" next to a 90-240s target, and the model splits the
    # difference.
    editor: str
    pacing: str
    lens: str

    @property
    def dims(self) -> tuple[int, int]:
        return (self.width, self.height)

    @property
    def ratio(self) -> float:
        return self.width / self.height


FORMATS = {
    "vertical": FormatSpec(
        name="vertical",
        width=1080, height=1920,
        reframe=True,
        caption_profile="vertical",
        dur_min=20, dur_max=45,
        target_min=20, target_max=35,
        editor="a viral clip editor for TikTok and YouTube Shorts",
        pacing="SHORTER IS BETTER. A punchy 25s clip outperforms a 40s clip every time.",
        lens="think like a TikTok editor",
    ),
    "horizontal": FormatSpec(
        name="horizontal",
        width=1920, height=1080,
        reframe=False,
        caption_profile="lower_third",
        dur_min=60, dur_max=300,
        target_min=90, target_max=240,
        editor="an editor cutting standalone segments out of a long-form show",
        pacing=(
            "A complete arc beats a short one. Give the point room to land, "
            "then cut everything that does not serve it."
        ),
        lens="think like a long-form editor",
    ),
    "square": FormatSpec(
        name="square",
        width=1080, height=1080,
        reframe=True,
        caption_profile="center",
        dur_min=20, dur_max=45,
        target_min=20, target_max=35,
        editor="a viral clip editor for feeds where the frame is square",
        pacing="SHORTER IS BETTER. A punchy 25s clip outperforms a 40s clip every time.",
        lens="think like a social editor",
    ),
}

DEFAULT_FORMAT = "vertical"
REFERENCE_HEIGHT = 1920


def get_format(name: str | None) -> FormatSpec:
    if name is not None and name not in FORMATS:
        # Raw MCP/API callers bypass the CLI's choices= guard; warn so a typo'd
        # format doesn't silently render as vertical.
        print(f"[formats] unknown format {name!r}; using {DEFAULT_FORMAT}", file=sys.stderr)
    return FORMATS.get(name or DEFAULT_FORMAT, FORMATS[DEFAULT_FORMAT])


def export_dims(spec: FormatSpec, source_width: int, source_height: int) -> tuple[int, int]:
    hd_w, hd_h = spec.width, spec.height
    if source_width <= 0 or source_height <= 0:
        return hd_w, hd_h
    ratio = spec.ratio
    src_ratio = source_width / source_height
    if src_ratio > ratio:
        native_h = source_height
        native_w = int(source_height * ratio)
    else:
        native_w = source_width
        native_h = int(source_width / ratio)
    scale = max(native_h / hd_h, native_w / hd_w, 1.0)
    scale = min(scale, 2.0)
    out_w = int(round(hd_w * scale))
    out_h = int(round(hd_h * scale))
    return out_w - out_w % 2, out_h - out_h % 2


def overlay_px(value: float, video_height: int) -> int:
    if video_height <= 0:
        return max(1, int(round(value)))
    return max(1, int(round(value * (video_height / REFERENCE_HEIGHT))))
