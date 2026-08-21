"""
Clip suggestion via AI CLI (Claude Code or Codex).

Delegates moment selection to an AI CLI, which uses the PodStack knowledge base
(.podcli/knowledge/) and CLAUDE.md for context-aware clip extraction.

Priority: Claude Code → Codex → heuristic fallback.
"""

import hashlib
import json
import math
import os
import re
import subprocess
import sys
from typing import Optional, Callable

from config.paths import paths
from services.audio_analyzer import compute_energy_scores
from services.audio_events import compute_event_scores, dominant_reaction

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from presets import DEFAULT_PRESET, MIN_CLIP_DURATION, MAX_CLIP_DURATION, TARGET_CLIP_DURATION_MIN, TARGET_CLIP_DURATION_MAX
from services.formats import get_format
from utils.text import clean_title
from services import ai_provider, podcli_cloud
from services.ai_cli import (
    _engine_label,
    _find_ai_cli,
    _find_ai_cli_candidates,
    _format_timeout_label,
    _run_ai_command,
    classify_cli_error,
    get_ai_cli_status,
)


def _load_existing_shorts(episodes_path: str) -> list[str]:
    """Titles of shorts already published, so they are not suggested again.

    Reads the shipped table shape and the older numbered-list shape. Cells left
    as `[placeholder]` are not titles.
    """
    if not os.path.exists(episodes_path):
        return []
    try:
        with open(episodes_path, encoding="utf-8") as f:
            content = f.read()
    except OSError:
        return []

    shorts: list[str] = []
    for raw in content.split("\n"):
        line = raw.strip()
        if not line:
            continue
        title = ""
        if line.startswith("|"):
            cells = [c.strip() for c in line.strip("|").split("|")]
            # | # | Moment | Title | Platform | Status |
            if len(cells) >= 3 and cells[0].lower() not in ("#", "num", "no"):
                if not all(set(c) <= set("-: ") for c in cells):
                    status = cells[4].lower() if len(cells) >= 5 else ""
                    # A dropped moment was considered and passed over, and the
                    # template says it can be reconsidered. Anything else that
                    # reached this table is a clip that exists.
                    if "drop" not in status:
                        title = cells[2]
        elif re.match(r"^\d+[.)]\s", line):
            title = re.split(r"\s[-\u2013\u2014]\s", line, maxsplit=1)[0]
            title = re.sub(r"^\d+[.)]\s*", "", title).strip()

        title = title.strip().strip("*`").strip()
        if title and not (title.startswith("[") and title.endswith("]")):
            shorts.append(title)
    return shorts


# (filename, max_chars) that reach the selection prompt, highest priority first.
# Shared with kb_signature so the suggestion cache and the prompt cannot drift.
class ClipBounds:
    """Duration bounds and prompt framing for one output format.

    formats.py calls itself the single source of truth for duration bounds and
    every consumer honoured that except the one deciding which moments get
    picked. Explicit overrides still win, so a Studio slider or a preset can
    narrow a format without redefining it.
    """

    __slots__ = ("dur_min", "dur_max", "target_min", "target_max", "editor", "pacing", "lens")

    def __init__(self, spec, dur_min=None, dur_max=None):
        self.dur_min = int(dur_min) if dur_min else spec.dur_min
        self.dur_max = int(dur_max) if dur_max else spec.dur_max
        if self.dur_max < self.dur_min:
            self.dur_min, self.dur_max = spec.dur_min, spec.dur_max
        self.target_min = max(spec.target_min, self.dur_min)
        self.target_max = min(spec.target_max, self.dur_max)
        if self.target_max < self.target_min:
            self.target_min, self.target_max = self.dur_min, self.dur_max
        self.editor = spec.editor
        self.pacing = spec.pacing
        self.lens = spec.lens

    @classmethod
    def of(cls, fmt=None, dur_min=None, dur_max=None) -> "ClipBounds":
        return cls(get_format(fmt), dur_min, dur_max)

    def keeps(self, seconds: float) -> bool:
        return self.dur_min <= seconds <= self.dur_max


KB_FILES = [
    ("04-shorts-creation-guide.md", 4000),   # moment selection criteria, content types
    ("05-title-formulas.md", 3000),          # title rules, shapes, banned openers
    ("02-voice-and-tone.md", 3000),          # banned words, voice fingerprint, coffee test
    ("01-brand-identity.md", 1500),          # show context, positioning, audience
    ("11-inspiration-channels.md", 2000),    # viral hook patterns, reference styles
    ("12-quick-reference.md", 2000),         # hook bank, title formulas, hashtags
    ("08-topics-themes.md", 1000),           # topic areas, audience interest map
    ("00-master-instructions.md", 1500),     # quality gate, auto-detection rules
]


def kb_signature() -> str:
    """Fingerprint of exactly what the knowledge base contributes to the prompt.

    Hashes the assembled text rather than file mtimes, so it survives a git
    checkout that rewrites timestamps without changing content, and it already
    accounts for per-file truncation and for files skipped as unfilled.
    """
    from services.knowledge_base import load_kb_context

    kb_dir = paths["knowledge"]
    parts = [load_kb_context(KB_FILES, kb_dir) or ""]
    parts.extend(_load_existing_shorts(os.path.join(kb_dir, "03-episodes-database.md")))
    return hashlib.sha256("\u0000".join(parts).encode("utf-8")).hexdigest()[:16]


CAPTION_STYLES = ("branded", "hormozi", "karaoke", "subtle")


