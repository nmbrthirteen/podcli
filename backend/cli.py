#!/usr/bin/env python3
"""
podcli — CLI entry point.

One-command processing:
    python cli.py process video.mp4 --top 5 --transcript transcript.txt
    python cli.py process video.mp4 --preset myshow
    python cli.py presets list
    python cli.py presets save myshow --caption-style branded --logo ~/logo.png
    python cli.py info  (show encoder, system info)
"""

import argparse
import json
import os
import sys
import time

# Suppress macOS ObjC duplicate class warnings from OpenCV's bundled dylibs
os.environ["OBJC_DISABLE_INITIALIZE_FORK_SAFETY"] = "YES"
if sys.platform == "darwin":
    os.environ.setdefault("DYLD_LIBRARY_PATH", "")

# Load .env file into os.environ (HF_TOKEN, PODCLI_QUALITY, etc.)
_env_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".env")
if os.path.exists(_env_file):
    with open(_env_file) as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _key, _val = _line.split("=", 1)
                _key, _val = _key.strip(), _val.strip()
                if _key and _val:
                    os.environ.setdefault(_key, _val)

# Add parent dir to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def _ensure_ssl_certs():
    """Fix SSL certificate issues automatically (macOS + corporate proxies)."""
    import ssl

    # Quick test — if HTTPS works, we're fine
    try:
        import urllib.request
        urllib.request.urlopen("https://huggingface.co", timeout=5)
        return
    except Exception:
        pass

    print("  ⚠ SSL issue detected — fixing automatically...")

    # Method 1: Set certifi certs (works for most cases including corporate proxies)
    try:
        import certifi
        os.environ["SSL_CERT_FILE"] = certifi.where()
        os.environ["REQUESTS_CA_BUNDLE"] = certifi.where()
        # Monkey-patch ssl to use certifi for this process
        ssl._create_default_https_context = lambda: ssl.create_default_context(cafile=certifi.where())
        print("  ✓ SSL configured via certifi")
        return
    except ImportError:
        pass

    # Method 2: pip install certifi into venv, then use it
    venv_pip = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "venv", "bin", "pip")
    if os.path.exists(venv_pip):
        import subprocess as sp
        try:
            sp.run([venv_pip, "install", "-q", "certifi"], capture_output=True, timeout=60)
            import importlib
            certifi = importlib.import_module("certifi")
            os.environ["SSL_CERT_FILE"] = certifi.where()
            os.environ["REQUESTS_CA_BUNDLE"] = certifi.where()
            ssl._create_default_https_context = lambda: ssl.create_default_context(cafile=certifi.where())
            print("  ✓ SSL configured (installed certifi)")
            return
        except Exception:
            pass

    # Method 3: macOS Install Certificates command
    if sys.platform == "darwin":
        import subprocess as sp
        import glob
        ver = f"{sys.version_info.major}.{sys.version_info.minor}"
        cert_scripts = glob.glob(f"/Applications/Python {ver}*/Install Certificates.command") + \
                       glob.glob(f"/Applications/Python {ver}/Install Certificates.command")
        if cert_scripts:
            try:
                sp.run(["bash", cert_scripts[0]], capture_output=True, timeout=30)
                # Force reload ssl context
                ssl._create_default_https_context = ssl._create_unverified_context
                print("  ✓ SSL certificates installed (restart may be needed)")
                return
            except Exception:
                pass

    # Method 4: Last resort — disable SSL verification for this session
    # This is safe because we're only downloading Whisper models from known URLs
    ssl._create_default_https_context = ssl._create_unverified_context
    os.environ["PYTHONHTTPSVERIFY"] = "0"
    print("  ⚠ SSL verification disabled for this session (model downloads only)")


