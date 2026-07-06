"""
Clip suggestion via AI CLI (Claude Code or Codex).

Delegates moment selection to an AI CLI, which uses the PodStack knowledge base
(.podcli/knowledge/) and CLAUDE.md for context-aware clip extraction.

Priority: Claude Code → Codex → heuristic fallback.
"""

import json
import math
import os
import subprocess
import sys
import tempfile
from typing import Optional, Callable

from config.paths import paths

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from presets import MIN_CLIP_DURATION, MAX_CLIP_DURATION, TARGET_CLIP_DURATION_MIN, TARGET_CLIP_DURATION_MAX


def _cli_name_exts() -> list[str]:
    if sys.platform == "win32":
        return ["", ".cmd", ".exe", ".bat"]
    return [""]


def _resolve_cli_path(path: str) -> Optional[str]:
    for ext in _cli_name_exts():
        candidate = path + ext
        if os.path.isfile(candidate):
            return candidate
    return None


def _dedupe_dirs(dirs: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for directory in dirs:
        if not directory:
            continue
        directory = os.path.expanduser(directory)
        if directory in seen:
            continue
        seen.add(directory)
        if os.path.isdir(directory):
            ordered.append(directory)
    return ordered


def _npmrc_prefix_dirs() -> list[str]:
    dirs: list[str] = []
    npmrc_paths = [os.path.join(os.path.expanduser("~"), ".npmrc")]
    try:
        from services.env_settings import _env_path
        npmrc_paths.append(os.path.join(os.path.dirname(_env_path()), ".npmrc"))
    except Exception:
        pass
    for npmrc in npmrc_paths:
        if not os.path.isfile(npmrc):
            continue
        try:
            with open(npmrc, encoding="utf-8") as f:
                for line in f:
                    stripped = line.strip()
                    if not stripped or stripped.startswith("#") or stripped.startswith(";"):
                        continue
                    if stripped.startswith("prefix="):
                        prefix = stripped.split("=", 1)[1].strip()
                        if prefix:
                            dirs.append(prefix if sys.platform == "win32" else os.path.join(prefix, "bin"))
        except Exception:
            pass
    return dirs


def _package_manager_bin_dirs() -> list[str]:
    dirs: list[str] = []
    npm_cmds = [
        (["npm", "config", "get", "prefix"], "prefix"),
        (["npm", "root", "-g"], "root"),
    ]
    for args, kind in npm_cmds:
        try:
            result = subprocess.run(args, capture_output=True, text=True, timeout=2)
        except Exception:
            continue
        if result.returncode != 0:
            continue
        raw = result.stdout.strip().splitlines()[0].strip() if result.stdout.strip() else ""
        if not raw:
            continue
        if kind == "prefix":
            dirs.append(raw if sys.platform == "win32" else os.path.join(raw, "bin"))
        elif kind == "root":
            dirs.append(os.path.join(raw, ".bin"))
        else:
            dirs.append(raw)

    for args, kind in (
        (["pnpm", "config", "get", "global-bin-dir"], "bin"),
        (["pnpm", "bin", "-g"], "bin"),
        (["yarn", "global", "bin"], "bin"),
    ):
        try:
            result = subprocess.run(args, capture_output=True, text=True, timeout=2)
        except Exception:
            continue
        if result.returncode != 0:
            continue
        raw = result.stdout.strip().splitlines()[0].strip() if result.stdout.strip() else ""
        if raw:
            dirs.append(raw)

    return dirs


def _version_manager_bin_dirs() -> list[str]:
    home = os.path.expanduser("~")
    dirs = [
        os.path.join(home, "bin"),
        os.path.join(home, ".asdf", "shims"),
        os.path.join(home, ".local", "share", "mise", "shims"),
        os.path.join(home, ".local", "share", "rtx", "shims"),
        os.path.join(home, ".bun", "bin"),
        os.path.join(home, ".cargo", "bin"),
        os.path.join(home, "go", "bin"),
        os.path.join(home, ".local", "share", "pnpm"),
        os.path.join(home, ".claude", "bin"),
    ]

    nvm_dir = os.environ.get("NVM_DIR") or os.path.join(home, ".nvm")
    try:
        import glob
        dirs.extend(sorted(glob.glob(os.path.join(nvm_dir, "versions", "node", "*", "bin")), reverse=True))
        dirs.extend(glob.glob(os.path.join(home, ".fnm", "node-versions", "*", "installation", "bin")))
        dirs.extend(glob.glob(os.path.join(home, ".local", "share", "fnm", "node-versions", "*", "installation", "bin")))
    except Exception:
        pass

    fnm_bin = os.path.join(home, ".local", "share", "fnm", "current", "bin")
    dirs.append(fnm_bin)
    dirs.append(os.path.join(home, ".volta", "bin"))

    if sys.platform == "win32":
        for env_key in ("APPDATA", "LOCALAPPDATA", "ProgramFiles", "ProgramFiles(x86)", "ProgramW6432"):
            base = os.environ.get(env_key)
            if not base:
                continue
            dirs.extend([
                os.path.join(base, "npm"),
                os.path.join(base, "Programs", "nodejs"),
                os.path.join(base, "Microsoft", "WinGet", "Links"),
            ])
        dirs.append(os.path.join(home, "scoop", "shims"))
        dirs.append(os.path.join(os.environ.get("ProgramData", ""), "npm"))
    else:
        dirs.extend([
            "/usr/bin",
            "/bin",
            "/usr/local/bin",
            "/opt/homebrew/bin",
            "/opt/homebrew/sbin",
            "/snap/bin",
            "/var/lib/snapd/snap/bin",
        ])

    npm_prefix = (
        os.environ.get("NPM_CONFIG_PREFIX")
        or os.environ.get("npm_config_prefix")
        or ""
    ).strip()
    if npm_prefix:
        dirs.append(os.path.join(os.path.expanduser(npm_prefix), "bin"))

    return dirs


def _static_lookup_dirs() -> list[str]:
    home = os.path.expanduser("~")
    dirs = [
        os.path.join(home, ".local", "bin"),
        os.path.join(home, ".claude", "local", "bin"),
        os.path.join(home, ".claude", "local", "node_modules", ".bin"),
        os.path.join(home, ".npm-global", "bin"),
    ]
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        if appdata:
            dirs.append(os.path.join(appdata, "npm"))
        dirs.append(os.path.join(home, ".local", "bin"))
    return dirs


def _all_lookup_dirs() -> list[str]:
    return _dedupe_dirs(
        _static_lookup_dirs()
        + _version_manager_bin_dirs()
        + _npmrc_prefix_dirs()
        + _package_manager_bin_dirs()
    )


def _path_lookup_dirs() -> list[str]:
    return _all_lookup_dirs()


def _npm_global_bin_dirs() -> list[str]:
    return _package_manager_bin_dirs()


def _parse_shell_lookup_line(line: str) -> Optional[str]:
    candidate = line.strip().strip('"')
    if not candidate:
        return None
    if " is " in candidate:
        candidate = candidate.split(" is ", 1)[1].strip()
    if candidate.startswith("(") and candidate.endswith(")"):
        candidate = candidate[1:-1].strip()
    return _resolve_cli_path(candidate) or (candidate if os.path.isfile(candidate) else None)


def _shell_lookup(name: str) -> Optional[str]:
    if sys.platform == "win32":
        commands = [
            ["where", name],
            [
                "powershell",
                "-NoProfile",
                "-Command",
                f"(Get-Command {name} -All -ErrorAction SilentlyContinue | "
                f"Select-Object -ExpandProperty Source)",
            ],
        ]
    else:
        commands = [
            ["sh", "-lc", f"command -v {name}"],
            ["bash", "-lc", f"type -a {name} 2>/dev/null"],
            ["zsh", "-lc", f"whence -p {name} 2>/dev/null; command -v {name} 2>/dev/null"],
            ["fish", "-lc", f"type -a {name} 2>/dev/null"],
        ]

    for cmd in commands:
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=3)
        except Exception:
            continue
        if result.returncode != 0 or not result.stdout.strip():
            continue
        for line in result.stdout.strip().splitlines():
            resolved = _parse_shell_lookup_line(line)
            if resolved:
                return resolved
    return None


