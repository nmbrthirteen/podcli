"""Output format specifications — the single source of truth for clip dimensions.

Every aspect-ratio decision (crop target, caption geometry, duration bounds,
which scoring profile applies) derives from a FormatSpec so the render pipeline
is parameterized on format instead of hardcoding 1080x1920 per call site.
"""

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
    score_key: str

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
        score_key="vertical_score",
    ),
    "horizontal": FormatSpec(
        name="horizontal",
        width=1920, height=1080,
        reframe=False,
        caption_profile="lower_third",
        dur_min=60, dur_max=300,
        target_min=90, target_max=240,
        score_key="horizontal_score",
    ),
    "square": FormatSpec(
        name="square",
        width=1080, height=1080,
        reframe=True,
        caption_profile="center",
        dur_min=20, dur_max=45,
        target_min=20, target_max=35,
        score_key="vertical_score",
    ),
}

DEFAULT_FORMAT = "vertical"


def get_format(name: str | None) -> FormatSpec:
    return FORMATS.get(name or DEFAULT_FORMAT, FORMATS[DEFAULT_FORMAT])