def cmd_process(args):
    """Full auto pipeline: transcribe → suggest → export."""
    from services.clip_generator import generate_clip
    from services.transcript_parser import parse_speaker_transcript
    from services.audio_analyzer import get_energy_profile
    from services.encoder import get_encoder_info
    from presets import get_preset, DEFAULT_PRESET

    video_path = _clean_path(args.video)
    if not os.path.exists(video_path):
        print(f"Error: Video not found: {video_path}", file=sys.stderr)
        sys.exit(1)

    # Load preset or defaults
    if args.preset:
        try:
            config = get_preset(args.preset)
            print(f"  Using preset: {args.preset}")
        except FileNotFoundError:
            print(f"Error: Preset '{args.preset}' not found", file=sys.stderr)
            sys.exit(1)
    else:
        config = {**DEFAULT_PRESET}

    # CLI overrides
    if args.caption_style:
        config["caption_style"] = args.caption_style
    if args.crop:
        config["crop_strategy"] = args.crop
    if args.top:
        config["top_clips"] = args.top
    if args.logo:
        from services.asset_store import resolve as resolve_asset
        resolved = resolve_asset(args.logo)
        if resolved:
            config["logo_path"] = resolved
        else:
            print(f"  Warning: Logo '{args.logo}' not found (checked assets and filesystem)", file=sys.stderr)
            config["logo_path"] = args.logo  # pass through anyway
    if args.outro:
        from services.asset_store import resolve as resolve_asset_outro
        resolved = resolve_asset_outro(args.outro)
        if resolved:
            config["outro_path"] = resolved
        else:
            print(f"  Warning: Outro '{args.outro}' not found (checked assets and filesystem)", file=sys.stderr)
            config["outro_path"] = args.outro
    if args.time_adjust is not None:
        config["time_adjust"] = args.time_adjust
    if args.no_energy:
        config["energy_boost"] = False
    if args.quality:
        config["quality"] = args.quality

    # Set quality env var before importing video_processor
    quality = config.get("quality", os.environ.get("PODCLI_QUALITY", "high"))
    os.environ["PODCLI_QUALITY"] = quality

    # Output directory
    output_dir = args.output or os.path.join(os.path.dirname(video_path), "clips")
    os.makedirs(output_dir, exist_ok=True)

    enc_info = get_encoder_info()
    print(f"\n  podcli — processing")
    print(f"  Encoder: {enc_info['best']} ({enc_info['system']})")
    print(f"  Quality: {quality}")
    print(f"  Video:   {os.path.basename(video_path)}")
    print()

    # ── Step 1: Get transcript ──
    transcript = None
    words = []
    segments = []

    if args.transcript:
        print("  [1/5] Loading transcript...")
        with open(args.transcript, "r") as f:
            raw_text = f.read()

        # Detect format
        if raw_text.strip().startswith("{") or raw_text.strip().startswith("["):
            data = json.loads(raw_text)
            if isinstance(data, list):
                words = data
            else:
                words = data.get("words", [])
                segments = data.get("segments", [])
            print(f"         JSON transcript: {len(words)} words")
        else:
            parsed = parse_speaker_transcript(
                raw_text,
                time_adjust=config.get("time_adjust", 0),
            )
            if "error" in parsed:
                print(f"  Error: {parsed['error']}", file=sys.stderr)
                sys.exit(1)
            words = parsed["words"]
            segments = parsed["segments"]
            print(f"         Parsed: {len(segments)} segments, {len(words)} words")
    else:
        print("  [1/5] Transcribing with Whisper...")
        _ensure_ssl_certs()
        import warnings
        warnings.filterwarnings("ignore", message="FP16 is not supported on CPU")
        from services.transcription import transcribe_file
        import threading

        # Spinner runs in background while Whisper blocks
        _spin_stop = threading.Event()
        _spin_msg = ["Loading model..."]

        def _spinner():
            frames = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
            i = 0
            while not _spin_stop.is_set():
                print(f"\r         {frames[i % len(frames)]}  {_spin_msg[0][:55]:<55}", end="", flush=True)
                i += 1
                _spin_stop.wait(0.12)
            print(f"\r         {'':60}", end="\r")  # clear line

        spin_thread = threading.Thread(target=_spinner, daemon=True)
        spin_thread.start()

        def _transcribe_progress(pct, msg):
            _spin_msg[0] = f"{msg} ({pct}%)" if pct < 100 else msg

        result = transcribe_file(
            file_path=video_path,
            model_size=config.get("whisper_model", "base"),
            progress_callback=_transcribe_progress,
        )
        _spin_stop.set()
        spin_thread.join(timeout=1)
        words = result["words"]
        segments = result["segments"]
        print(f"         Done: {len(segments)} segments, {len(words)} words")

    # Check speaker data availability (needed for smart cropping)
    speakers_in_words = set(w.get("speaker") for w in words if w.get("speaker"))
    if len(speakers_in_words) > 1:
        print(f"         Speakers detected: {len(speakers_in_words)} (crop will follow active speaker)")
    elif len(speakers_in_words) == 1:
        print(f"         Single speaker detected (static face crop)")
    else:
        print(f"         No speaker data (center crop fallback)")

    if not segments:
        print("  Error: No transcript segments found.", file=sys.stderr)
        sys.exit(1)

    # ── Step 2: Analyze audio energy ──
    energy_scores = None
    if config.get("energy_boost", True):
        print("  [2/5] Analyzing audio energy...")
        try:
            profile = get_energy_profile(video_path, segments)
            energy_scores = profile["segment_scores"]
            print(f"         {len(profile['peak_times'])} peak moments found")
        except Exception as e:
            print(f"         Skipped (error: {e})")
    else:
        print("  [2/5] Audio analysis skipped (--no-energy)")

    # ── Step 3: Score and select clips ──
    top_n = config.get("top_clips", 5)
    clips = None

    # Try Claude first (uses PodStack knowledge base for intelligent selection)
    from services.claude_suggest import suggest_with_claude, _find_claude

    claude_path = _find_claude()
    if claude_path:
        print(f"  [3/5] Selecting moments with Claude (PodStack)...")
        clips = suggest_with_claude(
            segments=segments,
            top_n=top_n,
            progress_callback=lambda pct, msg: print(f"         {msg}") if msg else None,
        )
        if clips:
            print(f"         ✓ Claude selected {len(clips)} clips")
        else:
            print(f"         ⚠ Claude unavailable, falling back to heuristics")
    else:
        print(f"  [3/5] Scoring clips (heuristic mode)...")
        print(f"         ℹ Install Claude Code for smarter selection:")
        print(f"           https://docs.anthropic.com/en/docs/claude-code")

    # Fallback to heuristic algorithm
    if not clips:
        clips = _suggest_clips(
            segments=segments,
            energy_scores=energy_scores,
            top_n=top_n,
            min_dur=config.get("min_clip_duration", 20),
            max_dur=config.get("max_clip_duration", 90),
        )

    if not clips:
        print("  No clips found. Try a longer transcript or lower --min-duration.", file=sys.stderr)
        sys.exit(1)

    print(f"\n         Selected {len(clips)} clips:")
    for i, c in enumerate(clips):
        m_s = int(c["start_second"]) // 60
        s_s = int(c["start_second"]) % 60
        ctype = c.get("content_type", "")
        score_val = c.get("score", 0)
        score_str = f"({score_val}/20)" if isinstance(score_val, int) and score_val <= 20 else f"({score_val:.0f}pts)"
        type_tag = f" [{ctype}]" if ctype and ctype != "unknown" else ""
        print(f"           {i+1}. [{m_s}:{s_s:02d} → +{c['duration']}s] {score_str}{type_tag} {c['title'][:50]}")
        if c.get("why"):
            print(f"              {c['why'][:70]}")

    # ── Step 4: Export ──
    print(f"\n  [4/5] Exporting {len(clips)} clips to {output_dir}/")
    results = []
    t0 = time.time()

    for i, clip in enumerate(clips):
        print(f"         Clip {i+1}/{len(clips)}: {clip['title'][:40]}...", end="", flush=True)
        try:
            result = generate_clip(
                video_path=video_path,
                start_second=clip["start_second"],
                end_second=clip["end_second"],
                caption_style=config.get("caption_style", "branded"),
                crop_strategy=config.get("crop_strategy", "face"),
                transcript_words=words,
                title=clip.get("title", f"clip_{i+1}"),
                output_dir=output_dir,
                logo_path=config.get("logo_path") or None,
                outro_path=config.get("outro_path") or None,
            )
            results.append(result)
            print(f" ✓ {result['file_size_mb']}MB")
        except Exception as e:
            print(f" ✗ {e}")
            results.append({"status": "error", "error": str(e)})

    elapsed = time.time() - t0
    success = sum(1 for r in results if "output_path" in r)
    print(f"\n         {success}/{len(clips)} clips exported in {elapsed:.1f}s")

    # ── Step 4b: Generate thumbnails for each clip ──
    thumb_dir = os.path.join(output_dir, "thumbnails")
    try:
        from services.thumbnail_generator import generate_variations, thumbnail_to_video_frame
        from services.asset_store import resolve as resolve_thumb_asset

        logo_for_thumb = config.get("logo_path") or None
        # Try to find a guest photo from assets
        guest_photo = None
        try:
            from services.asset_store import list_assets as list_thumb_assets
            photos = [a for a in list_thumb_assets() if a["type"] == "image" and os.path.exists(a["path"])]
            if photos:
                guest_photo = photos[0]["path"]
        except Exception:
            pass

        print(f"\n  [4b/5] Generating thumbnails...")
        for i, clip in enumerate(clips):
            clip_thumb_dir = os.path.join(thumb_dir, f"clip_{i+1}")
            paths = generate_variations(
                title=clip.get("title", f"Clip {i+1}"),
                output_dir=clip_thumb_dir,
                guest_photo_path=guest_photo,
                logo_path=logo_for_thumb,
            )
            print(f"         Clip {i+1}: {len(paths)} variations → {os.path.basename(clip_thumb_dir)}/")

            # Append thumbnail as 2-sec frame to the rendered clip
            clip_result = results[i] if i < len(results) else None
            if clip_result and clip_result.get("output_path") and paths:
                # Use first variation as default (user can re-run with specific choice)
                thumb_video = os.path.join(clip_thumb_dir, "thumb_frame.mp4")
                try:
                    thumbnail_to_video_frame(paths[0], thumb_video)
                    # Append to clip
                    from services.video_processor import concat_outro
                    final_with_thumb = clip_result["output_path"].replace(".mp4", "_with_thumb.mp4")
                    concat_outro(clip_result["output_path"], thumb_video, final_with_thumb, crossfade_duration=0.3)
                    # Replace original
                    os.replace(final_with_thumb, clip_result["output_path"])
                    print(f"                 + thumbnail appended (2s fade)")
                except Exception as e:
                    print(f"                 ⚠ thumbnail append failed: {e}")

        print(f"         Thumbnails saved to {thumb_dir}/")
    except ImportError:
        print(f"\n  [4b/5] Thumbnails skipped (pip install Pillow)")
    except Exception as e:
        print(f"\n  [4b/5] Thumbnails skipped: {e}")

    print(f"\n  Output: {output_dir}/")

    # ── Step 5: Content generation via Claude (PodStack /produce-shorts) ──
    from services.claude_suggest import _find_claude
    claude_path = _find_claude()

    accent = "\033[38;2;212;135;74m"
    gray = "\033[38;5;245m"
    green = "\033[38;2;74;222;128m"
    bold = "\033[1m"
    reset = "\033[0m"

    if claude_path:
        print(f"\n  {bold}[5/5] Generating content package (PodStack)...{reset}")

        # Build transcript text for Claude
        transcript_lines = []
        for seg in segments:
            sp = seg.get("speaker", "")
            sp_label = f"[{sp}] " if sp else ""
            start = seg.get("start", 0)
            mins = int(start) // 60
            secs = int(start) % 60
            text = seg.get("text", "").strip()
            if text:
                transcript_lines.append(f"({mins}:{secs:02d}) {sp_label}{text}")

        # Build clip summary for context
        clip_summary = "\n".join(
            f"- Short {i+1}: \"{c['title']}\" ({c['start_second']}s-{c['end_second']}s) [{c.get('content_type', '')}]"
            for i, c in enumerate(clips)
        )

        prompt = f"""Run /produce-shorts for this episode. Clips are already rendered — now generate the content package.

Video: {os.path.basename(video_path)}
Clips rendered: {success}
Output: {output_dir}/

Selected clips:
{clip_summary}

Transcript:
{chr(10).join(transcript_lines[:500])}

Generate:
1. 8 title options per clip (using knowledge base title formulas)
2. Ready-to-paste descriptions with hashtags for each clip
3. Thumbnail text briefs (podcast 16:9 + shorts 9:16) for each clip
4. Posting schedule recommendation
5. Publish checklist

Save the content package to episodes/ directory."""

        import tempfile, subprocess as sp
        project_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")

        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, dir=project_dir) as f:
            f.write(prompt)
            prompt_file = f.name

        try:
            print(f"         {gray}Claude is generating titles, descriptions, thumbnails...{reset}")
            result = sp.run(
                f'cat "{prompt_file}" | "{claude_path}" --print -p -',
                capture_output=True, text=True, cwd=project_dir,
                timeout=300, shell=True,
            )
            if result.returncode == 0 and result.stdout.strip():
                # Save content package
                import re
                guest = "episode"
                for seg in segments[:10]:
                    text = seg.get("text", "")
                    if text and len(text) > 20:
                        guest = re.sub(r'[^a-zA-Z0-9]', '-', text[:30]).strip('-').lower()
                        break

                episodes_dir = os.path.join(project_dir, "episodes")
                os.makedirs(episodes_dir, exist_ok=True)
                pkg_path = os.path.join(episodes_dir, f"content-package-{guest[:20]}.md")
                with open(pkg_path, "w") as f:
                    f.write(result.stdout)

                print(f"         {green}✓{reset} Content package saved to {accent}episodes/{os.path.basename(pkg_path)}{reset}")
            else:
                print(f"         {gray}⚠ Claude returned no content. Run {accent}/produce-shorts{reset} {gray}in Claude Code manually.{reset}")
        except Exception as e:
            print(f"         {gray}⚠ Content generation skipped: {e}{reset}")
            print(f"         {gray}Run {accent}/produce-shorts{reset} {gray}in Claude Code for titles + descriptions.{reset}")
        finally:
            try:
                os.unlink(prompt_file)
            except Exception:
                pass
    else:
        print(f"\n  {gray}Clips rendered. For titles, descriptions & thumbnails:{reset}")
        print(f"  {gray}Open Claude Code and run {accent}/produce-shorts{reset}")

    print()


