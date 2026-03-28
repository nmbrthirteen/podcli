"""
Clip suggestion via Claude Code.

Delegates moment selection to Claude, which uses the PodStack knowledge base
(.podcli/knowledge/) and CLAUDE.md for context-aware clip extraction.

Falls back to heuristic scoring if Claude is unavailable.
"""

import json
import os
import subprocess
import sys
import tempfile
from typing import Optional, Callable


def _find_claude() -> Optional[str]:
    """Find the claude CLI binary."""
    # Check common locations
    for path in [
        os.path.expanduser("~/.local/bin/claude"),
        "/usr/local/bin/claude",
        "/opt/homebrew/bin/claude",
    ]:
        if os.path.exists(path):
            return path

    # Check PATH
    try:
        result = subprocess.run(["which", "claude"], capture_output=True, text=True, timeout=5)
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except Exception:
        pass

    return None


def _load_existing_shorts(episodes_path: str) -> list[str]:
    """Extract existing short titles from episode database to avoid duplicates."""
    if not os.path.exists(episodes_path):
        return []
    try:
        with open(episodes_path) as f:
            content = f.read()
        # Parse lines that look like shorts entries: "1. [title] — [category]"
        shorts = []
        for line in content.split("\n"):
            line = line.strip()
            if line and (line.startswith("1.") or line.startswith("2.") or
                         line.startswith("3.") or line.startswith("4.") or
                         line.startswith("5.") or line.startswith("6.") or
                         line.startswith("7.") or line.startswith("8.") or
                         line.startswith("9.")):
                # Extract title between brackets or after number
                title = line.split("—")[0].strip().lstrip("0123456789.").strip()
                if title and title != "[Short title]":
                    shorts.append(title)
        return shorts
    except Exception:
        return []


def _build_prompt(transcript_text: str, segment_count: int, duration_min: float, top_n: int) -> str:
    """Build the prompt for Claude to extract clips.

    Inlines key rules from the knowledge base since Claude --print mode
    can't read project files.
    """

    # Load knowledge base files inline — prioritized by relevance to clip selection
    kb_context = ""
    kb_dir = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "..", ".podcli", "knowledge"
    )
    # (filename, max_chars) — higher priority files get more budget
    _kb_files = [
        ("04-shorts-creation-guide.md", 4000),   # moment selection criteria, content types
        ("05-title-formulas.md", 3000),           # title rules, shapes, banned openers
        ("02-voice-and-tone.md", 3000),           # banned words, voice fingerprint, coffee test
        ("01-brand-identity.md", 1500),           # show context, positioning, audience
        ("11-inspiration-channels.md", 2000),     # viral hook patterns, reference styles
        ("12-quick-reference.md", 2000),          # hook bank, title formulas, hashtags
        ("08-topics-themes.md", 1000),            # topic areas, audience interest map
        ("00-master-instructions.md", 1500),      # quality gate, auto-detection rules
    ]
    for fname, max_chars in _kb_files:
        fpath = os.path.join(kb_dir, fname)
        if os.path.exists(fpath):
            try:
                with open(fpath) as f:
                    content = f.read().strip()
                # Skip template-only files (uncustomized placeholders)
                if content.count("[Your Show Name]") > 2 and len(content) < 500:
                    continue
                kb_context += f"\n--- {fname} ---\n{content[:max_chars]}\n"
            except Exception:
                pass

    # Load existing shorts from episode database for duplicate avoidance
    episodes_path = os.path.join(kb_dir, "03-episodes-database.md")
    existing_shorts = _load_existing_shorts(episodes_path)

    return f"""You are a viral clip editor for TikTok and YouTube Shorts. Find the {top_n} most scroll-stopping moments in this podcast transcript.

IMPORTANT: Return ONLY valid JSON. No markdown, no explanation, no code fences.

TIMESTAMP FORMAT: All timestamps in the transcript are in SECONDS (e.g., [123.4s]).
All timestamps you return MUST be in SECONDS as numbers (e.g., 123.4), NOT minutes:seconds.

DURATION RULES (CRITICAL):
- Target: 25-40 seconds (this is the viral sweet spot)
- Maximum: 50 seconds (absolute hard limit — anything longer loses viewers)
- Minimum: 15 seconds (too short = no payoff)
- SHORTER IS BETTER. A punchy 25s clip outperforms a 50s clip every time.
- If a thought takes longer than 40s, use segments to cut the filler in the middle

CUTTING RULES (CRITICAL):
- Cut TIGHT. Every second must earn its place.
- Start at the exact moment the hook hits — no preamble, no "so", no "well"
- End the MOMENT the point lands with a complete thought — don't trail off
- NEVER cut mid-sentence or mid-thought. The viewer must feel closure.
- The last sentence must feel like a natural ending, a punchline, or a mic-drop
- If there's filler/tangent in the middle, use multiple segments to skip it
- A 30s clip with zero dead weight beats a 50s clip with 20s of fluff

MOMENT SELECTION (think like a TikTok editor):
- Would YOU stop scrolling for this? If no, skip it.
- First 3 seconds must HOOK — a bold claim, shocking number, or provocative question
- Must make complete sense standalone — no "as I mentioned" or "going back to"
- Must end on a COMPLETE THOUGHT — sentence boundary, natural pause, or mic-drop moment
- Single focused idea — one concept, fully delivered, no loose threads
- Prioritize: controversial takes, surprising numbers, founder war stories, "wait what?" moments, emotional peaks
- Skip: generic advice, obvious statements, context-dependent references

{f"KNOWLEDGE BASE:{kb_context}" if kb_context else ""}

{f"EXISTING SHORTS (avoid duplicating these moments):{chr(10).join('- ' + s for s in existing_shorts)}" if existing_shorts else ""}

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
- Final clip duration (sum of segments) MUST be 15-50 seconds (target 25-40s)
- Each segment must start and end on COMPLETE SENTENCES — never mid-thought
- The LAST segment must end on a sentence that feels like a natural conclusion
- Must make sense standalone when stitched together
- Sort clips by timestamp order

Transcript ({segment_count} segments, ~{duration_min:.0f} min):

{transcript_text}"""


