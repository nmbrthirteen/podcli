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


def _build_prompt(transcript_text: str, segment_count: int, duration_min: float, top_n: int) -> str:
    """Build the prompt for Claude to extract clips.

    Inlines key rules from the knowledge base since Claude --print mode
    can't read project files.
    """

    # Try to load knowledge base files inline
    kb_context = ""
    kb_dir = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "..", ".podcli", "knowledge"
    )
    for fname in ["04-shorts-creation-guide.md", "05-title-formulas.md", "02-voice-and-tone.md"]:
        fpath = os.path.join(kb_dir, fname)
        if os.path.exists(fpath):
            try:
                with open(fpath) as f:
                    content = f.read()
                kb_context += f"\n--- {fname} ---\n{content[:2000]}\n"
            except Exception:
                pass

    return f"""You are the clip extraction engine for a podcast. Analyze this transcript and return the {top_n} best moments for YouTube Shorts.

IMPORTANT: Return ONLY valid JSON. No markdown, no explanation, no code fences.

TIMESTAMP FORMAT: All timestamps in the transcript are in SECONDS (e.g., [123.4s]).
All timestamps you return MUST be in SECONDS as numbers (e.g., 123.4), NOT minutes:seconds.

DURATION RULES (CRITICAL):
- Target: 30-45 seconds per clip (ideal for YouTube Shorts)
- Maximum: 60 seconds (hard limit — YouTube Shorts cuts off at 60s)
- Minimum: 20 seconds (too short = no value)
- If a moment runs longer than 50 seconds, find a tighter cut point

CUTTING RULES (CRITICAL):
- Cut TIGHT. Every second must earn its place.
- Start at the exact moment the hook hits — no preamble, no "so", no "well"
- End the INSTANT the point lands — don't let it trail off
- If the speaker rambles before making a point, start AFTER the ramble
- If there's filler/tangent in the middle, pick the tighter segment that preserves the core idea
- The transcript timestamps are your scalpel — use them precisely
- A 30s clip with zero dead weight beats a 50s clip with 20s of fluff

MOMENT SELECTION:
- Must start with a strong hook (first 3 seconds grab attention)
- Must make complete sense standalone — no "as I mentioned" or "going back to"
- Must end cleanly on a sentence boundary (not mid-thought)
- Single focused idea per clip — one concept, fully delivered
- Prioritize: surprising facts, bold claims, founder stories, counterintuitive insights, "aha" moments
- Variety across content types

{f"KNOWLEDGE BASE:{kb_context}" if kb_context else ""}

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
- Final clip duration (sum of segments) MUST be 20-60 seconds (target 30-45s)
- Each segment must start and end on sentence boundaries
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

            # Reject clips shorter than 15 seconds (Claude returned bad timestamps)
            if kept_duration < 15:
                continue

            normalized.append({
                "title": c.get("title", "Untitled")[:55],
                "start_second": keep_segments[0]["start"] if keep_segments else start_sec,
                "end_second": keep_segments[-1]["end"] if keep_segments else end_sec,
                "segments": keep_segments,
                "duration": round(kept_duration),
                "score": total,
                "content_type": c.get("content_type", "unknown"),
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