def _suggest_clips(
    segments: list,
    energy_scores: list | None = None,
    top_n: int = 5,
    min_dur: float = 20,
    max_dur: float = 90,
) -> list:
    """
    Score and rank transcript segments into viral clip suggestions.

    Scoring dimensions:
    1. Hook strength — does it open with something that grabs attention?
    2. Standalone value — does it make sense without context?
    3. Completeness — does it start and end on sentence boundaries?
    4. Content signals — keywords, questions, stories, bold claims
    5. Speaker dynamics — speaker changes make clips more engaging
    6. Audio energy — louder/more passionate = more engaging
    7. Density — information per second (too sparse = boring)
    """

    # ── Signal keywords by category (weighted differently) ──

    # Strong hooks — things people say right before a great moment
    HOOK_PHRASES = [
        "here's the thing", "let me tell you", "the truth is",
        "what people don't realize", "nobody talks about",
        "the biggest mistake", "the real reason", "here's what happened",
        "i'll never forget", "that's when i realized", "the moment i knew",
        "so here's the secret", "this is the part where", "what i learned",
        "the one thing", "if i'm being honest", "the hard truth",
    ]

    # Insight signals — content that teaches or reveals
    INSIGHT_WORDS = [
        "because", "actually", "specifically", "the problem is",
        "most people", "counterintuitive", "the data shows",
        "what we found", "turns out", "the reason",
        "in practice", "the trick", "fundamentally",
    ]

    # Story signals — narrative elements that pull people in
    STORY_SIGNALS = [
        "when i", "when we", "i remember", "years ago", "at that point",
        "we decided", "we were", "i was", "the first time",
        "that morning", "one day", "back then", "suddenly",
    ]

    # Numbers/specifics — concrete details increase credibility
    NUMBER_PATTERN = None
    try:
        import re
        NUMBER_PATTERN = re.compile(r'\$[\d,.]+[mkb]?|\d+%|\d+\.\d+[x×]|\d{2,}', re.IGNORECASE)
    except Exception:
        pass

    # Sentence boundary detection
    def _is_sentence_start(text):
        """Check if text starts at a sentence boundary."""
        t = text.strip()
        if not t:
            return False
        # Starts with capital letter after nothing or after sentence-ending punct
        return t[0].isupper()

    def _is_sentence_end(text):
        """Check if text ends at a sentence boundary."""
        t = text.strip()
        if not t:
            return False
        return t[-1] in '.!?'

    def _find_sentence_boundary_start(segs, idx, max_lookback=3):
        """Walk backwards to find the nearest sentence start."""
        for offset in range(min(max_lookback, idx)):
            check_idx = idx - offset
            if check_idx <= 0:
                return 0
            prev_text = segs[check_idx - 1].get("text", "").strip()
            curr_text = segs[check_idx].get("text", "").strip()
            if prev_text and prev_text[-1] in '.!?' and curr_text and curr_text[0].isupper():
                return check_idx
        return idx

    def _find_sentence_boundary_end(segs, idx, max_lookahead=3):
        """Walk forward to find the nearest sentence end."""
        for offset in range(min(max_lookahead, len(segs) - idx)):
            check_idx = idx + offset
            check_text = segs[check_idx].get("text", "").strip()
            if check_text and check_text[-1] in '.!?':
                return check_idx
        return idx

    # ── Build clips with smart windowing ──

    clips = []
    # Multiple window sizes to catch different moment lengths
    win_sizes = [5, 7, 9, 11, 14]

    for win_size in win_sizes:
        step = max(1, int(win_size * 0.5))
        for i in range(0, len(segments) - win_size, step):
            # Snap window start and end to sentence boundaries
            snap_start = _find_sentence_boundary_start(segments, i)
            snap_end = _find_sentence_boundary_end(segments, i + win_size - 1)

            win = segments[snap_start : snap_end + 1]
            if len(win) < 3:
                continue

            text = " ".join(s.get("text", "") for s in win)
            start = win[0].get("start", 0)
            end = win[-1].get("end", 0)
            dur = end - start

            if dur < min_dur or dur > max_dur:
                continue

            text_lower = text.lower()
            score = 0
            reasons = []

            # ── 1. Hook strength (0-8 pts) ──
            # Check if clip opens with a strong hook
            first_30_chars = " ".join(s.get("text", "") for s in win[:3]).lower()
            for phrase in HOOK_PHRASES:
                if phrase in first_30_chars:
                    score += 5
                    reasons.append("strong_hook")
                    break

            # Question as opener
            first_seg_text = win[0].get("text", "")
            if "?" in first_seg_text:
                score += 3
                reasons.append("question_hook")

            # ── 2. Sentence boundary quality (0-4 pts) ──
            starts_clean = _is_sentence_start(win[0].get("text", ""))
            ends_clean = _is_sentence_end(win[-1].get("text", ""))
            if starts_clean:
                score += 2
            if ends_clean:
                score += 2
                reasons.append("clean_ending")

            # ── 3. Content signals (0-10 pts) ──
            insight_count = sum(1 for kw in INSIGHT_WORDS if kw in text_lower)
            story_count = sum(1 for kw in STORY_SIGNALS if kw in text_lower)

            if insight_count >= 2:
                score += min(insight_count * 1.5, 5)
                reasons.append("insightful")
            if story_count >= 2:
                score += min(story_count * 1.5, 5)
                reasons.append("narrative")

            # Exclamation = passion/emphasis
            exclaim_count = text.count("!")
            if exclaim_count >= 1:
                score += min(exclaim_count, 3)

            # ── 4. Specificity — numbers, dollars, percentages (0-4 pts) ──
            if NUMBER_PATTERN:
                numbers = NUMBER_PATTERN.findall(text)
                if numbers:
                    score += min(len(numbers) * 1.5, 4)
                    reasons.append("specific_numbers")

            # ── 5. Speaker dynamics (0-5 pts) ──
            speakers_in_window = set()
            speaker_changes = 0
            prev_speaker = None
            for s in win:
                sp = s.get("speaker")
                if sp:
                    speakers_in_window.add(sp)
                    if prev_speaker and sp != prev_speaker:
                        speaker_changes += 1
                    prev_speaker = sp

            if len(speakers_in_window) > 1:
                # Multi-speaker clips are more dynamic
                score += 2
                if speaker_changes >= 2:
                    score += min(speaker_changes, 3)
                    reasons.append("dialogue")

            # ── 6. Audio energy (0-6 pts) ──
            if energy_scores:
                seg_energies = energy_scores[snap_start : snap_end + 1]
                if seg_energies:
                    avg_e = sum(seg_energies) / len(seg_energies)
                    max_e = max(seg_energies)
                    # Energy variance = dynamic range (builds tension)
                    variance = sum((e - avg_e) ** 2 for e in seg_energies) / len(seg_energies)
                    energy_score = avg_e * 0.3 + max_e * 0.3 + (variance ** 0.5) * 0.4
                    score += min(energy_score, 6)
                    if max_e > 7:
                        reasons.append("high_energy")

            # ── 7. Density check — penalize sparse/rambling segments ──
            words_per_sec = len(text.split()) / max(dur, 1)
            if words_per_sec < 1.5:
                score *= 0.7  # Too sparse, probably silence or filler
            elif words_per_sec > 2.5:
                score *= 1.1  # Dense = packed with info

            # ── 8. Anti-patterns — penalize weak clips ──
            # Clips that reference other parts of the conversation
            if any(ref in text_lower for ref in [
                "as i said", "like i mentioned", "going back to",
                "earlier when", "as we discussed", "you said earlier",
            ]):
                score *= 0.5  # Needs context = bad short

            # Mid-sentence start
            if not starts_clean:
                score *= 0.8

            # ── Build title from the hook ──
            # Find the first strong sentence as the title
            title = ""
            for s in win[:4]:
                t = s.get("text", "").strip()
                if t and len(t) > 15:
                    title = t
                    break
            if not title:
                title = text[:60].strip()
            if len(title) > 55:
                # Cut at word boundary
                title = title[:55].rsplit(" ", 1)[0] + "..."

            if score >= 5:  # Higher threshold = better clips
                clips.append({
                    "title": title,
                    "start_second": round(start, 1),
                    "end_second": round(end, 1),
                    "duration": round(dur),
                    "score": round(score, 2),
                    "reasons": reasons,
                    "preview": text[:120].strip(),
                })

    # ── Deduplicate overlapping clips (keep highest score) ──
    clips.sort(key=lambda c: c["score"], reverse=True)
    selected = []
    for clip in clips:
        overlap = False
        for sel in selected:
            if (clip["start_second"] < sel["end_second"] and
                clip["end_second"] > sel["start_second"]):
                overlap_amt = (min(clip["end_second"], sel["end_second"]) -
                              max(clip["start_second"], sel["start_second"]))
                if overlap_amt > min(clip["duration"], sel["duration"]) * 0.3:
                    overlap = True
                    break
        if not overlap:
            selected.append(clip)
        if len(selected) >= top_n:
            break

    # Sort by time for natural ordering
    selected.sort(key=lambda c: c["start_second"])
    return selected


