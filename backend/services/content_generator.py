"""
Per-clip content generation (titles, descriptions, tags).

Single source of truth used by CLI, Web UI, and MCP.
"""

import json
import os
import subprocess
import sys
import tempfile
import threading
from typing import Optional, Callable

from config.paths import paths
from services import ai_provider
from services.knowledge_base import load_kb_context as kb_load_context, warn_missing_context

# Codex silently truncates long prompts, so it gets a shortened one. The prompts
# here lead with the request precisely so this cut only costs transcript tail.
CODEX_PROMPT_LIMIT = 4000


def _shorten_for_codex(engine: str, prompt: str) -> str:
    return prompt[:CODEX_PROMPT_LIMIT] if engine == "codex" else prompt


CONTENT_KB_FILES = [
    ("05-title-formulas.md", 3000),
    ("06-descriptions-template.md", 2000),
    ("02-voice-and-tone.md", 2000),
    ("01-brand-identity.md", 1000),
    ("12-quick-reference.md", 1500),
]


def load_kb_context(
    files: Optional[list[tuple[str, int]]] = None,
    task: str = "content generation",
) -> str:
    """Load PodStack knowledge base files as inline prompt context."""
    kb_context = kb_load_context(files if files is not None else CONTENT_KB_FILES)
    if not kb_context:
        warn_missing_context(task)
    return kb_context


def _sample_lines(lines: list[str], max_lines: int = 30) -> list[str]:
    """Pick evenly spaced lines so long episodes are represented end to end."""
    if len(lines) <= max_lines:
        return lines
    last = len(lines) - 1
    picked = sorted({round(i * last / (max_lines - 1)) for i in range(max_lines)})
    return [lines[i] for i in picked]


def _parse_content(raw_text: str) -> dict:
    result = {
        "raw_text": raw_text,
        "titles": [],
        "top_pick": "",
        "description": "",
        "tags": "",
        "hashtags": "",
    }
    section = ""
    for line in raw_text.split("\n"):
        stripped = line.strip()
        if not stripped:
            # Keep paragraph breaks in multi-paragraph episode descriptions.
            if section == "description" and result["description"]:
                result["description"] += "\n"
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
    return result


def _stream_claude_content(
    cli_path: str,
    prompt_file: str,
    project_dir: str,
    timeout: int,
    on_partial: Callable[[dict], None],
) -> Optional[str]:
    """Stream one claude --print run, emitting a parsed partial package as each
    output line completes. Returns the full response text, or None when
    streaming is unavailable so the caller can fall back to the blocking runner.
    """
    args = [
        cli_path, "--print", "--verbose",
        "--output-format", "stream-json", "--include-partial-messages",
        "-p", "-",
    ]
    try:
        prompt_fh = open(prompt_file, encoding="utf-8")
    except Exception:
        return None
    try:
        proc = subprocess.Popen(
            args,
            stdin=prompt_fh,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=project_dir,
        )
    except Exception:
        prompt_fh.close()
        return None

    # readline blocks between deltas, so the timeout is enforced by a watchdog
    # kill rather than an in-loop deadline check.
    watchdog = threading.Timer(timeout, proc.kill)
    watchdog.start()
    text = ""
    final_text = None
    emitted = None
    try:
        for line in proc.stdout:
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                event = json.loads(line)
            except Exception:
                continue
            etype = event.get("type")
            if etype == "stream_event":
                delta = (event.get("event") or {}).get("delta") or {}
                if delta.get("type") == "text_delta":
                    chunk = delta.get("text", "")
                    text += chunk
                    if "\n" in chunk:
                        parsed = _parse_content(text)
                        snapshot = json.dumps(parsed, sort_keys=True)
                        if snapshot != emitted:
                            emitted = snapshot
                            on_partial(parsed)
            elif etype == "result":
                final_text = event.get("result") or None
        rc = proc.wait()
    except Exception:
        try:
            proc.kill()
            proc.wait()
        except Exception:
            pass
        return None
    finally:
        watchdog.cancel()
        prompt_fh.close()

    out = (final_text or text).strip()
    if rc != 0 or not out:
        return None
    return out