def suggest_with_claude(
    segments: list[dict],
    top_n: int = 5,
    progress_callback: Optional[Callable[[int, str], None]] = None,
) -> Optional[list[dict]]:
    """
    Use Claude Code to extract the best clip moments from transcript segments.

    Returns list of clip dicts or None if Claude is unavailable.
    """
    claude_path = _find_claude()
    if not claude_path:
        return None

    if progress_callback:
        progress_callback(0, "Preparing transcript for Claude...")

    # Build transcript text from segments
    lines = []
    for seg in segments:
        speaker = seg.get("speaker", "")
        speaker_label = f"[{speaker}] " if speaker else ""
        start = seg.get("start", 0)
        text = seg.get("text", "").strip()
        if text:
            # Use absolute seconds (not M:SS) so Claude returns seconds too
            lines.append(f"[{start:.1f}s] {speaker_label}{text}")

    transcript_text = "\n".join(lines)

    # Estimate duration
    duration_min = 0
    if segments:
        duration_min = (segments[-1].get("end", 0) - segments[0].get("start", 0)) / 60

    prompt = _build_prompt(transcript_text, len(segments), duration_min, top_n)

    # Write prompt to temp file to avoid shell escaping issues
    project_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")

    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, dir=project_dir) as f:
        f.write(prompt)
        prompt_file = f.name

    if progress_callback:
        progress_callback(20, "Asking Claude to analyze transcript...")

    try:
        # Pipe prompt via stdin — avoids shell escaping and arg size limits
        # Run from the podcli project dir so it picks up CLAUDE.md + knowledge base
        result = subprocess.run(
            f'cat "{prompt_file}" | "{claude_path}" --print -p -',
            capture_output=True,
            text=True,
            cwd=project_dir,
            timeout=300,  # 5 min max
            shell=True,
        )

        if result.returncode != 0:
            if progress_callback:
                progress_callback(0, f"Claude returned error: {result.stderr[:200]}")
            return None

        if progress_callback:
            progress_callback(80, "Parsing Claude's suggestions...")

        # Parse JSON from Claude's response
        response = result.stdout.strip()

        # Strip markdown code fences if present
        if "```" in response:
            import re
            fence_match = re.search(r"```(?:json)?\s*\n?(.*?)\n?\s*```", response, re.DOTALL)
            if fence_match:
                response = fence_match.group(1).strip()

        # Use raw_decode to extract the first complete JSON object
        # (safer than rfind which can grab a stray "}" after the JSON)
        json_start = response.find("{")
        if json_start >= 0:
            decoder = json.JSONDecoder()
            data, _ = decoder.raw_decode(response, json_start)
        else:
            data = json.loads(response)

        clips = data.get("clips", [])

        if not clips:
            return None

        # Normalize to the format the CLI expects
        normalized = []
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

        for c in clips:
            scores = c.get("scores", {})
            total = sum(scores.values()) if scores else c.get("total_score", 0)

            # Parse segments (multi-cut ranges) or fall back to single range
            raw_segments = c.get("segments", [])
            keep_segments = []
            for seg in raw_segments:
                s = round(_parse_seconds(seg.get("start", 0)), 1)
                e = round(_parse_seconds(seg.get("end", 0)), 1)
                if e > s:
                    keep_segments.append({"start": s, "end": e})

            start_sec = round(_parse_seconds(c.get("start_second", 0)), 1)
            end_sec = round(_parse_seconds(c.get("end_second", 0)), 1)

            # Fall back to single segment if none provided
            if not keep_segments and end_sec > start_sec:
                keep_segments = [{"start": start_sec, "end": end_sec}]

            # Compute actual kept duration
            kept_duration = sum(seg["end"] - seg["start"] for seg in keep_segments)

            # Reject clips outside the target duration range
            if kept_duration < 15 or kept_duration > 55:
                continue

            normalized.append({
                "title": c.get("title", "Untitled")[:55],
                "start_second": keep_segments[0]["start"] if keep_segments else start_sec,
                "end_second": keep_segments[-1]["end"] if keep_segments else end_sec,
                "segments": keep_segments,
                "duration": round(kept_duration),
                "score": total,
                "content_type": c.get("content_type", "unknown"),
                # MCP-aligned fields
                "reasoning": c.get("why", ""),
                "preview_text": c.get("quote", "")[:120],
                "suggested_caption_style": "hormozi",
                # Backwards compat
                "quote": c.get("quote", ""),
                "why": c.get("why", ""),
                "reasons": [c.get("content_type", "")],
                "preview": c.get("quote", "")[:120],
            })

        # Sort by timestamp
        normalized.sort(key=lambda x: x["start_second"])

        if progress_callback:
            progress_callback(100, f"Claude suggested {len(normalized)} clips")

        return normalized

    except json.JSONDecodeError as e:
        if progress_callback:
            progress_callback(0, f"Could not parse Claude's response as JSON: {e}")
        return None
    except subprocess.TimeoutExpired:
        if progress_callback:
            progress_callback(0, "Claude timed out (5 min limit)")
        return None
    except Exception as e:
        if progress_callback:
            progress_callback(0, f"Claude error: {e}")
        return None
    finally:
        # Clean up temp file
        try:
            os.unlink(prompt_file)
        except Exception:
            pass