def cmd_presets(args):
    """Manage presets."""
    from presets import list_presets, get_preset, save_preset, delete_preset

    if args.presets_action == "list":
        presets = list_presets()
        if not presets:
            print("  No saved presets. Create one with: podcli presets save <name>")
            return
        print(f"\n  Saved presets ({len(presets)}):\n")
        for p in presets:
            print(f"    {p['name']}")
            print(f"      caption: {p.get('caption_style', '?')} | crop: {p.get('crop_strategy', '?')} | top: {p.get('top_clips', '?')}")
        print()

    elif args.presets_action == "save":
        config = {}
        if args.caption_style:
            config["caption_style"] = args.caption_style
        if args.crop:
            config["crop_strategy"] = args.crop
        if args.logo:
            config["logo_path"] = args.logo
        if args.top:
            config["top_clips"] = args.top
        if args.time_adjust is not None:
            config["time_adjust"] = args.time_adjust

        path = save_preset(args.name, config)
        print(f"  Preset '{args.name}' saved to {path}")

    elif args.presets_action == "delete":
        if delete_preset(args.name):
            print(f"  Preset '{args.name}' deleted")
        else:
            print(f"  Preset '{args.name}' not found")

    elif args.presets_action == "show":
        try:
            p = get_preset(args.name)
            print(f"\n  Preset: {args.name}\n")
            for k, v in p.items():
                if k != "name":
                    print(f"    {k}: {v}")
            print()
        except FileNotFoundError:
            print(f"  Preset '{args.name}' not found")