def _glob_cli_paths(name: str) -> list[str]:
    import glob
    home = os.path.expanduser("~")
    patterns = [
        os.path.join(home, ".claude", "bin", name),
        os.path.join(home, ".claude", "*", "bin", name),
        os.path.join(home, ".local", "share", "claude", "bin", name),
        os.path.join(home, ".local", "share", "npm", "*", "bin", name),
    ]
    if sys.platform == "win32":
        patterns.extend([
            os.path.join(home, ".claude", "bin", f"{name}.exe"),
            os.path.join(home, ".claude", "bin", f"{name}.cmd"),
        ])
    found: list[str] = []
    for pattern in patterns:
        try:
            found.extend(glob.glob(pattern))
        except Exception:
            pass
    return found


def _configured_cli_path(engine: str) -> Optional[str]:
    env_key = "PODCLI_CLAUDE_PATH" if engine == "claude" else "PODCLI_CODEX_PATH"
    raw = (os.environ.get(env_key) or "").strip()
    if not raw:
        try:
            from services.env_settings import _read_pairs
            raw = (_read_pairs().get(env_key) or "").strip()
        except Exception:
            pass
    if not raw:
        return None
    return _resolve_cli_path(raw) or (raw if os.path.isfile(raw) else None)


def _find_cli(name: str, extra_paths: list[str] = None) -> Optional[str]:
    import shutil

    for path in (extra_paths or []) + _glob_cli_paths(name):
        resolved = _resolve_cli_path(path)
        if resolved:
            return resolved

    lookup_dirs = _all_lookup_dirs()
    lookup_path = os.pathsep.join(lookup_dirs + [os.environ.get("PATH", "")])
    found = shutil.which(name, path=lookup_path)
    if found:
        return found

    for directory in lookup_dirs:
        resolved = _resolve_cli_path(os.path.join(directory, name))
        if resolved:
            return resolved

    for directory in (os.environ.get("PATH", "") or "").split(os.pathsep):
        if not directory:
            continue
        resolved = _resolve_cli_path(os.path.join(directory, name))
        if resolved:
            return resolved

    return _shell_lookup(name)