def generate_custom_content(
    instruction: str,
    transcript_segments: list[dict],
    mode: str = "shorts",
    progress_callback: Optional[Callable[[int, str], None]] = None,
) -> Optional[dict]:
    """Run a free-form content request against the AI CLI with KB + transcript context.

    Returns {"text", "engine"} with the raw model output, or None if no AI CLI.
    """
    if not ai_provider.available():
        return None

    kb_context = load_kb_context()
    lines = []
    for seg in transcript_segments:
        sp = seg.get("speaker", "")
        sp_label = f"[{sp}] " if sp else ""
        lines.append(f"{sp_label}{seg.get('text', '').strip()}")
    excerpt = chr(10).join(_sample_lines(lines, max_lines=40))

    # REQUEST first so codex prompt truncation never drops the ask.
    prompt = f"""You are helping create YouTube content for a podcast. Follow the knowledge base voice and rules. Return ONLY the requested output, no preamble.

REQUEST ({'full episode' if mode == 'episode' else 'short clip'}):
{instruction.strip()}

KNOWLEDGE BASE:
{kb_context}

TRANSCRIPT EXCERPT:
{excerpt}"""

    attempted: list[str] = []

    def announce(label: str) -> None:
        if progress_callback:
            progress_callback(30, f"Asking {label}..." if not attempted else f"Retrying with {label}...")
        attempted.append(label)

    result = ai_provider.generate(
        prompt,
        timeout=120,
        on_attempt=announce,
        adapt=_shorten_for_codex,
    )
    if not result.ok:
        print(f"Warning: content generation failed: {result.error}", file=sys.stderr)
        return None
    if progress_callback:
        progress_callback(100, "Done")
    return {"text": result.text, "engine": result.provider}


def generate_clip_content(
    clip: dict,
    transcript_segments: list[dict],
    progress_callback: Optional[Callable[[int, str], None]] = None,
    mode: str = "shorts",
    partial_callback: Optional[Callable[[dict], None]] = None,
) -> Optional[dict]:
    """
    Generate titles, description, tags, and hashtags for a single clip.

    Args:
        clip: dict with title, start_second, end_second, content_type
        transcript_segments: full transcript segments list
        progress_callback: optional (percent, message) callback
        mode: "shorts" (per-clip package) or "episode" (long-form episode package)

    Returns:
        dict with raw_text, titles, description, tags, hashtags, or None if AI unavailable
    """
    providers = ai_provider.status()["providers"]
    if not providers:
        return None

    if progress_callback:
        progress_callback(0, f"Generating content via {providers[0]['label']}...")

    kb_context = load_kb_context(task="title and description generation")

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

    if mode == "episode":
        prompt = f"""Generate a YouTube content package for this full podcast episode. Return ONLY the content below, no preamble.

KNOWLEDGE BASE:
{kb_context}

EPISODE: "{clip.get('title', '')}"

TRANSCRIPT EXCERPT:
{chr(10).join(_sample_lines(clip_transcript))}

Generate exactly this (no other text):

TITLES (8 options, 50-70 chars, keyword-first, follow title spec):
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
[2-3 short paragraphs: what the episode covers and why it matters]
[guest attribution line]

TAGS:
[comma-separated, 10-15 tags for YouTube]

HASHTAGS:
[3-5 hashtags for description]"""
    else:
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

    def usable(text: str) -> bool:
        parsed = _parse_content(text)
        return bool(parsed["titles"] or parsed["description"])

    raw_text = None
    engine_used = ""

    # The Studio renders titles as they arrive. Only the Claude CLI can stream,
    # so it gets first refusal; everything else falls through to the chain.
    if partial_callback is not None:
        cli_path = ai_provider.claude_cli_path()
        if cli_path:
            if progress_callback:
                progress_callback(30, "Asking Claude for titles & descriptions...")
            from utils.prompt_files import write_prompt_file
            prompt_file = write_prompt_file(prompt)
            try:
                raw_text = _stream_claude_content(
                    cli_path=cli_path,
                    prompt_file=prompt_file,
                    project_dir=project_dir,
                    timeout=120,
                    on_partial=partial_callback,
                )
                engine_used = "claude"
            finally:
                try:
                    os.unlink(prompt_file)
                except OSError:
                    pass

    if raw_text is None or not usable(raw_text):
        attempted: list[str] = []

        def announce(label: str) -> None:
            if progress_callback:
                if attempted:
                    progress_callback(0, f"Retrying content generation with {label}...")
                progress_callback(30, f"Asking {label} for titles & descriptions...")
            attempted.append(label)

        attempt = ai_provider.generate(
            prompt,
            timeout=120,
            project_dir=project_dir,
            on_attempt=announce,
            adapt=_shorten_for_codex,
            accept=usable,
        )
        if not attempt.ok:
            return None
        raw_text, engine_used = attempt.text, attempt.provider

    if progress_callback:
        progress_callback(90, "Parsing content...")

    result = _parse_content(raw_text)
    result["engine"] = engine_used

    if progress_callback:
        progress_callback(100, f"Content ready ({len(result['titles'])} titles)")

    return result