def cmd_assets(args):
    """Manage named assets (logos, intros, outros)."""
    from services.asset_store import register, unregister, list_assets, resolve

    accent = "\033[38;2;212;135;74m"
    gray = "\033[38;5;245m"
    green = "\033[38;2;74;222;128m"
    bold = "\033[1m"
    reset = "\033[0m"

    if args.assets_action == "list":
        assets = list_assets()
        if not assets:
            print(f"\n  No assets registered. Add one:")
            print(f"    {accent}podcli assets add{reset} {gray}mylogo /path/to/logo.png{reset}")
            print()
            return
        print(f"\n  {bold}Registered assets ({len(assets)}){reset}\n")
        for a in assets:
            exists = os.path.exists(a["path"])
            status = f"{green}✓{reset}" if exists else "\033[38;2;248;113;113m✗ missing\033[0m"
            print(f"    {accent}{a['name']}{reset}  {gray}({a['type']}){reset}  {status}")
            print(f"      {gray}{a['path']}{reset}")
        print()
        print(f"  {gray}Use in commands:{reset}  {accent}--logo mylogo{reset}  {gray}or{reset}  {accent}--outro myoutro{reset}")
        print()

    elif args.assets_action == "add":
        name = args.name
        file_path = args.path
        asset_type = getattr(args, "type", "auto") or "auto"
        try:
            asset = register(name, file_path, asset_type)
            print(f"\n  {green}✓{reset} Registered {accent}{name}{reset} ({asset['type']})")
            print(f"    {gray}{asset['path']}{reset}\n")
        except FileNotFoundError as e:
            print(f"\n  ✗ {e}\n", file=sys.stderr)
            sys.exit(1)

    elif args.assets_action == "remove":
        if unregister(args.name):
            print(f"\n  ✓ Removed '{args.name}'\n")
        else:
            print(f"\n  '{args.name}' not found\n")

    elif args.assets_action == "resolve":
        path = resolve(args.name)
        if path:
            print(path)
        else:
            print(f"  Not found: {args.name}", file=sys.stderr)
            sys.exit(1)