def _preferred_caption_style(kb_dir: str) -> str:
    """The show's caption style, from the knowledge base or the preset default.

    04-shorts-creation-guide.md carries a "Caption style:" line that nothing
    read, so every suggestion arrived asking for hormozi regardless of what the
    show had written down or set as its preset.
    """
    path = os.path.join(kb_dir, "04-shorts-creation-guide.md")
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                m = re.match(r"^\s*[-*]?\s*caption style\s*:\s*(.+)$", line, re.I)
                if not m:
                    continue
                value = m.group(1).strip().strip("*`").lower()
                # Still the shipped "[branded / hormozi / karaoke / subtle]" list.
                if value.startswith("["):
                    break
                for style in CAPTION_STYLES:
                    if value == style:
                        return style
                break
    except OSError:
        pass
    return DEFAULT_PRESET.get("caption_style", "branded")


# Codex receives its prompt as an argv argument and silently truncates a long
# one. Truncating the tail drops the end of the episode, which is exactly how a
# run ends up with every clip in the opening minutes, so the transcript is
# sampled across the whole timeline instead.
CODEX_TRANSCRIPT_LIMIT = 48000


def _sample_transcript(transcript_text: str, limit: int = CODEX_TRANSCRIPT_LIMIT) -> str:
    """Thin a transcript to roughly `limit` characters, keeping its full span."""
    if len(transcript_text) <= limit:
        return transcript_text
    lines = transcript_text.split("\n")
    if len(lines) < 3:
        return transcript_text[:limit]
    keep_every = max(2, -(-len(transcript_text) // limit))
    sampled = lines[::keep_every]
    # The last line anchors the end of the timeline, which is the half a tail
    # truncation would have thrown away.
    if lines[-1] not in sampled[-1:]:
        sampled.append(lines[-1])
    return "\n".join(sampled)


def _codex_adapter(transcript_text: str):
    """An `adapt` callback that thins the transcript for Codex only."""
    def adapt(engine: str, prompt: str) -> str:
        if engine != "codex" or not transcript_text:
            return prompt
        sampled = _sample_transcript(transcript_text)
        if sampled == transcript_text:
            return prompt
        note = (
            "\n\nNOTE: this transcript is sampled across the full episode, so "
            "consecutive lines may skip ahead. Timestamps remain exact.\n"
        )
        return prompt.replace(transcript_text, note + sampled, 1)

    return adapt


def _total_score(clip: dict) -> float:
    """Sum the model's per-dimension scores, tolerating a malformed one.

    The values are whatever the model emitted. A single string in there used to
    raise TypeError inside the normalization loop and take down the whole
    suggestion run rather than costing one clip.
    """
    scores = clip.get("scores")
    if isinstance(scores, dict) and scores:
        total = 0.0
        usable = False
        for value in scores.values():
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                try:
                    value = float(value)
                except (TypeError, ValueError):
                    continue
            total += float(value)
            usable = True
        if usable:
            return total
    fallback = clip.get("total_score", 0)
    try:
        return float(fallback)
    except (TypeError, ValueError):
        return 0.0


MAX_REACTION_ANCHORS = 40


def _format_reaction_anchors(reaction_times: list[float] | None) -> str:
    """Prompt block listing detected laughter/reaction peaks as anchor moments."""
    if not reaction_times:
        return ""
    anchors = sorted({round(float(t), 1) for t in reaction_times})[:MAX_REACTION_ANCHORS]
    times = ", ".join(f"{t:.1f}s" for t in anchors)
    return (
        "\nAUDIENCE REACTION ANCHORS (detected laughter/cheering peaks, in seconds):\n"
        f"{times}\n"
        "The moment that CAUSED each reaction sits just before its anchor. "
        "Clips that contain or lead into an anchor are strong candidates — "
        "prefer them when quality is otherwise equal.\n"
    )


def _build_prompt(
    transcript_text: str,
    segment_count: int,
    duration_min: float,
    top_n: int,
    exclude_clips: list[dict] | None = None,
    reaction_times: list[float] | None = None,
    bounds: "ClipBounds | None" = None,
) -> str:
    """Build the prompt for Claude to extract clips.

    Inlines key rules from the knowledge base since Claude --print mode
    can't read project files.
    """

    # Load knowledge base files inline — prioritized by relevance to clip selection
    from services.knowledge_base import load_kb_context, warn_missing_context

    kb_dir = paths["knowledge"]
    kb_context = load_kb_context(KB_FILES, kb_dir)
    if not kb_context:
        warn_missing_context("clip scoring")

    # Load existing shorts from episode database for duplicate avoidance
    episodes_path = os.path.join(kb_dir, "03-episodes-database.md")
    existing_shorts = _load_existing_shorts(episodes_path)

    excluded_ranges = ""
    if exclude_clips:
        lines = []
        for clip in exclude_clips[:24]:
            start = round(float(clip.get("start_second", 0)), 1)
            end = round(float(clip.get("end_second", 0)), 1)
            title = str(clip.get("title", "Untitled")).strip()
            if end > start:
                lines.append(f"- {start:.1f}s to {end:.1f}s: {title[:80]}")
        if lines:
            excluded_ranges = (
                "\nALREADY SELECTED CLIPS (do NOT return overlapping moments with these):\n"
                + "\n".join(lines)
            )

    b = bounds or ClipBounds.of()
    return f"""You are {b.editor}. Find the {top_n} most scroll-stopping moments in this podcast transcript.

IMPORTANT: Return ONLY valid JSON. No markdown, no explanation, no code fences.

TIMESTAMP FORMAT: All timestamps in the transcript are in SECONDS (e.g., [123.4s]).
All timestamps you return MUST be in SECONDS as numbers (e.g., 123.4), NOT minutes:seconds.

DURATION RULES (CRITICAL):
- Target: {b.target_min}-{b.target_max} seconds (this is the sweet spot)
- Maximum: {b.dur_max} seconds (anything longer still renders, but gets flagged as over target)
- Minimum: {b.dur_min} seconds (too short = no payoff)
- {b.pacing}
- If a thought takes longer than {b.target_max}s, use segments to cut the filler in the middle

CUTTING RULES (CRITICAL):
- Cut TIGHT. Every second must earn its place.
- Start at the exact moment the hook hits — no preamble, no "so", no "well"
- End the MOMENT the point lands with a complete thought — don't trail off
- NEVER cut mid-sentence or mid-thought. The viewer must feel closure.
- The last sentence must feel like a natural ending, a punchline, or a mic-drop
- If there's filler/tangent in the middle, use multiple segments to skip it
- A tight clip with zero dead weight beats a {b.dur_max}s clip with fluff

MOMENT SELECTION ({b.lens}):
- Would YOU stop scrolling for this? If no, skip it.
- First 3 seconds must HOOK — a bold claim, shocking number, or provocative question
- Must make complete sense standalone — no "as I mentioned" or "going back to"
- Must end on a COMPLETE THOUGHT — sentence boundary, natural pause, or mic-drop moment
- Single focused idea — one concept, fully delivered, no loose threads
- Prioritize: controversial takes, surprising numbers, founder war stories, "wait what?" moments, emotional peaks
- Skip: generic advice, obvious statements, context-dependent references
- On long episodes, search the ENTIRE timeline and diversify the picks. Do not cluster all clips in one section if later sections contain strong standalone moments.

{f"KNOWLEDGE BASE:{kb_context}" if kb_context else ""}

{f"ALREADY PUBLISHED (do NOT suggest these moments again):{chr(10)}{chr(10).join('- ' + s for s in existing_shorts)}" if existing_shorts else ""}
{excluded_ranges}{_format_reaction_anchors(reaction_times)}

Score each moment on 4 dimensions (1-5 each):
- standalone: Makes sense without episode context?
- hook: Grabs attention in first 3 seconds?
- relevance: Matters to target audience?
- quotability: Memorable, shareable phrasing?

Classify each as: guest_story | technical_insight | market_landscape | business_strategy | hot_take

Return this exact JSON structure:
{{
  "clips": [
    {{
      "title": "First strong sentence from the moment",
      "start_second": 123.4,
      "end_second": 168.4,
      "segments": [
        {{"start": 123.4, "end": 140.0}},
        {{"start": 145.2, "end": 168.4}}
      ],
      "duration": 40,
      "content_type": "guest_story",
      "scores": {{"standalone": 4, "hook": 5, "relevance": 4, "quotability": 3}},
      "total_score": 16,
      "quote": "The key quote from this moment",
      "why": "One sentence on why this works as a short"
    }}
  ]
}}

SEGMENTS RULES:
- "segments" is an array of keep-ranges within the clip. Use it to CUT OUT dead weight.
- If the moment is clean with no filler, use a single segment: [{{"start": X, "end": Y}}]
- If there's a ramble/tangent/filler in the middle, split into multiple segments that skip it
- Each segment must start and end on sentence boundaries
- The rendered video will stitch these segments together seamlessly
- "duration" = total kept time (sum of all segment lengths), NOT end - start
- "start_second" / "end_second" = outer bounds (first segment start, last segment end)
- Example: speaker makes great point (10s), rambles (8s), delivers punchline (12s) → 2 segments, 22s total

Rules:
- Final clip duration (sum of segments) MUST be {b.dur_min}-{b.dur_max} seconds (target {b.target_min}-{b.target_max}s)
- Each segment must start and end on COMPLETE SENTENCES — never mid-thought
- The LAST segment must end on a sentence that feels like a natural conclusion
- Must make sense standalone when stitched together
- Sort clips by timestamp order

Transcript ({segment_count} segments, ~{duration_min:.0f} min):

{transcript_text}"""


def _split_prompt_for_cache(prompt: str, transcript_text: str) -> tuple[str, str]:
    """Separate the transcript from the ask, for backends that cache prefixes.

    Returns (stable_prefix, instruction). The transcript is the only large
    thing here and it is identical across every pass on an episode, so caching
    it turns four full reads into one read and three cache hits.
    """
    marker = f"\n\n{transcript_text}"
    if not transcript_text or not prompt.endswith(marker):
        return "", prompt
    return transcript_text, prompt[: -len(marker)]


def _build_transcript_text(segments: list[dict]) -> str:
    """Serialize transcript segments into the prompt-friendly text format."""
    lines = []
    for seg in segments:
        speaker = seg.get("speaker", "")
        speaker_label = f"[{speaker}] " if speaker else ""
        start = seg.get("start", 0)
        text = seg.get("text", "").strip()
        if text:
            lines.append(f"[{start:.1f}s] {speaker_label}{text}")
    return "\n".join(lines)


def _segments_duration_seconds(segments: list[dict]) -> float:
    """Estimate total covered duration from transcript segments."""
    if not segments:
        return 0.0
    return max(0.0, float(segments[-1].get("end", segments[-1].get("start", 0))) - float(segments[0].get("start", 0)))


def _should_bucket_initial_selection(segments: list[dict]) -> bool:
    """
    Use bucketed AI search for long or dense transcripts.

    This keeps initial clip discovery from spending many minutes on a single
    whole-episode prompt for long podcasts.
    """
    if not segments:
        return False

    duration_seconds = _segments_duration_seconds(segments)
    transcript_chars = sum(len(str(seg.get("text", ""))) for seg in segments)

    return bool(
        duration_seconds >= 45 * 60
        or len(segments) >= 180
        or transcript_chars >= 18000
    )


def _dedupe_clips_by_range(clips: list[dict]) -> list[dict]:
    """Collapse overlapping clip suggestions (>50% of the shorter clip), keeping
    the higher-scored one, sorted by start time. Exact-range matching would miss
    near-duplicates like 100.0-140.0 vs 102.5-141.5."""
    kept: list[dict] = []
    # Highest-scored first so the survivor of an overlap is the better clip.
    for clip in sorted(clips, key=lambda c: c.get("score", 0), reverse=True):
        start = float(clip.get("start_second", 0))
        end = float(clip.get("end_second", 0))
        dur = max(0.0, end - start)
        duplicate = False
        for k in kept:
            k_start = float(k.get("start_second", 0))
            k_end = float(k.get("end_second", 0))
            overlap = max(0.0, min(end, k_end) - max(start, k_start))
            shorter = min(dur, max(0.0, k_end - k_start)) or 1.0
            if overlap / shorter > 0.5:
                duplicate = True
                break
        if not duplicate:
            kept.append(clip)
    return sorted(kept, key=lambda c: c.get("start_second", 0))


def _drop_clips_overlapping(clips: list[dict], exclude_clips: list[dict]) -> list[dict]:
    """Drop clips that overlap an excluded range by >50% of the shorter clip.
    The prompt already asks the AI to skip these; this enforces it if it doesn't."""
    if not exclude_clips:
        return clips
    kept = []
    for clip in clips:
        start = float(clip.get("start_second", 0))
        end = float(clip.get("end_second", 0))
        dur = max(0.0, end - start)
        overlaps = False
        for ex in exclude_clips:
            ex_start = float(ex.get("start_second", 0))
            ex_end = float(ex.get("end_second", 0))
            overlap = max(0.0, min(end, ex_end) - max(start, ex_start))
            shorter = min(dur, max(0.0, ex_end - ex_start)) or 1.0
            if overlap / shorter > 0.5:
                overlaps = True
                break
        if not overlaps:
            kept.append(clip)
    return kept


def _select_top_by_score(clips: list[dict], top_n: int) -> list[dict]:
    """Keep the best `top_n` clips, then order them by start time.

    An explicit `rank` wins over the score, but only when every clip has one.
    """
    if len(clips) <= top_n:
        return sorted(clips, key=lambda c: c.get("start_second", 0))
    if clips and all(isinstance(c.get("rank"), int) for c in clips):
        ranked = sorted(clips, key=lambda c: c["rank"])[:top_n]
    else:
        ranked = sorted(clips, key=lambda c: c.get("score", 0), reverse=True)[:top_n]
    return sorted(ranked, key=lambda c: c.get("start_second", 0))


def find_moments_from_text(
    description: str,
    segments: list[dict],
    existing_clips: Optional[list[dict]] = None,
    progress_callback: Optional[Callable[[int, str], None]] = None,
    max_results: int = 3,
    bounds: ClipBounds | None = None,
) -> list[dict]:
    """Locate the moment(s) the user described/pasted in the transcript.
    Returns clip dicts (same shape as suggest_with_claude). Status goes to
    progress_callback; warnings to stderr — never stdout, which is the task
    runner's JSON-RPC channel."""
    existing_clips = existing_clips or []
    bounds = bounds or ClipBounds.of()
    if not ai_provider.available():
        print("No AI available for moment search", file=sys.stderr, flush=True)
        return []

    if progress_callback:
        progress_callback(15, "Reading transcript...")
    transcript_text = _build_transcript_text(segments)

    existing_desc = ""
    if existing_clips:
        existing_desc = "\n\nALREADY SELECTED (do not re-suggest these):\n"
        for c in existing_clips:
            existing_desc += f"- {c.get('start_second')}s-{c.get('end_second')}s: {c.get('title', '')}\n"

    upper = max(1, int(max_results))
    prompt = f"""Find the moment(s) the user is describing in this podcast transcript. Return ONLY valid JSON.

USER WANTS: "{description}"
{existing_desc}
RULES:
- Find the EXACT moment(s) matching what the user pasted/described
- The user may list several moments — return one clip per distinct moment they mention
- Return 1-{upper} matching moments (best match first)
- All timestamps in SECONDS as numbers
- Duration target: {bounds.target_min}-{bounds.target_max} seconds, max {bounds.dur_max} seconds
- Cut tight: start at the hook, end when the point lands
- Use segments to cut filler if needed

Return this JSON:
{{
  "clips": [
    {{
      "title": "First sentence of the moment",
      "start_second": 123.4,
      "end_second": 158.4,
      "segments": [{{"start": 123.4, "end": 158.4}}],
      "duration": 35,
      "content_type": "guest_story",
      "scores": {{"standalone": 4, "hook": 5, "relevance": 4, "quotability": 3}},
      "total_score": 16,
      "quote": "The key quote",
      "why": "Why this matches what the user asked for"
    }}
  ]
}}

Transcript:
{transcript_text}"""

    project_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")

    def announce(label: str) -> None:
        if progress_callback:
            progress_callback(40, f"Searching transcript with {label}...")

    caption_style = _preferred_caption_style(paths["knowledge"])
    try:
        data, _result = ai_provider.generate_json(
            prompt, timeout=900, project_dir=project_dir, on_attempt=announce,
            adapt=_codex_adapter(transcript_text),
        )
        if data:
            found = []
            for c in data.get("clips", []):
                total = _total_score(c)
                keep_segments = []
                for seg in c.get("segments", []):
                    s = round(float(seg.get("start", 0)), 1)
                    e = round(float(seg.get("end", 0)), 1)
                    if e > s:
                        keep_segments.append({"start": s, "end": e})

                start_sec = round(float(c.get("start_second", 0)), 1)
                end_sec = round(float(c.get("end_second", 0)), 1)
                if not keep_segments and end_sec > start_sec:
                    keep_segments = [{"start": start_sec, "end": end_sec}]

                kept_duration = sum(seg["end"] - seg["start"] for seg in keep_segments)
                if not bounds.keeps(kept_duration):
                    continue

                found.append({
                    "title": clean_title(c.get("title", "Untitled")),
                    "start_second": keep_segments[0]["start"] if keep_segments else start_sec,
                    "end_second": keep_segments[-1]["end"] if keep_segments else end_sec,
                    "segments": keep_segments,
                    "duration": round(kept_duration),
                    "score": total,
                    "content_type": c.get("content_type", "unknown"),
                    "reasoning": c.get("why", ""),
                    "preview_text": c.get("quote", "")[:120],
                    "suggested_caption_style": caption_style,
                    "quote": c.get("quote", ""),
                    "why": c.get("why", ""),
                    "reasons": [c.get("content_type", "")],
                    "preview": c.get("quote", "")[:120],
                })

            if found:
                return _dedupe_clips_by_range(found)

        return []

    except Exception as e:
        print(f"Moment search error: {e}", file=sys.stderr, flush=True)
        return []


def suggest_with_claude(
    segments: list[dict],
    top_n: int = 5,
    exclude_clips: list[dict] | None = None,
    progress_callback: Optional[Callable[[int, str], None]] = None,
    timeout: int = 900,
    error_sink: Optional[list[str]] = None,
    reaction_times: list[float] | None = None,
    bounds: ClipBounds | None = None,
) -> Optional[list[dict]]:
    """
    Use an AI CLI (Claude Code or Codex) to extract the best clip moments.

    Tries available AI CLIs in preference order and retries on runtime failure.
    Returns None if neither succeeds.
    """
    providers = ai_provider.status()["providers"]
    if not providers:
        return None
    bounds = bounds or ClipBounds.of()

    if progress_callback:
        progress_callback(0, f"Preparing transcript for {providers[0]['label']}...")

    transcript_text = _build_transcript_text(segments)

    # Estimate duration
    duration_min = 0
    if segments:
        duration_min = (segments[-1].get("end", 0) - segments[0].get("start", 0)) / 60

    prompt = _build_prompt(
        transcript_text,
        len(segments),
        duration_min,
        top_n,
        exclude_clips=exclude_clips,
        reaction_times=reaction_times,
        bounds=bounds,
    )

    project_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")

    def _parse_seconds(val) -> float:
        """Parse a timestamp value — handles both 123.4 and '2:03' formats."""
        if isinstance(val, (int, float)):
            return float(val)
        s = str(val).strip()
        if ":" in s:
            parts = s.split(":")
            try:
                return float(parts[0]) * 60 + float(parts[1])
            except (ValueError, IndexError):
                return 0.0
        try:
            return float(s)
        except ValueError:
            return 0.0

    attempted: list[str] = []
    current = {"label": ""}

    def announce(label: str) -> None:
        current["label"] = label
        if attempted and progress_callback:
            progress_callback(0, f"Retrying with {label}...")
        if progress_callback:
            progress_callback(20, f"Asking {label} to analyze transcript...")
        attempted.append(label)

    def usable(text: str):
        """Reject a response that parses but has nothing in it, so the next
        engine gets a turn rather than the user getting an empty result."""
        label = current["label"]
        if progress_callback:
            progress_callback(80, f"Parsing {label}'s suggestions...")
        parsed = ai_provider.extract_json(text)
        if not isinstance(parsed, dict):
            return f"{label} returned output that wasn't valid JSON"
        if not isinstance(parsed.get("clips"), list) or not parsed["clips"]:
            return f"{label} ran but found no clips in the transcript"
        return True

    cached_prefix, instruction = _split_prompt_for_cache(prompt, transcript_text)

    # What this channel's own published clips say about what works, plus the
    # house style learned from edits the team made to earlier output. Empty for
    # everyone else, so the free path is unchanged.
    learned = podcli_cloud.prompt_block()
    if learned:
        instruction = f"{learned}\n\n{instruction}"
        # local_prompt replaces the prompt outright, so local backends need it too.
        prompt = f"{learned}\n\n{prompt}"

    attempt = ai_provider.generate(
        instruction,
        timeout=timeout,
        project_dir=project_dir,
        on_attempt=announce,
        accept=usable,
        purpose="select_moments",
        stable_prefix=cached_prefix or None,
        # Local backends keep the prompt exactly as it has always been built;
        # only the cloud sees the split form.
        local_prompt=prompt,
        adapt=_codex_adapter(transcript_text),
    )

    if not attempt.ok:
        if progress_callback:
            progress_callback(0, attempt.error)
        if error_sink is not None:
            # Classified only for a CLI failure. The advice it adds is "run
            # `claude` once in a terminal", which is right for a local CLI and
            # wrong for a workspace session or an API key, and `classify_cli_error`
            # matches on "unauthorized" so it rewrote those too.
            # The CLI path tags the attempt with its engine name, not "cli",
            # so this asks the question the other way round.
            error_sink.append(
                attempt.error
                if attempt.provider in ("cloud", "api")
                else classify_cli_error(attempt.error)
            )
        return None

    label = attempt.label

    # Several independent searches over the same transcript find overlapping but
    # not identical moments. Keeping the union and re-ranking beats picking one
    # set, and the dedupe/scoring below already exists for exactly this shape.
    def records(payload: object) -> list[dict]:
        """
        The clip objects in a response, and only those.

        `usable` checked the primary response is a non-empty list, which still
        admits a null or a string inside it, and the alternates are not checked
        at all: `.get` on any of those is an AttributeError out of a code path
        with nothing above it to catch.
        """
        if not isinstance(payload, dict):
            return []
        found = payload.get("clips")
        if not isinstance(found, list):
            return []
        return [c for c in found if isinstance(c, dict)]

    clips = records(ai_provider.extract_json(attempt.text))
    for alternate in attempt.alternates:
        clips.extend(records(ai_provider.extract_json(alternate)))

    caption_style = _preferred_caption_style(paths["knowledge"])
    normalized = []
    for c in clips:
        total = _total_score(c)

        raw_segments = c.get("segments")
        raw_segments = raw_segments if isinstance(raw_segments, list) else []
        keep_segments = []
        for seg in raw_segments:
            if not isinstance(seg, dict):
                continue
            s = round(_parse_seconds(seg.get("start", 0)), 1)
            e = round(_parse_seconds(seg.get("end", 0)), 1)
            if e > s:
                keep_segments.append({"start": s, "end": e})

        start_sec = round(_parse_seconds(c.get("start_second", 0)), 1)
        end_sec = round(_parse_seconds(c.get("end_second", 0)), 1)

        if not keep_segments and end_sec > start_sec:
            keep_segments = [{"start": start_sec, "end": end_sec}]

        kept_duration = sum(seg["end"] - seg["start"] for seg in keep_segments)
        if not bounds.keeps(kept_duration):
            continue

        normalized.append({
            "title": clean_title(c.get("title", "Untitled")),
            "start_second": keep_segments[0]["start"] if keep_segments else start_sec,
            "end_second": keep_segments[-1]["end"] if keep_segments else end_sec,
            "segments": keep_segments,
            "duration": round(kept_duration),
            "score": total,
            "content_type": c.get("content_type", "unknown"),
            "reasoning": c.get("why", ""),
            "preview_text": c.get("quote", "")[:120],
            "suggested_caption_style": caption_style,
            "quote": c.get("quote", ""),
            "why": c.get("why", ""),
            "reasons": [c.get("content_type", "")],
            "preview": c.get("quote", "")[:120],
            "_ai_engine": attempt.provider,
        })

    # Dedupe within the pool as well as against already-selected clips: several
    # attempts routinely surface the same strong moment, and without this the
    # top N would be the same clip repeated.
    selected = _select_top_by_score(
        _drop_clips_overlapping(_dedupe_clips_by_range(normalized), exclude_clips or []),
        top_n,
    )

    if selected:
        if progress_callback:
            progress_callback(100, f"{label} suggested {len(selected)} clips")
        return selected

    if progress_callback:
        progress_callback(0, f"{label} returned no usable clips")
    if error_sink is not None:
        error_sink.append(
            f"{label} returned clips but none were usable (wrong length or format)"
        )
    return None


def suggest_initial_with_claude(
    segments: list[dict],
    top_n: int = 5,
    exclude_clips: list[dict] | None = None,
    progress_callback: Optional[Callable[[int, str], None]] = None,
    error_sink: Optional[list[str]] = None,
    reaction_times: list[float] | None = None,
    bounds: ClipBounds | None = None,
) -> Optional[list[dict]]:
    """
    Initial clip discovery entry point.

    For long podcasts, search transcript buckets first so the AI can return
    quickly and cover the full timeline instead of timing out on one giant
    prompt.
    """
    exclude_clips = exclude_clips or []
    if not _should_bucket_initial_selection(segments):
        return suggest_with_claude(
            segments=segments,
            top_n=top_n,
            exclude_clips=exclude_clips,
            progress_callback=progress_callback,
            error_sink=error_sink,
            timeout=180,
            reaction_times=reaction_times,
            bounds=bounds,
        )

    start_bound = float(segments[0].get("start", 0))
    end_bound = float(segments[-1].get("end", segments[-1].get("start", 0)))
    duration = max(0.0, end_bound - start_bound)
    if duration <= 0:
        return None

    bucket_count = min(6, max(3, math.ceil((duration / 60.0) / 25.0)))
    bucket_size = duration / bucket_count
    buckets = []
    for idx in range(bucket_count):
        bucket_start = start_bound + idx * bucket_size
        bucket_end = end_bound if idx == bucket_count - 1 else start_bound + (idx + 1) * bucket_size
        bucket_segments = _slice_segments_for_range(segments, bucket_start, bucket_end)
        if len(bucket_segments) < 3:
            continue
        buckets.append({
            "start": bucket_start,
            "end": bucket_end,
            "segments": bucket_segments,
        })

    if not buckets:
        return suggest_with_claude(
            segments=segments,
            top_n=top_n,
            exclude_clips=exclude_clips,
            progress_callback=progress_callback,
            error_sink=error_sink,
            timeout=180,
            reaction_times=reaction_times,
            bounds=bounds,
        )

    if progress_callback:
        progress_callback(0, f"Long episode detected — searching {len(buckets)} timeline buckets...")

    aggregated: list[dict] = []
    per_bucket_top_n = max(2, math.ceil(top_n / max(1, len(buckets))))

    for idx, bucket in enumerate(buckets):
        bucket_label = (
            f"bucket {idx + 1}/{len(buckets)} "
            f"[{int(bucket['start'] // 60)}:{int(bucket['start'] % 60):02d}-"
            f"{int(bucket['end'] // 60)}:{int(bucket['end'] % 60):02d}]"
        )
        if progress_callback:
            progress_callback(0, f"Searching {bucket_label}...")

        bucket_clips = suggest_with_claude(
            segments=bucket["segments"],
            top_n=per_bucket_top_n,
            exclude_clips=exclude_clips + aggregated,
            progress_callback=(
                None if progress_callback is None
                else lambda pct, msg, bucket_label=bucket_label: progress_callback(
                    pct,
                    f"{bucket_label}: {msg}" if msg else msg,
                )
            ),
            error_sink=error_sink,
            timeout=90,
            reaction_times=[
                t for t in (reaction_times or []) if bucket["start"] <= t <= bucket["end"]
            ] or None,
            bounds=bounds,
        )
        if not bucket_clips:
            continue

        aggregated.extend(bucket_clips)

    deduped = _dedupe_clips_by_range(aggregated)
    if len(deduped) >= top_n:
        return _select_top_by_score(deduped, top_n)

    fallback_clips = suggest_with_claude(
        segments=segments,
        top_n=top_n,
        exclude_clips=exclude_clips + deduped,
        progress_callback=(
            None if progress_callback is None
            else lambda pct, msg: progress_callback(
                pct,
                f"global pass: {msg}" if msg else msg,
            )
        ),
        error_sink=error_sink,
        timeout=120,
        reaction_times=reaction_times,
        bounds=bounds,
    )
    if fallback_clips:
        deduped = _dedupe_clips_by_range(deduped + fallback_clips)

    return _select_top_by_score(deduped, top_n) if deduped else None


REACTION_BLEND_WEIGHT = 0.2
ENERGY_BLEND_WEIGHT = 0.1


def blend_signal_scores(
    clips: list[dict],
    energy_data: list[dict] | None = None,
    events_data: list[dict] | None = None,
    reaction_weight: float = REACTION_BLEND_WEIGHT,
    energy_weight: float = ENERGY_BLEND_WEIGHT,
) -> list[dict]:
    """Blend per-clip reaction/energy peaks into AI-selected clip scores.

    The AI score (0-20) stays primary: the 0-10 signal scores are added at
    modest weight (max +3 combined), so laughter or an energy spike breaks
    ties rather than overruling editorial judgment. Mutates and returns
    `clips`, preserving their order.
    """
    if not clips or (not energy_data and not events_data):
        return clips

    ranges = [
        {"start": float(c.get("start_second", 0)), "end": float(c.get("end_second", 0))}
        for c in clips
    ]
    reaction_scores = compute_event_scores(events_data or [], ranges)
    energy_scores = compute_energy_scores(energy_data or [], ranges)

    for clip, rng, r_score, e_score in zip(clips, ranges, reaction_scores, energy_scores):
        boost = round(reaction_weight * r_score + energy_weight * e_score, 2)
        if boost <= 0:
            continue
        clip["signal_boost"] = boost
        clip["score"] = round(float(clip.get("score", 0)) + boost, 2)
        if r_score > 3:
            label = dominant_reaction(events_data or [], rng["start"], rng["end"]) or "reaction"
            if label not in (clip.get("reasons") or []):
                clip.setdefault("reasons", []).append(label)
    return clips


MIN_RANKABLE_SURPLUS = 2


def rank_clips_with_ai(
    clips: list[dict],
    top_n: int,
    progress_callback: Optional[Callable[[int, str], None]] = None,
    timeout: int = 45,
) -> list[dict]:
    """Compare a whole candidate pool in one transcript-free pass.

    Mutates and returns `clips`, writing a 1-based `rank` and leaving `score`
    untouched. Falls back to score ordering on any failure.
    """
    if len(clips) < top_n + MIN_RANKABLE_SURPLUS:
        return clips

    lines = []
    for idx, clip in enumerate(clips):
        boost = clip.get("signal_boost")
        lines.append(
            f'{idx}. "{clip.get("title", "Untitled")}" '
            f'[{clip.get("duration", 0)}s, {clip.get("content_type", "unknown")}, '
            f'score {clip.get("score", 0)}'
            + (f', audience reaction +{boost}' if boost else "")
            + "]\n"
            f'   quote: {str(clip.get("quote", ""))[:200]}\n'
            f'   why: {str(clip.get("why", ""))[:200]}'
        )

    learned = podcli_cloud.prompt_block()
    prompt = f"""You are choosing which {top_n} of these {len(clips)} podcast clips to publish.

IMPORTANT: Return ONLY valid JSON. No markdown, no explanation, no code fences.

Each candidate was found by a separate pass over one stretch of the episode, so
their scores are not comparable to each other. Rank them against each other now.

Rank on:
- Would a stranger stop scrolling for this? That is the whole job.
- Does it stand alone, with no missing setup?
- Does it end on a complete thought rather than trailing off?
- Variety across the set: {top_n} clips that make the same point is one clip.

{f"{learned}{chr(10)}{chr(10)}" if learned else ""}CANDIDATES:
{chr(10).join(lines)}

Return this exact JSON structure, best first, every candidate listed exactly once:
{{
  "ranked": [
    {{"id": 3, "why": "One sentence on why this outranks the rest"}},
    {{"id": 0, "why": "..."}}
  ]
}}"""

    label = {"value": "AI"}

    def announce(name: str) -> None:
        label["value"] = name
        if progress_callback:
            progress_callback(0, f"Ranking {len(clips)} candidates with {name}...")

    project_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
    payload, result = ai_provider.generate_json(
        prompt,
        timeout=timeout,
        project_dir=project_dir,
        on_attempt=announce,
        purpose="rank_moments",
    )

    def give_up(reason: str) -> list[dict]:
        if progress_callback:
            progress_callback(100, f"Ranking unavailable, using scores ({reason})")
        print(f"Clip ranking skipped: {reason}", file=sys.stderr, flush=True)
        return clips

    if not result.ok:
        return give_up(result.error or "ranking pass produced no response")
    if not isinstance(payload, dict) or not isinstance(payload.get("ranked"), list):
        return give_up(f"{label['value']} returned a ranking in an unreadable shape")

    order: list[int] = []
    for entry in payload["ranked"]:
        if not isinstance(entry, dict):
            continue
        idx = entry.get("id")
        if isinstance(idx, int) and 0 <= idx < len(clips) and idx not in order:
            order.append(idx)

    if len(order) < len(clips):
        return give_up(
            f"{label['value']} ranked {len(order)} of {len(clips)} candidates"
        )

    for position, idx in enumerate(order, start=1):
        clips[idx]["rank"] = position
    if progress_callback:
        progress_callback(100, f"{label['value']} ranked {len(clips)} candidates")
    return clips


def select_clips_with_signal_scores(
    clips: list[dict],
    top_n: int,
    energy_data: list[dict] | None = None,
    events_data: list[dict] | None = None,
    progress_callback: Optional[Callable[[int, str], None]] = None,
) -> list[dict]:
    """Blend the audio signals in, rank against each other, cut to `top_n`."""
    blended = blend_signal_scores(clips, energy_data=energy_data, events_data=events_data)
    ranked = rank_clips_with_ai(blended, top_n, progress_callback=progress_callback)
    return _select_top_by_score(ranked, top_n)


def _bucket_coverage_seconds(existing_clips: list[dict], start: float, end: float) -> float:
    """Total selected-clip overlap inside a time bucket."""
    covered = 0.0
    for clip in existing_clips:
        overlap_start = max(start, float(clip.get("start_second", 0)))
        overlap_end = min(end, float(clip.get("end_second", 0)))
        if overlap_end > overlap_start:
            covered += overlap_end - overlap_start
    return covered


def _slice_segments_for_range(segments: list[dict], start: float, end: float) -> list[dict]:
    """Return transcript segments that overlap a bucket range."""
    return [
        seg for seg in segments
        if float(seg.get("end", seg.get("start", 0))) > start
        and float(seg.get("start", 0)) < end
    ]


def suggest_more_with_claude(
    segments: list[dict],
    existing_clips: list[dict],
    top_n: int = 8,
    progress_callback: Optional[Callable[[int, str], None]] = None,
    bounds: ClipBounds | None = None,
) -> Optional[list[dict]]:
    """
    Ask the AI for more clips by searching under-covered time buckets first.

    This prevents the common failure mode where repeated whole-episode prompts
    keep returning the same few obvious moments.
    """
    if not segments:
        return None

    start_bound = float(segments[0].get("start", 0))
    end_bound = float(segments[-1].get("end", segments[-1].get("start", 0)))
    duration = max(0.0, end_bound - start_bound)
    if duration <= 0:
        return None

    bucket_count = min(6, max(3, math.ceil((duration / 60.0) / 25.0)))
    bucket_size = duration / bucket_count
    buckets = []
    for idx in range(bucket_count):
        bucket_start = start_bound + idx * bucket_size
        bucket_end = end_bound if idx == bucket_count - 1 else start_bound + (idx + 1) * bucket_size
        bucket_segments = _slice_segments_for_range(segments, bucket_start, bucket_end)
        if len(bucket_segments) < 3:
            continue
        coverage_seconds = _bucket_coverage_seconds(existing_clips, bucket_start, bucket_end)
        bucket_duration = max(1.0, bucket_end - bucket_start)
        buckets.append({
            "start": bucket_start,
            "end": bucket_end,
            "segments": bucket_segments,
            "coverage_ratio": coverage_seconds / bucket_duration,
            "coverage_seconds": coverage_seconds,
        })

    if not buckets:
        return suggest_with_claude(
            segments=segments,
            top_n=top_n,
            exclude_clips=existing_clips,
            progress_callback=progress_callback,
            bounds=bounds,
        )

    buckets.sort(key=lambda b: (b["coverage_ratio"], b["coverage_seconds"], b["start"]))
    target_bucket_count = min(len(buckets), max(2, min(4, math.ceil(top_n / 3.0))))
    selected_buckets = buckets[:target_bucket_count]
    remaining_buckets = buckets[target_bucket_count:]
    per_bucket_top_n = max(2, math.ceil(top_n / max(1, len(selected_buckets))))

    aggregated: list[dict] = []
    for idx, bucket in enumerate(selected_buckets):
        bucket_label = f"bucket {idx + 1}/{len(selected_buckets)} [{int(bucket['start'] // 60)}:{int(bucket['start'] % 60):02d}-{int(bucket['end'] // 60)}:{int(bucket['end'] % 60):02d}]"
        if progress_callback:
            progress_callback(0, f"Searching {bucket_label}...")

        bucket_clips = suggest_with_claude(
            segments=bucket["segments"],
            top_n=per_bucket_top_n,
            exclude_clips=existing_clips + aggregated,
            progress_callback=(
                None if progress_callback is None
                else lambda pct, msg, bucket_label=bucket_label: progress_callback(
                    pct,
                    f"{bucket_label}: {msg}" if msg else msg,
                )
            ),
            bounds=bounds,
        )
        if not bucket_clips:
            continue

        aggregated.extend(bucket_clips)
        if len(aggregated) >= top_n:
            break

    if len(aggregated) < top_n and remaining_buckets:
        for idx, bucket in enumerate(remaining_buckets, start=len(selected_buckets) + 1):
            bucket_label = f"bucket {idx}/{len(buckets)} [{int(bucket['start'] // 60)}:{int(bucket['start'] % 60):02d}-{int(bucket['end'] // 60)}:{int(bucket['end'] % 60):02d}]"
            if progress_callback:
                progress_callback(0, f"Searching {bucket_label}...")

            bucket_clips = suggest_with_claude(
                segments=bucket["segments"],
                top_n=max(1, min(per_bucket_top_n, top_n - len(aggregated))),
                exclude_clips=existing_clips + aggregated,
                progress_callback=(
                    None if progress_callback is None
                    else lambda pct, msg, bucket_label=bucket_label: progress_callback(
                        pct,
                        f"{bucket_label}: {msg}" if msg else msg,
                    )
                ),
                bounds=bounds,
            )
            if not bucket_clips:
                continue

            aggregated.extend(bucket_clips)
            if len(aggregated) >= top_n:
                break

    if len(aggregated) < max(2, min(top_n, 4)):
        fallback_clips = suggest_with_claude(
            segments=segments,
            top_n=top_n,
            exclude_clips=existing_clips + aggregated,
            progress_callback=(
                None if progress_callback is None
                else lambda pct, msg: progress_callback(
                    pct,
                    f"global pass: {msg}" if msg else msg,
                )
            ),
            bounds=bounds,
        )
        if fallback_clips:
            aggregated.extend(fallback_clips)

    deduped = _dedupe_clips_by_range(aggregated)
    return _select_top_by_score(deduped, top_n) if deduped else None