def _ai_cli_search_paths(name: str) -> list[str]:
    paths_out = [os.path.join(directory, name) for directory in _all_lookup_dirs()]
    paths_out.extend(_glob_cli_paths(name))
    return paths_out


def _env_cli_path(engine: str) -> Optional[str]:
    return _configured_cli_path(engine)


def get_ai_cli_status() -> dict:
    configured = {
        "claude": _configured_cli_path("claude"),
        "codex": _configured_cli_path("codex"),
    }
    candidates = [
        {"engine": engine, "path": path}
        for path, engine in _find_ai_cli_candidates()
    ]
    return {
        "configured": configured,
        "candidates": candidates,
        "available": bool(candidates),
        "searched_dirs": _all_lookup_dirs(),
    }


def _find_ai_cli_candidates() -> list[tuple[str, str]]:
    candidates = []

    claude = _env_cli_path("claude") or _find_cli("claude", _ai_cli_search_paths("claude"))
    if claude:
        candidates.append((claude, "claude"))

    codex = _env_cli_path("codex") or _find_cli("codex", _ai_cli_search_paths("codex"))
    if codex:
        candidates.append((codex, "codex"))

    return candidates


def _find_ai_cli() -> tuple[Optional[str], str]:
    """
    Find the best available AI CLI.

    Returns (path, engine) where engine is "claude" or "codex".
    Returns (None, "") if neither is available.
    """
    candidates = _find_ai_cli_candidates()
    return candidates[0] if candidates else (None, "")