def cmd_thumbnails(args):
    """Generate thumbnail variations for a title."""
    from services.thumbnail_generator import generate_variations
    from services.asset_store import resolve as resolve_asset

    accent = "\033[38;2;212;135;74m"
    green = "\033[38;2;74;222;128m"
    gray = "\033[38;5;245m"
    bold = "\033[1m"
    reset = "\033[0m"

    logo = None
    if args.logo:
        logo = resolve_asset(args.logo)
    else:
        # Auto-use first logo asset
        try:
            from services.asset_store import list_assets
            logos = [a for a in list_assets() if a["type"] == "logo" and os.path.exists(a["path"])]
            if logos:
                logo = logos[0]["path"]
        except Exception:
            pass

    photo = None
    if args.photo:
        photo = resolve_asset(args.photo)

    print(f"\n  {bold}Generating {args.variations} thumbnail variations...{reset}")
    print(f"  Title: {accent}{args.title}{reset}")

    paths = generate_variations(
        title=args.title,
        output_dir=args.output,
        guest_photo_path=photo,
        logo_path=logo,
        config={"variations": args.variations},
    )

    for p in paths:
        print(f"  {green}✓{reset} {p}")

    print(f"\n  {gray}Open the folder to preview and pick the best one.{reset}")
    print(f"  {gray}Edit .podcli/thumbnail-config.json to customize colors, fonts, layout.{reset}\n")


def cmd_info(args):
    """Show system info."""
    from services.encoder import get_encoder_info

    info = get_encoder_info()
    print(f"\n  podcli system info\n")
    print(f"    Platform:   {info['system']}")
    print(f"    Encoder:    {info['best']}")
    print(f"    Available:  {', '.join(info['available'])}")
    print(f"    Flags:      {' '.join(info['best_flags'])}")
    print()


VERSION = "1.0.0"

BANNER = """
\033[38;2;212;135;74m  ┌─────────────────────────────────────┐
  │                                     │
  │   ██████╗  ██████╗ ██████╗          │
  │   ██╔══██╗██╔═══██╗██╔══██╗         │
  │   ██████╔╝██║   ██║██║  ██║         │
  │   ██╔═══╝ ██║   ██║██║  ██║         │
  │   ██║     ╚██████╔╝██████╔╝\033[0m\033[1m CLI\033[0m\033[38;2;212;135;74m    │
  │   ╚═╝      ╚═════╝ ╚═════╝         │
  │                                     │
  └─────────────────────────────────────┘\033[0m"""


def print_banner():
    """Print startup banner with system info."""
    from services.encoder import get_encoder_info

    print(BANNER)

    try:
        info = get_encoder_info()
        encoder = info["best"]
        if encoder == "libx264":
            encoder_label = "CPU"
        else:
            encoder_label = encoder.replace("h264_", "").upper()
    except Exception:
        encoder_label = "CPU"

    # Count knowledge base files
    kb_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".podcli", "knowledge")
    kb_count = len([f for f in os.listdir(kb_path) if f.endswith(".md")]) if os.path.isdir(kb_path) else 0

    gray = "\033[38;5;245m"
    accent = "\033[38;2;212;135;74m"
    green = "\033[38;2;74;222;128m"
    yellow = "\033[38;2;250;204;21m"
    red = "\033[38;2;248;113;113m"
    dim = "\033[2m"
    bold = "\033[1m"
    reset = "\033[0m"

    # Check HF_TOKEN for speaker diarization
    hf_token = os.environ.get("HF_TOKEN", "")
    if not hf_token:
        env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".env")
        if os.path.exists(env_path):
            with open(env_path) as f:
                for line in f:
                    if line.strip().startswith("HF_TOKEN=") and line.strip().split("=", 1)[1].strip():
                        hf_token = line.strip().split("=", 1)[1].strip()
                        break

    speakers_ok = bool(hf_token)

    # Check Claude Code
    from services.claude_suggest import _find_claude
    claude_path = _find_claude()

    print(f"  {bold}podcli{reset} v{VERSION}")

    # Status — one line
    claude_tag = f"{green}✓{reset}" if claude_path else f"{yellow}✗{reset}"
    speaker_tag = f"{green}✓{reset}" if speakers_ok else f"{yellow}✗{reset}"
    print(f"  {gray}Encoder {green}{encoder_label}{reset} {gray}· Claude {claude_tag} {gray}· Speakers {speaker_tag} {gray}· Knowledge {green}{kb_count}{reset}")

    # Assets — one line if any
    try:
        from services.asset_store import list_assets
        assets = list_assets()
        if assets:
            parts = []
            for a in assets:
                if os.path.exists(a["path"]):
                    parts.append(f"{green}✓{reset} {a['name']}")
                else:
                    parts.append(f"{red}✗{reset} {a['name']}")
            print(f"  {gray}Assets{reset}  {'  '.join(parts)}")
    except Exception:
        pass

    print()

    if not speakers_ok:
        print(f"  {yellow}⚠ Speaker detection not set up — run: podcli info{reset}")

    print()


