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


def get_format(name: str | None) -> FormatSpec:
    if name is not None and name not in FORMATS:
        # Raw MCP/API callers bypass the CLI's choices= guard; warn so a typo'd
        # format doesn't silently render as vertical.
        print(f"[formats] unknown format {name!r}; using {DEFAULT_FORMAT}", file=sys.stderr)
    return FORMATS.get(name or DEFAULT_FORMAT, FORMATS[DEFAULT_FORMAT])
