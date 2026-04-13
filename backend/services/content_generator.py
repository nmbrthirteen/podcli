"""
Per-clip content generation (titles, descriptions, tags) via AI CLI.

Single source of truth used by CLI, Web UI, and MCP.
"""

import json
import os
import subprocess
import tempfile
from typing import Optional, Callable

from services.claude_suggest import _engine_label, _find_ai_cli_candidates, _run_ai_command


def _load_kb_context() -> str:
    """Load PodStack knowledge base files for content generation."""
    kb_dir = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "..", ".podcli", "knowledge"
    )
    kb_context = ""
    for fname, max_chars in [
        ("05-title-formulas.md", 3000),
        ("06-descriptions-template.md", 2000),
        ("02-voice-and-tone.md", 2000),
        ("01-brand-identity.md", 1000),
        ("12-quick-reference.md", 1500),
    ]:
        fpath = os.path.join(kb_dir, fname)
        if os.path.exists(fpath):
            try:
                with open(fpath) as kf:
                    content = kf.read().strip()
                # Skip uncustomized templates
                if content.count("[Your Show Name]") > 2 and len(content) < 500:
                    continue
                kb_context += f"\n--- {fname} ---\n{content[:max_chars]}\n"
            except Exception:
                pass
    return kb_context


def generate_clip_content(
    clip: dict,
    transcript_segments: list[dict],
    progress_callback: Optional[Callable[[int, str], None]] = None,
) -> Optional[dict]:
    """
    Generate titles, description, tags, and hashtags for a single clip.

    Args:
        clip: dict with title, start_second, end_second, content_type
        transcript_segments: full transcript segments list
        progress_callback: optional (percent, message) callback

    Returns:
        dict with raw_text, titles, description, tags, hashtags, or None if AI unavailable
    """
    candidates = _find_ai_cli_candidates()
    if not candidates:
        return None

    label = _engine_label(candidates[0][1])
    if progress_callback:
        progress_callback(0, f"Generating content via {label}...")

    kb_context = _load_kb_context()

    # Extract transcript text for this clip's time range
    clip_start = clip.get("start_second", 0)
    clip_end = clip.get("end_second", 0)
    clip_transcript = []
    for seg in transcript_segments:
        seg_start = seg.get("start", 0)
        if clip_start - 5 <= seg_start <= clip_end + 5:
            sp = seg.get("speaker", "")
            sp_label = f"[{sp}] " if sp else ""
            clip_transcript.append(f"{sp_label}{seg.get('text', '').strip()}")

    prompt = f"""Generate a YouTube Shorts content package for this clip. Return ONLY the content below, no preamble.

KNOWLEDGE BASE:
{kb_context}

CLIP: "{clip.get('title', '')}"
Duration: {clip_end - clip_start:.0f}s
Content type: {clip.get('content_type', 'unknown')}

TRANSCRIPT EXCERPT:
{chr(10).join(clip_transcript[:30])}

Generate exactly this (no other text):

TITLES (8 options, 40-60 chars, keyword-first, follow title spec):
1. [title]
2. [title]
3. [title]
4. [title]
5. [title]
6. [title]
7. [title]
8. [title]
TOP PICK: [number] — [why]

DESCRIPTION:
[hook line under 100 chars]
[guest attribution line]

TAGS:
[comma-separated, 8-12 tags for YouTube]

HASHTAGS:
[5 hashtags for description]"""

    project_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")

    from utils.prompt_files import write_prompt_file
    prompt_file = write_prompt_file(prompt)

    try:
        for idx, (cli_path, engine) in enumerate(candidates):
            label = _engine_label(engine)
            if progress_callback:
                if idx > 0:
                    progress_callback(0, f"Retrying content generation with {label}...")
                progress_callback(30, f"Asking {label} for titles & descriptions...")

            try:
                cr = _run_ai_command(
                    cli_path=cli_path,
                    engine=engine,
                    prompt=prompt[:4000] if engine == "codex" else prompt,
                    prompt_file=prompt_file,
                    project_dir=project_dir,
                    timeout=120,
                )
            except subprocess.TimeoutExpired:
                continue
            except Exception:
                continue

            if cr.returncode != 0 or not cr.stdout.strip():
                continue

            raw_text = cr.stdout.strip()

            if progress_callback:
                progress_callback(90, "Parsing content...")

            result = {
                "raw_text": raw_text,
                "titles": [],
                "top_pick": "",
                "description": "",
                "tags": "",
                "hashtags": "",
                "engine": engine,
            }

            lines = raw_text.split("\n")
            section = ""
            for line in lines:
                stripped = line.strip()
                if not stripped:
                    continue
                upper = stripped.upper()
                if upper.startswith("TITLES"):
                    section = "titles"
                    continue
                elif upper.startswith("TOP PICK"):
                    result["top_pick"] = stripped
                    section = ""
                    continue
                elif upper.startswith("DESCRIPTION"):
                    section = "description"
                    continue
                elif upper.startswith("TAGS"):
                    section = "tags"
                    continue
                elif upper.startswith("HASHTAGS"):
                    section = "hashtags"
                    continue

                if section == "titles" and stripped[0:1].isdigit() and ". " in stripped[:4]:
                    result["titles"].append(stripped)
                elif section == "description":
                    result["description"] += stripped + "\n"
                elif section == "tags":
                    result["tags"] = stripped
                elif section == "hashtags":
                    result["hashtags"] = stripped

            result["description"] = result["description"].strip()
            if not result["titles"] and not result["description"]:
                continue

            if progress_callback:
                progress_callback(100, f"Content ready ({len(result['titles'])} titles)")

            return result

        return None
    finally:
        try:
            os.unlink(prompt_file)
        except Exception:
            pass