def main():
    parser = argparse.ArgumentParser(
        prog="podcli",
        description="AI-powered podcast clip generator",
    )
    parser.add_argument("--version", action="version", version=f"podcli {VERSION}")
    parser.add_argument("--no-banner", action="store_true", help=argparse.SUPPRESS)
    sub = parser.add_subparsers(dest="command")

    # ── process ──
    proc = sub.add_parser("process", help="Process a video into clips")
    proc.add_argument("video", help="Path to podcast video file")
    proc.add_argument("-t", "--transcript", help="Path to transcript file (.txt or .json)")
    proc.add_argument("-n", "--top", type=int, help="Number of top clips to export (default: 5)")
    proc.add_argument("-o", "--output", help="Output directory (default: ./clips)")
    proc.add_argument("-p", "--preset", help="Load a saved preset")
    proc.add_argument("--caption-style", choices=["branded", "hormozi", "karaoke", "subtle"])
    proc.add_argument("--crop", choices=["center", "face"])
    proc.add_argument("--logo", help="Logo image (asset name or path)")
    proc.add_argument("--outro", help="Outro video (asset name or path)")
    proc.add_argument("--time-adjust", type=float, help="Timestamp offset in seconds")
    proc.add_argument("--no-energy", action="store_true", help="Skip audio energy analysis")
    proc.add_argument("--quality", choices=["low", "medium", "high", "max"], help="Output quality (default: high)")

    # ── presets ──
    pre = sub.add_parser("presets", help="Manage presets")
    pre_sub = pre.add_subparsers(dest="presets_action")

    pre_list = pre_sub.add_parser("list", help="List all presets")

    pre_save = pre_sub.add_parser("save", help="Save a preset")
    pre_save.add_argument("name", help="Preset name")
    pre_save.add_argument("--caption-style", choices=["branded", "hormozi", "karaoke", "subtle"])
    pre_save.add_argument("--crop", choices=["center", "face"])
    pre_save.add_argument("--logo", help="Logo path")
    pre_save.add_argument("--top", type=int, help="Default top clips count")
    pre_save.add_argument("--time-adjust", type=float)

    pre_show = pre_sub.add_parser("show", help="Show a preset")
    pre_show.add_argument("name")

    pre_del = pre_sub.add_parser("delete", help="Delete a preset")
    pre_del.add_argument("name")

    # ── assets ──
    ast = sub.add_parser("assets", help="Manage named assets (logos, intros, outros)")
    ast_sub = ast.add_subparsers(dest="assets_action")

    ast_list = ast_sub.add_parser("list", help="List all registered assets")

    ast_add = ast_sub.add_parser("add", help="Register a file as a named asset")
    ast_add.add_argument("name", help="Short name (e.g., 'mylogo', 'outro')")
    ast_add.add_argument("path", help="Path to file")
    ast_add.add_argument("--type", choices=["logo", "video", "image", "audio", "other"], help="Asset type (default: auto-detect)")

    ast_rm = ast_sub.add_parser("remove", help="Remove a named asset")
    ast_rm.add_argument("name")

    ast_resolve = ast_sub.add_parser("resolve", help="Print the path for an asset name")
    ast_resolve.add_argument("name")

    # ── thumbnails ──
    thumb = sub.add_parser("thumbnails", help="Generate thumbnail variations for a title")
    thumb.add_argument("title", help="Title text for the thumbnail")
    thumb.add_argument("-o", "--output", default="./thumbnails", help="Output directory")
    thumb.add_argument("--photo", help="Guest photo (asset name or path)")
    thumb.add_argument("--logo", help="Logo (asset name or path)")
    thumb.add_argument("-n", "--variations", type=int, default=3, help="Number of variations")

    # ── info ──
    sub.add_parser("info", help="Show system info (encoder, etc.)")

    args = parser.parse_args()

    if args.command == "process":
        if not getattr(args, "no_banner", False):
            print()
        cmd_process(args)
    elif args.command == "thumbnails":
        cmd_thumbnails(args)
    elif args.command == "presets":
        cmd_presets(args)
    elif args.command == "assets":
        cmd_assets(args)
    elif args.command == "info":
        cmd_info(args)
    else:
        interactive_menu()


