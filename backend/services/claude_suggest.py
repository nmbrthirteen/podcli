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
    """Build the prompt for Claude to extract clips."""
    return f"""You are the clip extraction engine for a podcast. Analyze this transcript and return the {top_n} best moments for YouTube Shorts.

IMPORTANT: Return ONLY valid JSON. No markdown, no explanation, no code fences.

Use the knowledge base in .podcli/knowledge/ for:
- Voice and tone rules (02-voice-and-tone.md)
- Moment selection criteria (04-shorts-creation-guide.md)
- Content type classification (05-title-formulas.md)
- Existing episodes to avoid duplicates (03-episodes-database.md)

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
      "end_second": 178.9,
      "duration": 55,
      "content_type": "guest_story",
      "scores": {{"standalone": 4, "hook": 5, "relevance": 4, "quotability": 3}},
      "total_score": 16,
      "quote": "The key quote from this moment",
      "why": "One sentence on why this works as a short"
    }}
  ]
}}

Rules:
- Clips must be 20-90 seconds
- Must start and end on sentence boundaries
- Must make sense standalone (no "as I mentioned earlier")
- Prioritize: surprising facts, bold claims, founder stories, counterintuitive insights
- Variety across content types
- Sort by timestamp order

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
        mins = int(start) // 60
        secs = int(start) % 60
        text = seg.get("text", "").strip()
        if text:
            lines.append(f"({mins}:{secs:02d}) {speaker_label}{text}")

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
        # Call claude with --print (non-interactive, outputs result directly)
        # Run from the podcli project dir so it picks up CLAUDE.md + knowledge base
        result = subprocess.run(
            [claude_path, "--print", "-p", f"$(cat {prompt_file})"],
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

        # Try to find JSON in the response (Claude might add text around it)
        json_start = response.find("{")
        json_end = response.rfind("}") + 1
        if json_start >= 0 and json_end > json_start:
            json_str = response[json_start:json_end]
            data = json.loads(json_str)
        else:
            # Try parsing the whole thing
            data = json.loads(response)

        clips = data.get("clips", [])

        if not clips:
            return None

        # Normalize to the format the CLI expects
        normalized = []
        for c in clips:
            scores = c.get("scores", {})
            total = sum(scores.values()) if scores else c.get("total_score", 0)
            normalized.append({
                "title": c.get("title", "Untitled")[:55],
                "start_second": round(float(c.get("start_second", 0)), 1),
                "end_second": round(float(c.get("end_second", 0)), 1),
                "duration": round(float(c.get("duration", 0))),
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
