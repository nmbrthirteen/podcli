"""Content profiles — which signals drive moment detection, per content type.

A ContentProfile is orthogonal to FormatSpec: FormatSpec is a render-time aspect-ratio
decision, a ContentProfile is a detection-time decision about which signal channels
matter and how candidate moments are generated. A podcast is selected transcript-first
(the LLM reads dialogue); a party video has no useful transcript and is driven by
laughter/energy peaks. Both are the same fusion engine with different channel weights.

Weights are starting points, tuned with real footage. `candidate_source` picks how
moments are generated: "llm" keeps the existing transcript-first selector (podcast),
"saliency" peak-picks a fused signal curve (party/action, works with no transcript).
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class ContentProfile:
    name: str
    candidate_source: str  # "llm" | "saliency"
    channel_weights: dict[str, float]
    # Reaction-based expansion: anchor on a laugh/cheer onset and extend backwards.
    reaction_lookback_sec: float
    reaction_payoff_sec: float
    # Peak-pick tuning for the saliency candidate source (per-video normalized units).
    peak_min_gap_sec: float
    peak_top_percentile: float


_CHANNELS = ("transcript_semantic", "prosody", "audio_event", "energy", "motion", "face_reaction")


def _weights(**kw) -> dict[str, float]:
    return {ch: float(kw.get(ch, 0.0)) for ch in _CHANNELS}


PROFILES = {
    "podcast": ContentProfile(
        name="podcast",
        candidate_source="llm",
        channel_weights=_weights(
            transcript_semantic=0.5, prosody=0.2, audio_event=0.15, energy=0.1, face_reaction=0.05
        ),
        reaction_lookback_sec=8.0,
        reaction_payoff_sec=2.0,
        peak_min_gap_sec=15.0,
        peak_top_percentile=0.15,
    ),
    "party": ContentProfile(
        name="party",
        candidate_source="saliency",
        channel_weights=_weights(
            audio_event=0.4, prosody=0.2, energy=0.2, motion=0.1, face_reaction=0.1
        ),
        reaction_lookback_sec=8.0,
        reaction_payoff_sec=2.0,
        peak_min_gap_sec=8.0,
        peak_top_percentile=0.15,
    ),
    "action": ContentProfile(
        name="action",
        candidate_source="saliency",
        channel_weights=_weights(
            audio_event=0.3, motion=0.2, prosody=0.2, energy=0.2, transcript_semantic=0.1
        ),
        reaction_lookback_sec=6.0,
        reaction_payoff_sec=2.0,
        peak_min_gap_sec=8.0,
        peak_top_percentile=0.15,
    ),
}

DEFAULT_PROFILE = "podcast"


def get_profile(name: str | None) -> ContentProfile:
    import sys
    if name is not None and name not in PROFILES:
        print(f"[profiles] unknown profile {name!r}; using {DEFAULT_PROFILE}", file=sys.stderr)
    return PROFILES.get(name or DEFAULT_PROFILE, PROFILES[DEFAULT_PROFILE])