def interactive_menu():
    """Interactive startup — show banner then let user pick what to do."""

    accent = "\033[38;2;212;135;74m"
    gray = "\033[38;5;245m"
    green = "\033[38;2;74;222;128m"
    yellow = "\033[38;2;250;204;21m"
    bold = "\033[1m"
    dim = "\033[2m"
    reset = "\033[0m"

    print_banner()

    print(f"  {bold}Quick start:{reset}")
    print(f"    {accent}1{reset}  Process a video → shorts + content package")
    print(f"    {accent}2{reset}  Open Web UI")
    print(f"    {accent}3{reset}  Manage assets")
    print(f"    {accent}q{reset}  Quit")
    print()

    try:
        choice = input(f"  {gray}>{reset} ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return

    if choice == "1":
        _interactive_process()
    elif choice == "2":
        print(f"\n  {gray}Starting Web UI...{reset}\n")
        import subprocess as sp
        sp.run(["npm", "run", "ui"], cwd=os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
    elif choice == "3":
        _interactive_assets()
    elif choice in ("q", "Q", ""):
        return
    else:
        print(f"\n  {gray}Unknown option.{reset}\n")


def _clean_path(val):
    """Clean a path that may have shell escapes or quotes from drag-drop."""
    val = val.strip().strip("'\"")
    # macOS Terminal adds backslash escapes when dragging files
    val = val.replace("\\ ", " ")
    val = val.replace("\\(", "(").replace("\\)", ")")
    val = val.replace("\\,", ",")
    val = val.replace("\\'", "'")
    # Also handle generic backslash-space
    if "\\" in val and not os.path.exists(val):
        unescaped = val.replace("\\", "")
        if os.path.exists(unescaped):
            return unescaped
    return val


def _flush_stdin():
    """Flush any buffered stdin (leftover newlines from previous inputs)."""
    import select
    try:
        while select.select([sys.stdin], [], [], 0.0)[0]:
            sys.stdin.readline()
    except Exception:
        pass


def _ask(prompt, default=None, validate=None, required=False, is_path=False):
    """Ask a question, retry until valid or Ctrl+C."""
    _flush_stdin()
    while True:
        try:
            val = input(prompt).strip().strip("'\"")
        except (EOFError, KeyboardInterrupt):
            print()
            return default
        if not val:
            if default is not None:
                return default
            if required:
                continue  # silently re-prompt
            return val
        if is_path:
            val = _clean_path(val)
        if validate and not validate(val):
            continue
        return val


def _interactive_process():
    """Interactive video processing wizard. Simple input() calls, no abstraction."""

    accent = "\033[38;2;212;135;74m"
    gray = "\033[38;5;245m"
    green = "\033[38;2;74;222;128m"
    red = "\033[38;2;248;113;113m"
    bold = "\033[1m"
    dim = "\033[2m"
    reset = "\033[0m"

    def _prompt(msg, default=None):
        """Simple prompt. Returns stripped input or default."""
        try:
            val = input(msg).strip().strip("'\"")
            if not val:
                return default
            return _clean_path(val) if ("/" in val or "\\" in val) else val
        except (EOFError, KeyboardInterrupt):
            print()
            return default

    # 1. Video
    print(f"\n  {bold}Drag your episode video here or paste the path:{reset}")
    video = None
    while not video:
        v = _prompt(f"  {accent}▸{reset} ")
        if v is None:
            return
        if not v:
            continue
        if os.path.exists(v):
            video = v
            print(f"  {green}✓{reset} {os.path.basename(video)}")
        else:
            print(f"  {red}✗{reset} File not found, try again")

    # 2. Transcript
    print(f"\n  {bold}Transcript{reset} {dim}(drag file here, or just press Enter to auto-transcribe):{reset}")
    t = _prompt(f"  {accent}▸{reset} ", default="")
    transcript = t if t and os.path.exists(t) else None
    if transcript:
        print(f"  {green}✓{reset} {os.path.basename(transcript)}")
    else:
        print(f"  {gray}→ Will auto-transcribe with Whisper{reset}")

    # 3. Caption style
    print(f"\n  {bold}Caption style{reset} {dim}(1-4, default 1):{reset}")
    print(f"  {accent}1{reset} branded  {dim}dark pill on active word + logo{reset}")
    print(f"  {accent}2{reset} hormozi  {dim}bold uppercase, yellow highlight{reset}")
    print(f"  {accent}3{reset} karaoke  {dim}sentence visible, words light up{reset}")
    print(f"  {accent}4{reset} subtle   {dim}clean small text at bottom{reset}")
    styles = {"1": "branded", "2": "hormozi", "3": "karaoke", "4": "subtle"}
    caption_style = styles.get(_prompt(f"  {accent}▸{reset} ", default="1") or "1", "branded")
    print(f"  {green}✓{reset} {caption_style}")

    # 4. Quality
    print(f"\n  {bold}Quality{reset} {dim}(1-4, default 3):{reset}  {dim}1 low · 2 medium · 3 high · 4 max{reset}")
    qualities = {"1": "low", "2": "medium", "3": "high", "4": "max"}
    quality = qualities.get(_prompt(f"  {accent}▸{reset} ", default="3") or "3", "high")
    print(f"  {green}✓{reset} {quality}")

    # 5. Number of clips
    print(f"\n  {bold}How many clips?{reset} {dim}(default 5):{reset}")
    try:
        top_n = int(_prompt(f"  {accent}▸{reset} ", default="5") or "5")
    except ValueError:
        top_n = 5
    print(f"  {green}✓{reset} {top_n} clips")

    # 6. Logo
    logo = None
    try:
        from services.asset_store import list_assets
        logos = [a for a in list_assets() if a["type"] == "logo" and os.path.exists(a["path"])]
        if logos:
            print(f"\n  {bold}Logo{reset} {dim}(default 1):{reset}")
            for i, a in enumerate(logos):
                print(f"  {accent}{i+1}{reset} {a['name']}  {dim}{os.path.basename(a['path'])}{reset}")
            print(f"  {accent}0{reset} none")
            lc = _prompt(f"  {accent}▸{reset} ", default="1") or "1"
            if lc != "0":
                idx = int(lc) - 1
                if 0 <= idx < len(logos):
                    logo = logos[idx]["path"]
                    print(f"  {green}✓{reset} {logos[idx]['name']}")
    except Exception:
        pass

    # 7. Outro
    outro = None
    try:
        from services.asset_store import list_assets as list_assets_outro
        outros = [a for a in list_assets_outro() if a["type"] == "video" and os.path.exists(a["path"])]
        if outros:
            print(f"\n  {bold}Outro{reset} {dim}(default 0 = none):{reset}")
            for i, a in enumerate(outros):
                print(f"  {accent}{i+1}{reset} {a['name']}  {dim}{os.path.basename(a['path'])}{reset}")
            print(f"  {accent}0{reset} none")
            oc = _prompt(f"  {accent}▸{reset} ", default="0") or "0"
            if oc != "0":
                idx = int(oc) - 1
                if 0 <= idx < len(outros):
                    outro = outros[idx]["path"]
                    print(f"  {green}✓{reset} {outros[idx]['name']}")
        else:
            print(f"\n  {bold}Outro:{reset} {dim}(drag video or press Enter to skip):{reset}")
            o = _prompt(f"  {accent}▸{reset} ", default="")
            if o:
                o = _clean_path(o)
                if os.path.exists(o):
                    outro = o
                    print(f"  {green}✓{reset} {os.path.basename(outro)}")
    except Exception:
        pass

    # Summary + confirm
    print(f"\n  {'─' * 45}")
    print(f"  {bold}Video:{reset}      {os.path.basename(video)}")
    print(f"  {bold}Style:{reset}      {caption_style}  ·  Quality: {quality}  ·  Clips: {top_n}")
    if logo:
        print(f"  {bold}Logo:{reset}       ✓")
    if outro:
        print(f"  {bold}Outro:{reset}      ✓  {dim}{os.path.basename(outro)}{reset}")
    print(f"  {bold}Transcript:{reset} {'auto (Whisper)' if not transcript else os.path.basename(transcript)}")
    try:
        input(f"\n  {green}Ready!{reset} {bold}Press Enter to start{reset} {dim}(q to cancel){reset} ")
    except (EOFError, KeyboardInterrupt):
        print(f"\n  {gray}Cancelled.{reset}")
        return

    # Run
    cmd = [sys.executable, os.path.abspath(__file__), "process", video]
    if transcript:
        cmd += ["--transcript", transcript]
    cmd += ["--caption-style", caption_style]
    cmd += ["--quality", quality]
    cmd += ["--top", str(top_n)]
    if logo:
        cmd += ["--logo", logo]
    if outro:
        cmd += ["--outro", outro]

    print(f"\n  {green}▶{reset} Starting...\n")
    sys.stdout.flush()
    sys.stderr.flush()
    os.execv(sys.executable, cmd)


def _interactive_assets():
    """Interactive asset management."""

    accent = "\033[38;2;212;135;74m"
    gray = "\033[38;5;245m"
    green = "\033[38;2;74;222;128m"
    bold = "\033[1m"
    dim = "\033[2m"
    reset = "\033[0m"

    from services.asset_store import register, list_assets

    print(f"\n  {bold}Assets{reset}")
    print(f"    {accent}1{reset}  List registered assets")
    print(f"    {accent}2{reset}  Add new asset")
    try:
        choice = input(f"  {gray}>{reset} ").strip()
    except (EOFError, KeyboardInterrupt):
        return

    if choice == "1":
        assets = list_assets()
        if not assets:
            print(f"\n  {gray}No assets. Choose 2 to add one.{reset}\n")
            return
        print()
        for a in assets:
            exists = os.path.exists(a["path"])
            icon = f"{green}✓{reset}" if exists else f"\033[38;2;248;113;113m✗{reset}"
            print(f"    {icon} {accent}{a['name']}{reset}  {gray}({a['type']}) {os.path.basename(a['path'])}{reset}")
        print()

    elif choice == "2":
        try:
            name = input(f"\n  {bold}Asset name{reset} {dim}(short, e.g. mylogo){reset}: ").strip()
            path = input(f"  {bold}File path:{reset} ").strip().strip("'\"")
            if name and path:
                asset = register(name, path)
                print(f"\n  {green}✓{reset} Registered {accent}{name}{reset} ({asset['type']})\n")
        except (EOFError, KeyboardInterrupt, FileNotFoundError) as e:
            print(f"\n  {gray}{e}{reset}\n")


if __name__ == "__main__":
    main()