def _engine_label(engine: str) -> str:
    """Human-readable name for an AI engine id."""
    if engine == "claude":
        return "Claude"
    if engine == "codex":
        return "Codex"
    return "AI"


def _format_timeout_label(timeout: int) -> str:
    """Render a human-readable timeout label for progress messages."""
    if timeout % 60 == 0 and timeout >= 60:
        minutes = timeout // 60
        unit = "minute" if minutes == 1 else "minutes"
        return f"{minutes} {unit}"
    return f"{timeout}s"


def _run_ai_command(
    cli_path: str,
    engine: str,
    prompt: str,
    prompt_file: str,
    project_dir: str,
    timeout: int,
) -> subprocess.CompletedProcess:
    """Execute one AI CLI prompt and return the completed process."""
    if engine == "codex":
        output_file = prompt_file + ".out"
        result = subprocess.run(
            [
                cli_path, "exec",
                "--full-auto",
                "-o", output_file,
                prompt,
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=project_dir,
            timeout=timeout,
        )
        if os.path.exists(output_file):
            with open(output_file, encoding="utf-8") as f:
                result = subprocess.CompletedProcess(
                    args=result.args,
                    returncode=result.returncode,
                    stdout=f.read(),
                    stderr=result.stderr,
                )
            try:
                os.unlink(output_file)
            except Exception:
                pass
        return result

    shell = sys.platform == "win32" and cli_path.lower().endswith((".cmd", ".bat"))
    cmd = f'"{cli_path}" --print -p -' if shell else [cli_path, "--print", "-p", "-"]
    with open(prompt_file, encoding="utf-8") as prompt_fh:
        return subprocess.run(
            cmd,
            stdin=prompt_fh,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=project_dir,
            timeout=timeout,
            shell=shell,
        )


def _load_existing_shorts(episodes_path: str) -> list[str]:
    """Extract existing short titles from episode database to avoid duplicates."""
    if not os.path.exists(episodes_path):
        return []
    try:
        with open(episodes_path, encoding="utf-8") as f:
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


def _build_prompt(
    transcript_text: str,
    segment_count: int,
    duration_min: float,
    top_n: int,
    exclude_clips: list[dict] | None = None,
) -> str:
    """Build the prompt for Claude to extract clips.

    Inlines key rules from the knowledge base since Claude --print mode
    can't read project files.
    """

    # Load knowledge base files inline — prioritized by relevance to clip selection
    kb_context = ""
    kb_dir = paths["knowledge"]
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
                with open(fpath, encoding="utf-8") as f:
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

    return f"""You are a viral clip editor for TikTok and YouTube Shorts. Find the {top_n} most scroll-stopping moments in this podcast transcript.

IMPORTANT: Return ONLY valid JSON. No markdown, no explanation, no code fences.

TIMESTAMP FORMAT: All timestamps in the transcript are in SECONDS (e.g., [123.4s]).
All timestamps you return MUST be in SECONDS as numbers (e.g., 123.4), NOT minutes:seconds.

DURATION RULES (CRITICAL):
- Target: {TARGET_CLIP_DURATION_MIN}-{TARGET_CLIP_DURATION_MAX} seconds (this is the viral sweet spot)
- Maximum: {MAX_CLIP_DURATION} seconds (absolute hard limit — anything longer WILL FAIL rendering)
- Minimum: {MIN_CLIP_DURATION} seconds (too short = no payoff)
- SHORTER IS BETTER. A punchy 25s clip outperforms a 40s clip every time.
- If a thought takes longer than {TARGET_CLIP_DURATION_MAX}s, use segments to cut the filler in the middle

CUTTING RULES (CRITICAL):
- Cut TIGHT. Every second must earn its place.
- Start at the exact moment the hook hits — no preamble, no "so", no "well"
- End the MOMENT the point lands with a complete thought — don't trail off
- NEVER cut mid-sentence or mid-thought. The viewer must feel closure.
- The last sentence must feel like a natural ending, a punchline, or a mic-drop
- If there's filler/tangent in the middle, use multiple segments to skip it
- A 30s clip with zero dead weight beats a {MAX_CLIP_DURATION}s clip with fluff

MOMENT SELECTION (think like a TikTok editor):
- Would YOU stop scrolling for this? If no, skip it.
- First 3 seconds must HOOK — a bold claim, shocking number, or provocative question
- Must make complete sense standalone — no "as I mentioned" or "going back to"
- Must end on a COMPLETE THOUGHT — sentence boundary, natural pause, or mic-drop moment
- Single focused idea — one concept, fully delivered, no loose threads
- Prioritize: controversial takes, surprising numbers, founder war stories, "wait what?" moments, emotional peaks
- Skip: generic advice, obvious statements, context-dependent references
- On long episodes, search the ENTIRE timeline and diversify the picks. Do not cluster all clips in one section if later sections contain strong standalone moments.

{f"KNOWLEDGE BASE:{kb_context}" if kb_context else ""}

{f"EXISTING SHORTS (avoid duplicating these moments):{chr(10).join('- ' + s for s in existing_shorts)}" if existing_shorts else ""}
{excluded_ranges}

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
- Final clip duration (sum of segments) MUST be {MIN_CLIP_DURATION}-{MAX_CLIP_DURATION} seconds (target {TARGET_CLIP_DURATION_MIN}-{TARGET_CLIP_DURATION_MAX}s)
- Each segment must start and end on COMPLETE SENTENCES — never mid-thought
- The LAST segment must end on a sentence that feels like a natural conclusion
- Must make sense standalone when stitched together
- Sort clips by timestamp order

Transcript ({segment_count} segments, ~{duration_min:.0f} min):

{transcript_text}"""


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
    """Keep the highest-scored `top_n` clips, then order them by start time.
    Ranking by score must come before truncation — otherwise the earliest clips
    ship, not the best ones."""
    if len(clips) <= top_n:
        return sorted(clips, key=lambda c: c.get("start_second", 0))
    ranked = sorted(clips, key=lambda c: c.get("score", 0), reverse=True)[:top_n]
    return sorted(ranked, key=lambda c: c.get("start_second", 0))


def find_moments_from_text(
    description: str,
    segments: list[dict],
    existing_clips: Optional[list[dict]] = None,
    progress_callback: Optional[Callable[[int, str], None]] = None,
    max_results: int = 3,
) -> list[dict]:
    """Locate the moment(s) the user described/pasted in the transcript via an AI
    CLI. Returns clip dicts (same shape as suggest_with_claude). Status goes to
    progress_callback; warnings to stderr — never stdout, which is the task
    runner's JSON-RPC channel."""
    existing_clips = existing_clips or []
    candidates = _find_ai_cli_candidates()
    if not candidates:
        print("No AI CLI available for moment search", file=sys.stderr, flush=True)
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
- Duration target: {TARGET_CLIP_DURATION_MIN}-{TARGET_CLIP_DURATION_MAX} seconds, max {MAX_CLIP_DURATION} seconds
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

    # Prompt goes to .podcli/tmp/ (gitignored), not the repo root, so a crash
    # mid-run never litters the working tree with transcript dumps.
    project_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
    from utils.prompt_files import write_prompt_file
    prompt_file = write_prompt_file(prompt)

    try:
        for idx, (cli_path, engine) in enumerate(candidates):
            if progress_callback:
                label = "Claude" if engine == "claude" else "Codex"
                progress_callback(40, f"Searching transcript with {label}...")
            try:
                result = _run_ai_command(
                    cli_path=cli_path,
                    engine=engine,
                    prompt=prompt,
                    prompt_file=prompt_file,
                    project_dir=project_dir,
                    timeout=300,
                )
            except Exception:
                continue

            if result.returncode != 0 or not result.stdout.strip():
                continue

            response = result.stdout.strip()
            if "```" in response:
                import re

                fence_match = re.search(r"```(?:json)?\s*\n?(.*?)\n?\s*```", response, re.DOTALL)
                if fence_match:
                    response = fence_match.group(1).strip()

            try:
                json_start = response.find("{")
                if json_start >= 0:
                    data, _ = json.JSONDecoder().raw_decode(response, json_start)
                else:
                    data = json.loads(response)
            except Exception:
                continue

            found = []
            for c in data.get("clips", []):
                scores = c.get("scores", {})
                total = sum(scores.values()) if scores else c.get("total_score", 0)
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
                if kept_duration < MIN_CLIP_DURATION or kept_duration > MAX_CLIP_DURATION:
                    continue

                found.append({
                    "title": c.get("title", "Untitled")[:55],
                    "start_second": keep_segments[0]["start"] if keep_segments else start_sec,
                    "end_second": keep_segments[-1]["end"] if keep_segments else end_sec,
                    "segments": keep_segments,
                    "duration": round(kept_duration),
                    "score": total,
                    "content_type": c.get("content_type", "unknown"),
                    "reasoning": c.get("why", ""),
                    "preview_text": c.get("quote", "")[:120],
                    "suggested_caption_style": "hormozi",
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
    finally:
        try:
            os.unlink(prompt_file)
        except Exception:
            pass


def suggest_with_claude(
    segments: list[dict],
    top_n: int = 5,
    exclude_clips: list[dict] | None = None,
    progress_callback: Optional[Callable[[int, str], None]] = None,
    timeout: int = 300,
) -> Optional[list[dict]]:
    """
    Use an AI CLI (Claude Code or Codex) to extract the best clip moments.

    Tries available AI CLIs in preference order and retries on runtime failure.
    Returns None if neither succeeds.
    """
    candidates = _find_ai_cli_candidates()
    if not candidates:
        return None

    if progress_callback:
        label = _engine_label(candidates[0][1])
        progress_callback(0, f"Preparing transcript for {label}...")

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
    )

    # Write prompt to temp file to avoid shell escaping issues.
    # Goes to .podcli/tmp/ (gitignored) so crashes don't litter the repo root.
    project_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")

    from utils.prompt_files import write_prompt_file
    prompt_file = write_prompt_file(prompt)

    if progress_callback:
        first_label = _engine_label(candidates[0][1])
        progress_callback(20, f"Asking {first_label} to analyze transcript...")

    try:
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

        for idx, (cli_path, engine) in enumerate(candidates):
            label = _engine_label(engine)
            if idx > 0 and progress_callback:
                progress_callback(0, f"Retrying with {label}...")
                progress_callback(20, f"Asking {label} to analyze transcript...")

            try:
                result = _run_ai_command(
                    cli_path=cli_path,
                    engine=engine,
                    prompt=prompt,
                    prompt_file=prompt_file,
                    project_dir=project_dir,
                    timeout=timeout,
                )
            except subprocess.TimeoutExpired:
                if progress_callback:
                    progress_callback(0, f"{label} timed out ({_format_timeout_label(timeout)} limit)")
                continue
            except Exception as e:
                if progress_callback:
                    progress_callback(0, f"{label} error: {e}")
                continue

            if result.returncode != 0 or not result.stdout.strip():
                if progress_callback:
                    detail = (result.stderr or "no response").strip()[:200]
                    progress_callback(0, f"{label} returned error: {detail}")
                continue

            if progress_callback:
                progress_callback(80, f"Parsing {label}'s suggestions...")

            try:
                response = result.stdout.strip()
                if "```" in response:
                    import re
                    fence_match = re.search(r"```(?:json)?\s*\n?(.*?)\n?\s*```", response, re.DOTALL)
                    if fence_match:
                        response = fence_match.group(1).strip()

                json_start = response.find("{")
                if json_start >= 0:
                    decoder = json.JSONDecoder()
                    data, _ = decoder.raw_decode(response, json_start)
                else:
                    data = json.loads(response)
            except json.JSONDecodeError as e:
                if progress_callback:
                    progress_callback(0, f"Could not parse {label}'s response as JSON: {e}")
                continue

            clips = data.get("clips", [])
            if not clips:
                if progress_callback:
                    progress_callback(0, f"{label} returned no clips")
                continue

            normalized = []
            for c in clips:
                scores = c.get("scores", {})
                total = sum(scores.values()) if scores else c.get("total_score", 0)

                raw_segments = c.get("segments", [])
                keep_segments = []
                for seg in raw_segments:
                    s = round(_parse_seconds(seg.get("start", 0)), 1)
                    e = round(_parse_seconds(seg.get("end", 0)), 1)
                    if e > s:
                        keep_segments.append({"start": s, "end": e})

                start_sec = round(_parse_seconds(c.get("start_second", 0)), 1)
                end_sec = round(_parse_seconds(c.get("end_second", 0)), 1)

                if not keep_segments and end_sec > start_sec:
                    keep_segments = [{"start": start_sec, "end": end_sec}]

                kept_duration = sum(seg["end"] - seg["start"] for seg in keep_segments)
                if kept_duration < MIN_CLIP_DURATION or kept_duration > MAX_CLIP_DURATION:
                    continue

                normalized.append({
                    "title": c.get("title", "Untitled")[:55],
                    "start_second": keep_segments[0]["start"] if keep_segments else start_sec,
                    "end_second": keep_segments[-1]["end"] if keep_segments else end_sec,
                    "segments": keep_segments,
                    "duration": round(kept_duration),
                    "score": total,
                    "content_type": c.get("content_type", "unknown"),
                    "reasoning": c.get("why", ""),
                    "preview_text": c.get("quote", "")[:120],
                    "suggested_caption_style": "hormozi",
                    "quote": c.get("quote", ""),
                    "why": c.get("why", ""),
                    "reasons": [c.get("content_type", "")],
                    "preview": c.get("quote", "")[:120],
                    "_ai_engine": engine,
                })

            selected = _select_top_by_score(
                _drop_clips_overlapping(normalized, exclude_clips or []), top_n
            )

            if selected:
                if progress_callback:
                    progress_callback(100, f"{label} suggested {len(selected)} clips")
                return selected

            if progress_callback:
                progress_callback(0, f"{label} returned no usable clips")

        return None
    finally:
        # Clean up temp file
        try:
            os.unlink(prompt_file)
        except Exception:
            pass


def suggest_initial_with_claude(
    segments: list[dict],
    top_n: int = 5,
    progress_callback: Optional[Callable[[int, str], None]] = None,
) -> Optional[list[dict]]:
    """
    Initial clip discovery entry point.

    For long podcasts, search transcript buckets first so the AI can return
    quickly and cover the full timeline instead of timing out on one giant
    prompt.
    """
    if not _should_bucket_initial_selection(segments):
        return suggest_with_claude(
            segments=segments,
            top_n=top_n,
            progress_callback=progress_callback,
            timeout=180,
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
            progress_callback=progress_callback,
            timeout=180,
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
            exclude_clips=aggregated,
            progress_callback=(
                None if progress_callback is None
                else lambda pct, msg, bucket_label=bucket_label: progress_callback(
                    pct,
                    f"{bucket_label}: {msg}" if msg else msg,
                )
            ),
            timeout=90,
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
        exclude_clips=deduped,
        progress_callback=(
            None if progress_callback is None
            else lambda pct, msg: progress_callback(
                pct,
                f"global pass: {msg}" if msg else msg,
            )
        ),
        timeout=120,
    )
    if fallback_clips:
        deduped = _dedupe_clips_by_range(deduped + fallback_clips)

    return _select_top_by_score(deduped, top_n) if deduped else None


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
        )
        if fallback_clips:
            aggregated.extend(fallback_clips)

    deduped = _dedupe_clips_by_range(aggregated)
    return _select_top_by_score(deduped, top_n) if deduped else None
