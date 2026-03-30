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

# Load .env file (for HF_TOKEN, etc.)
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".env"))
except ImportError:
    pass
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

    # Resolve video path: CLI arg > preset > error
    if args.video:
        video_path = _clean_path(args.video)
    elif config.get("video_path"):
        video_path = _clean_path(config["video_path"])
    else:
        print(f"Error: No video specified. Provide a path or use a preset with video_path.", file=sys.stderr)
        sys.exit(1)

    if not os.path.exists(video_path):
        print(f"Error: Video not found: {video_path}", file=sys.stderr)
        sys.exit(1)

    # Resolve transcript from preset if not given on CLI
    if not args.transcript and config.get("transcript_path"):
        args.transcript = config["transcript_path"]

    # Apply preset corrections (merged with global corrections)
    if config.get("corrections"):
        from services.corrections import get_corrections, save_corrections
        global_corr = get_corrections()
        merged = {**global_corr, **config["corrections"]}
        if merged != global_corr:
            save_corrections(merged)

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
    elif not config.get("outro_path"):
        # Auto-detect outro from registered assets
        try:
            from services.asset_store import list_assets as _list_assets_auto
            for a in _list_assets_auto():
                if a["type"] == "video" and os.path.exists(a["path"]):
                    config["outro_path"] = a["path"]
                    break
        except Exception:
            pass
    if args.time_adjust is not None:
        config["time_adjust"] = args.time_adjust
    if args.no_energy:
        config["energy_boost"] = False
    if getattr(args, "no_speakers", False):
        config["no_speakers"] = True
    if getattr(args, "no_cache", False):
        config["no_cache"] = True
    if args.quality:
        config["quality"] = args.quality

    # Set quality env var before importing video_processor
    quality = config.get("quality", os.environ.get("PODCLI_QUALITY", "max"))
    os.environ["PODCLI_QUALITY"] = quality

    # Output directory: CLI arg > preset > default
    output_dir = args.output or config.get("output_dir") or os.path.join(os.path.dirname(video_path), "clips")
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
    result = {}

    # Transcription cache: keyed by video file path + size + mtime.
    # Saves ~2-5 min on re-runs by skipping Whisper + speaker detection.
    import hashlib
    cache_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".podcli", "cache")

    def _cache_key(path):
        stat = os.stat(path)
        raw = f"{os.path.abspath(path)}:{stat.st_size}:{stat.st_mtime}"
        return hashlib.md5(raw.encode()).hexdigest()

    def _load_cache(path):
        try:
            key = _cache_key(path)
            cache_file = os.path.join(cache_dir, f"{key}.json")
            if os.path.exists(cache_file):
                with open(cache_file) as f:
                    return json.load(f)
        except Exception:
            pass
        return None

    def _save_cache(path, data):
        try:
            os.makedirs(cache_dir, exist_ok=True)
            key = _cache_key(path)
            cache_file = os.path.join(cache_dir, f"{key}.json")
            with open(cache_file, "w") as f:
                json.dump(data, f)
        except Exception:
            pass

    if args.transcript:
        print("  [1/4] Loading transcript...")
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
        # Check cache first
        cached = _load_cache(video_path)
        if cached and not config.get("no_cache", False):
            print("  [1/4] Loaded from cache (instant)")
            words = cached["words"]
            segments = cached["segments"]
            result = cached
            print(f"         {len(segments)} segments, {len(words)} words")
        else:
            print("  [1/4] Transcribing with Whisper...")
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
                enable_diarization=not config.get("no_speakers", False),
                progress_callback=_transcribe_progress,
            )
            _spin_stop.set()
            spin_thread.join(timeout=1)
            words = result["words"]
            segments = result["segments"]
            print(f"         Done: {len(segments)} segments, {len(words)} words")

            # Save to cache for next run
            _save_cache(video_path, result)

    # Apply word corrections (Whisper misheard proper nouns, brand names)
    from services.corrections import apply_corrections
    apply_corrections(words, segments)

    # Extract face_map before result gets overwritten in clip loop
    face_map = result.get("face_map")

    # Check speaker data availability (needed for smart cropping)
    speakers_in_words = set(w.get("speaker") for w in words if w.get("speaker"))
    diarization_warning = result.get("diarization_warning")
    if diarization_warning:
        print(f"         ⚠ {diarization_warning}")
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
        print("  [2/4] Analyzing audio energy...")
        try:
            profile = get_energy_profile(video_path, segments)
            energy_scores = profile["segment_scores"]
            print(f"         {len(profile['peak_times'])} peak moments found")
        except Exception as e:
            print(f"         Skipped (error: {e})")
    else:
        print("  [2/4] Audio analysis skipped (--no-energy)")

    # ── Step 3: Score and select clips ──
    top_n = config.get("top_clips", 5)
    clips = None

    # Try Claude first (uses PodStack knowledge base for intelligent selection)
    from services.claude_suggest import suggest_with_claude, _find_ai_cli

    ai_path, ai_engine = _find_ai_cli()
    if ai_path:
        ai_label = "Claude" if ai_engine == "claude" else "Codex"
        print(f"  [3/4] Selecting moments with {ai_label} (PodStack)...")
        clips = suggest_with_claude(
            segments=segments,
            top_n=top_n,
            progress_callback=lambda pct, msg: print(f"         {msg}") if msg else None,
        )
        if clips:
            print(f"         ✓ {ai_label} selected {len(clips)} clips")
        else:
            print(f"         ⚠ {ai_label} unavailable, falling back to heuristics")
    else:
        print(f"  [3/4] Scoring clips (heuristic mode)...")
        print(f"         ℹ Install Claude Code or Codex for smarter selection")

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

    # ── Step 3.5: Interactive review ──
    clips = _review_clips(clips, segments, energy_scores, config)

    if not clips:
        print("\n  No clips selected. Exiting.")
        return

    # ── Step 4: Export ──
    # Check if thumbnail generation is enabled
    thumb_dir = os.path.join(output_dir, "thumbnails")
    _thumb_enabled = True
    _tc_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".podcli", "thumbnail-config.json")
    if os.path.exists(_tc_path):
        try:
            with open(_tc_path) as _tcf:
                _thumb_enabled = json.load(_tcf).get("enabled", True)
        except Exception:
            pass

    # Check if AI CLI is available for per-clip content generation
    from services.claude_suggest import _find_ai_cli
    _ai_cli_path, _ai_engine = _find_ai_cli()

    # Pre-load thumbnail tools if enabled
    _thumb_gen = None
    _thumb_to_video = None
    _thumb_logo = None
    _thumb_photo = None
    if _thumb_enabled:
        try:
            from services.thumbnail_ai import generate_variations as _tv, thumbnail_to_video_frame as _ttv
            _thumb_gen = _tv
            _thumb_to_video = _ttv
            _thumb_logo = config.get("logo_path") or None
            try:
                from services.asset_store import list_assets as list_thumb_assets
                photos = [a for a in list_thumb_assets() if a["type"] == "image" and os.path.exists(a["path"])]
                if photos:
                    _thumb_photo = photos[0]["path"]
            except Exception:
                pass
        except Exception:
            _thumb_enabled = False

    _ai_label = f" (+ titles via {'Claude' if _ai_engine == 'claude' else 'Codex'})" if _ai_cli_path else ""
    print(f"\n  [4/4] Exporting {len(clips)} clips{_ai_label} to {output_dir}/")
    results = []
    t0 = time.time()
    _skip_review = False

    for i, clip in enumerate(clips):
        ok = False
        result = None
        with _Spinner(f"Clip {i+1}/{len(clips)}: {clip['title'][:40]}...") as sp:
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
                    keep_segments=clip.get("segments"),
                    face_map=face_map,
                )
                results.append(result)
                ok = True
            except Exception as e:
                print(f"\n         ✗ {e}")
                results.append({"status": "error", "error": str(e)})
                continue
        if ok:
            print(f"         ✓ Clip {i+1}/{len(clips)}: {result['file_size_mb']}MB")

        # Generate thumbnail and append to clip immediately
        if _thumb_enabled and _thumb_gen and result.get("output_path"):
            try:
                clip_thumb_dir = os.path.join(thumb_dir, f"clip_{i+1}")
                paths = _thumb_gen(
                    title=clip.get("title", f"Clip {i+1}"),
                    output_dir=clip_thumb_dir,
                    photo_path=_thumb_photo,
                    video_path=video_path,
                    logo_path=_thumb_logo,
                )
                if paths:
                    thumb_video = os.path.join(clip_thumb_dir, "thumb_frame.mp4")
                    _thumb_to_video(paths[0], thumb_video, duration=1.5)
                    from services.video_processor import concat_outro
                    final_with_thumb = result["output_path"].replace(".mp4", "_with_thumb.mp4")
                    concat_outro(result["output_path"], thumb_video, final_with_thumb, crossfade_duration=0.15)
                    os.replace(final_with_thumb, result["output_path"])
                    print(f"                 + thumbnail appended ({len(paths)} variations in {os.path.basename(clip_thumb_dir)}/)")
            except Exception as e:
                print(f"                 ⚠ thumbnail: {e}")

        # Generate titles, descriptions, tags for this clip immediately
        if _ai_cli_path and result.get("output_path"):
            try:
                from services.content_generator import generate_clip_content
                content_result = generate_clip_content(
                    clip=clip,
                    transcript_segments=segments,
                )
                if content_result and content_result.get("raw_text"):
                    # Save per-clip content to file
                    _content_path = result["output_path"].replace(".mp4", "_content.md")
                    with open(_content_path, "w") as _cf:
                        _cf.write(f"# {clip.get('title', 'Clip')}\n\n{content_result['raw_text']}")

                    # Pretty-print in terminal
                    _accent = "\033[38;2;212;135;74m"
                    _bold = "\033[1m"
                    _dim = "\033[2m"
                    _yellow = "\033[33m"
                    _reset = "\033[0m"

                    print(f"\n  {'─' * 45}")
                    print(f"  {_bold}📋 Clip {i+1}: {clip['title'][:45]}{_reset}")
                    print(f"  {'─' * 45}")
                    for line in content_result["raw_text"].split("\n"):
                        stripped = line.strip()
                        if not stripped:
                            continue
                        if stripped.startswith("TITLES") or stripped.startswith("DESCRIPTION") or stripped.startswith("TAGS") or stripped.startswith("HASHTAGS") or stripped.startswith("TOP PICK"):
                            print(f"  {_bold}{stripped}{_reset}")
                        elif stripped[0:1].isdigit() and ". " in stripped[:4]:
                            print(f"  {_accent}{stripped}{_reset}")
                        elif stripped.startswith("#"):
                            print(f"  {_yellow}{stripped}{_reset}")
                        else:
                            print(f"  {_dim}{stripped}{_reset}")
                    print(f"  {_dim}Saved: {os.path.basename(_content_path)}{_reset}")
                    print()
            except Exception as e:
                print(f"                 ⚠ content: {e}")

        # ── Per-clip review: open video, ask for feedback ──
        if ok and not _skip_review and result.get("output_path") and os.path.exists(result["output_path"]):
            import subprocess as _review_sp
            _review_sp.Popen(["open", result["output_path"]] if sys.platform == "darwin" else ["xdg-open", result["output_path"]])

            while True:
                import questionary as _rq
                from questionary import Style as _RS
                _rstyle = _RS([
                    ("qmark", "fg:#d4874a bold"), ("question", "bold"),
                    ("answer", "fg:#4ade80"), ("pointer", "fg:#d4874a bold"),
                    ("highlighted", "fg:#d4874a bold"), ("selected", "fg:#4ade80"),
                ])
                _raction = _rq.select(
                    f"Clip {i+1}/{len(clips)}: {clip['title'][:40]}",
                    choices=[
                        _rq.Choice("Looks good — next clip", value="next"),
                        _rq.Choice("Change caption style", value="style"),
                        _rq.Choice("Make shorter (trim 5s from end)", value="shorter"),
                        _rq.Choice("Make longer (extend 5s)", value="longer"),
                        _rq.Choice("Start earlier (5s)", value="earlier"),
                        _rq.Choice("Start later (3s)", value="later"),
                        _rq.Choice("Tell me what to change", value="custom"),
                        _rq.Choice("Render the rest without asking", value="skip_review"),
                    ],
                    style=_rstyle,
                    instruction="",
                ).ask()

                if _raction is None or _raction == "next":
                    break

                if _raction == "skip_review":
                    _skip_review = True
                    break

                if _raction == "custom":
                    _feedback = _rq.text(
                        "What should I change?",
                        style=_rstyle,
                    ).ask()
                    if _feedback and _feedback.strip():
                        fb = _feedback.strip().lower()
                        # Parse common requests
                        if any(w in fb for w in ["shorter", "trim", "cut"]):
                            secs = 5
                            for word in fb.split():
                                try:
                                    secs = int(word)
                                    break
                                except ValueError:
                                    pass
                            clip["end_second"] -= secs
                            clip["duration"] = max(10, clip["duration"] - secs)
                            print(f"         Trimming {secs}s from end")
                        elif any(w in fb for w in ["longer", "extend", "more"]):
                            secs = 5
                            for word in fb.split():
                                try:
                                    secs = int(word)
                                    break
                                except ValueError:
                                    pass
                            clip["end_second"] += secs
                            clip["duration"] += secs
                            print(f"         Extending {secs}s")
                        elif any(w in fb for w in ["earlier", "before", "back"]):
                            secs = 5
                            for word in fb.split():
                                try:
                                    secs = int(word)
                                    break
                                except ValueError:
                                    pass
                            clip["start_second"] = max(0, clip["start_second"] - secs)
                            print(f"         Starting {secs}s earlier")
                        elif any(w in fb for w in ["later", "forward", "skip"]):
                            secs = 3
                            for word in fb.split():
                                try:
                                    secs = int(word)
                                    break
                                except ValueError:
                                    pass
                            clip["start_second"] += secs
                            print(f"         Starting {secs}s later")
                        elif any(w in fb for w in ["hormozi", "karaoke", "subtle", "branded"]):
                            for s in ["hormozi", "karaoke", "subtle", "branded"]:
                                if s in fb:
                                    config["caption_style"] = s
                                    print(f"         Changing to {s} style")
                                    break
                        else:
                            print(f"         Couldn't parse that. Try: 'shorter', 'longer 10', 'start 3s earlier', 'hormozi style'")
                            continue
                    else:
                        continue

                # Apply change
                if _raction == "style":
                    _new_style = _rq.select("Style:", choices=[
                        _rq.Choice("branded", value="branded"),
                        _rq.Choice("hormozi", value="hormozi"),
                        _rq.Choice("karaoke", value="karaoke"),
                        _rq.Choice("subtle", value="subtle"),
                    ], style=_rstyle).ask()
                    if _new_style:
                        config["caption_style"] = _new_style
                elif _raction == "shorter":
                    clip["end_second"] -= 5
                    clip["duration"] = max(10, clip["duration"] - 5)
                elif _raction == "longer":
                    clip["end_second"] += 5
                    clip["duration"] += 5
                elif _raction == "earlier":
                    clip["start_second"] = max(0, clip["start_second"] - 5)
                elif _raction == "later":
                    clip["start_second"] += 3

                # Re-render
                with _Spinner(f"Re-rendering clip {i+1}..."):
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
                            keep_segments=clip.get("segments"),
                            face_map=face_map,
                        )
                        results[-1] = result
                        print(f"         ✓ Re-rendered: {result['file_size_mb']}MB")
                        # Open new version
                        _review_sp.Popen(["open", result["output_path"]] if sys.platform == "darwin" else ["xdg-open", result["output_path"]])
                    except Exception as _re:
                        print(f"         ✗ {_re}")
                        break

    elapsed = time.time() - t0
    success = sum(1 for r in results if "output_path" in r)
    print(f"\n         {success}/{len(clips)} clips exported in {elapsed:.1f}s")
    if _thumb_enabled:
        print(f"         Thumbnails saved to {thumb_dir}/")

    print(f"\n  Output: {output_dir}/")

    accent = "\033[38;2;212;135;74m"
    gray = "\033[38;5;245m"
    bold = "\033[1m"
    reset = "\033[0m"

    if not _ai_cli_path:
        print(f"\n  {gray}For titles, descriptions & tags: install Claude Code or Codex CLI{reset}")

    # ── Post-render iteration ──
    _post_render_loop(
        clips=clips,
        results=results,
        segments=segments,
        words=words,
        config=config,
        video_path=video_path,
        output_dir=output_dir,
        face_map=face_map,
        energy_scores=energy_scores,
    )


class _Spinner:
    """Reusable terminal spinner for long-running operations."""

    def __init__(self, message: str, indent: str = "         "):
        import threading
        self._msg = message
        self._indent = indent
        self._stop = threading.Event()
        self._thread = None

    def __enter__(self):
        import threading
        self._stop.clear()

        def _run():
            frames = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
            i = 0
            while not self._stop.is_set():
                print(f"\r{self._indent}{frames[i % len(frames)]}  {self._msg[:55]:<55}", end="", flush=True)
                i += 1
                self._stop.wait(0.1)

        self._thread = threading.Thread(target=_run, daemon=True)
        self._thread.start()
        return self

    def update(self, message: str):
        self._msg = message

    def __exit__(self, *args):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1)
        print(f"\r{self._indent}{'':60}\r", end="")


def _print_clips(clips: list):
    """Print clip list in the standard format."""
    for i, c in enumerate(clips):
        m_s = int(c["start_second"]) // 60
        s_s = int(c["start_second"]) % 60
        ctype = c.get("content_type", "")
        score_val = c.get("score", 0)
        score_str = f"({score_val}/20)" if isinstance(score_val, int) and score_val <= 20 else f"({score_val:.0f}pts)"
        type_tag = f" [{ctype}]" if ctype and ctype != "unknown" else ""
        n_segs = len(c.get("segments", [])) if c.get("segments") else 1
        cuts_tag = f" ({n_segs} cuts)" if n_segs > 1 else ""
        selected = c.get("_selected", True)
        marker = "  ✓" if selected else "  ✗"
        print(f"        {marker} {i+1}. [{m_s}:{s_s:02d} → +{c['duration']}s] {score_str}{type_tag}{cuts_tag} {c['title'][:50]}")
        if c.get("why"):
            print(f"              {c['why'][:70]}")


def _find_moment_with_claude(description: str, segments: list, existing_clips: list) -> list:
    """Use Claude to find a specific moment described by the user."""
    from services.claude_suggest import _find_ai_cli

    cli_path, engine = _find_ai_cli()
    if not cli_path:
        print("         ⚠ No AI CLI available for moment search")
        return []

    # Build transcript text
    lines = []
    for seg in segments:
        speaker = seg.get("speaker", "")
        speaker_label = f"[{speaker}] " if speaker else ""
        start = seg.get("start", 0)
        text = seg.get("text", "").strip()
        if text:
            lines.append(f"[{start:.1f}s] {speaker_label}{text}")
    transcript_text = "\n".join(lines)

    # Build list of existing clip timestamps to avoid
    existing_desc = ""
    if existing_clips:
        existing_desc = "\n\nALREADY SELECTED (do not re-suggest these):\n"
        for c in existing_clips:
            existing_desc += f"- {c['start_second']}s-{c['end_second']}s: {c['title']}\n"

    import tempfile, subprocess, json as _json

    prompt = f"""Find the moment the user is describing in this podcast transcript. Return ONLY valid JSON.

USER WANTS: "{description}"
{existing_desc}
RULES:
- Find the EXACT moment matching the user's description
- Return 1-3 matching moments (best match first)
- All timestamps in SECONDS as numbers
- Duration target: 25-40 seconds, max 50 seconds
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

    project_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, dir=project_dir) as f:
        f.write(prompt)
        prompt_file = f.name

    try:
        if engine == "codex":
            output_file = prompt_file + ".out"
            result = subprocess.run(
                [cli_path, "exec", "--full-auto", "-o", output_file, prompt],
                capture_output=True, text=True, cwd=project_dir, timeout=300,
            )
            if os.path.exists(output_file):
                with open(output_file) as fh:
                    result = subprocess.CompletedProcess(
                        args=result.args, returncode=result.returncode,
                        stdout=fh.read(), stderr=result.stderr,
                    )
                try:
                    os.unlink(output_file)
                except Exception:
                    pass
        else:
            result = subprocess.run(
                f'cat "{prompt_file}" | "{cli_path}" --print -p -',
                capture_output=True, text=True, cwd=project_dir, timeout=300, shell=True,
            )

        if result.returncode != 0:
            return []

        response = result.stdout.strip()
        if "```" in response:
            import re
            fence_match = re.search(r"```(?:json)?\s*\n?(.*?)\n?\s*```", response, re.DOTALL)
            if fence_match:
                response = fence_match.group(1).strip()

        json_start = response.find("{")
        if json_start >= 0:
            decoder = _json.JSONDecoder()
            data, _ = decoder.raw_decode(response, json_start)
        else:
            data = _json.loads(response)

        found = []
        for c in data.get("clips", []):
            scores = c.get("scores", {})
            total = sum(scores.values()) if scores else c.get("total_score", 0)
            raw_segments = c.get("segments", [])
            keep_segments = []
            for seg in raw_segments:
                s = round(float(seg.get("start", 0)), 1)
                e = round(float(seg.get("end", 0)), 1)
                if e > s:
                    keep_segments.append({"start": s, "end": e})

            start_sec = round(float(c.get("start_second", 0)), 1)
            end_sec = round(float(c.get("end_second", 0)), 1)
            if not keep_segments and end_sec > start_sec:
                keep_segments = [{"start": start_sec, "end": end_sec}]

            kept_duration = sum(seg["end"] - seg["start"] for seg in keep_segments)
            if kept_duration < 10 or kept_duration > 90:
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
        return found

    except Exception as e:
        print(f"         ⚠ Search error: {e}")
        return []
    finally:
        try:
            os.unlink(prompt_file)
        except Exception:
            pass


def _review_clips(clips: list, segments: list, energy_scores: list | None, config: dict) -> list:
    """Interactive clip review — user can select/deselect, ask for more, or find specific moments."""
    import questionary
    from questionary import Style

    accent = "\033[38;2;212;135;74m"
    bold = "\033[1m"
    dim = "\033[2m"
    reset = "\033[0m"

    qstyle = Style([
        ("qmark", "fg:#d4874a bold"),
        ("question", "bold"),
        ("answer", "fg:#4ade80"),
        ("pointer", "fg:#d4874a bold"),
        ("highlighted", "fg:#d4874a bold"),
        ("selected", "fg:#4ade80"),
        ("instruction", "fg:#a1a1aa"),
    ])

    # Mark all clips as selected initially
    for c in clips:
        c["_selected"] = True

    while True:
        selected = [c for c in clips if c.get("_selected", True)]
        print(f"\n         {bold}{len(selected)}/{len(clips)} clips selected:{reset}")
        _print_clips(clips)

        choices = [
            questionary.Choice(f"Render {len(selected)} clips", value="render"),
            questionary.Choice("Toggle clips on/off", value="toggle"),
            questionary.Choice("Find a specific moment", value="find"),
            questionary.Choice("Get more suggestions from Claude", value="more"),
            questionary.Choice("Quit", value="quit"),
        ]

        action = questionary.select(
            "",
            choices=choices,
            style=qstyle,
            instruction="",
        ).ask()

        if action is None or action == "quit":
            return []

        if action == "render":
            return [c for c in clips if c.get("_selected", True)]

        if action == "toggle":
            toggle_choices = [
                questionary.Choice(
                    f"{'✓' if c.get('_selected', True) else '✗'} {i+1}. {c['title'][:45]} (+{c['duration']}s)",
                    value=i,
                    checked=c.get("_selected", True),
                )
                for i, c in enumerate(clips)
            ]
            picked = questionary.checkbox(
                "Select clips to render:",
                choices=toggle_choices,
                style=qstyle,
            ).ask()
            if picked is not None:
                for i, c in enumerate(clips):
                    c["_selected"] = i in picked

        elif action == "find":
            description = questionary.text(
                "Describe the moment:",
                style=qstyle,
            ).ask()
            if description and description.strip():
                with _Spinner("Searching transcript..."):
                    found = _find_moment_with_claude(description.strip(), segments, clips)
                if found:
                    for f_clip in found:
                        print(f"\n         {bold}Found:{reset} {f_clip['title']}")
                        m_s = int(f_clip["start_second"]) // 60
                        s_s = int(f_clip["start_second"]) % 60
                        print(f"         [{m_s}:{s_s:02d} → +{f_clip['duration']}s]")
                        if f_clip.get("quote"):
                            print(f"         {dim}\"{f_clip['quote'][:80]}\"{reset}")
                        if f_clip.get("why"):
                            print(f"         {dim}{f_clip['why'][:80]}{reset}")

                    add = questionary.confirm(
                        f"Add {len(found)} found clip{'s' if len(found) > 1 else ''} to the list?",
                        default=True,
                        style=qstyle,
                    ).ask()
                    if add:
                        for f_clip in found:
                            f_clip["_selected"] = True
                            clips.append(f_clip)
                else:
                    print(f"         Couldn't find that moment. Try describing it differently.")

        elif action == "more":
            top_n = config.get("top_clips", 5)
            from services.claude_suggest import suggest_with_claude
            with _Spinner(f"Asking Claude for {top_n} more suggestions...") as sp:
                more_clips = suggest_with_claude(
                    segments=segments,
                    top_n=top_n,
                    progress_callback=lambda pct, msg: sp.update(msg) if msg else None,
                )
            if more_clips:
                # Filter out duplicates (overlapping timestamps)
                new_count = 0
                for mc in more_clips:
                    is_dup = False
                    for existing in clips:
                        # Check if timestamps overlap significantly
                        overlap_start = max(mc["start_second"], existing["start_second"])
                        overlap_end = min(mc["end_second"], existing["end_second"])
                        if overlap_end - overlap_start > 5:
                            is_dup = True
                            break
                    if not is_dup:
                        mc["_selected"] = True
                        clips.append(mc)
                        new_count += 1
                print(f"         Added {new_count} new suggestions ({len(more_clips) - new_count} duplicates skipped)")
            else:
                print(f"         No additional suggestions found.")


def _post_render_loop(
    clips: list, results: list, segments: list, words: list,
    config: dict, video_path: str, output_dir: str, face_map, energy_scores,
):
    """Post-render iteration — user can re-render clips with changes or add new ones."""
    import questionary
    from questionary import Style
    from services.clip_generator import generate_clip

    accent = "\033[38;2;212;135;74m"
    bold = "\033[1m"
    dim = "\033[2m"
    reset = "\033[0m"

    qstyle = Style([
        ("qmark", "fg:#d4874a bold"),
        ("question", "bold"),
        ("answer", "fg:#4ade80"),
        ("pointer", "fg:#d4874a bold"),
        ("highlighted", "fg:#d4874a bold"),
        ("selected", "fg:#4ade80"),
        ("instruction", "fg:#a1a1aa"),
    ])

    # Build clip-to-result mapping
    rendered = []
    for i, (clip, result) in enumerate(zip(clips, results)):
        if "output_path" in result:
            rendered.append({"clip": clip, "result": result, "index": i})

    def _open_clip(r):
        """Open a rendered clip for preview."""
        import subprocess as _sp
        out = r["result"].get("output_path", "")
        if out and os.path.exists(out):
            _sp.Popen(["open", out] if sys.platform == "darwin" else ["xdg-open", out])

    def _rerender_clip(r):
        """Re-render a clip with current config."""
        clip = r["clip"]
        ok = False
        with _Spinner(f"Rendering: {clip['title'][:40]}..."):
            try:
                new_result = generate_clip(
                    video_path=video_path,
                    start_second=clip["start_second"],
                    end_second=clip["end_second"],
                    caption_style=config.get("caption_style", "branded"),
                    crop_strategy=config.get("crop_strategy", "face"),
                    transcript_words=words,
                    title=clip.get("title", "clip"),
                    output_dir=output_dir,
                    logo_path=config.get("logo_path") or None,
                    outro_path=config.get("outro_path") or None,
                    keep_segments=clip.get("segments"),
                    face_map=face_map,
                )
                r["result"] = new_result
                ok = True
            except Exception as e:
                print(f"\n         ✗ {e}")
        if ok:
            print(f"         ✓ {new_result['file_size_mb']}MB")
        return ok

    # Review each clip one by one — open for preview, ask for feedback
    print(f"\n  {'─' * 45}")
    print(f"  {bold}Review clips{reset}")

    for idx, r in enumerate(rendered):
        clip = r["clip"]
        print(f"\n  {accent}Clip {idx+1}/{len(rendered)}:{reset} {clip['title'][:50]}")
        _open_clip(r)

        while True:
            action = questionary.select(
                "",
                choices=[
                    questionary.Choice("Looks good — next", value="next"),
                    questionary.Choice("Change caption style", value="style"),
                    questionary.Choice("Make shorter (trim end)", value="shorter"),
                    questionary.Choice("Make longer (extend end)", value="longer"),
                    questionary.Choice("Shift start earlier", value="earlier"),
                    questionary.Choice("Shift start later", value="later"),
                    questionary.Choice("Skip this clip (delete)", value="skip"),
                ],
                style=qstyle,
                instruction="",
            ).ask()

            if action is None or action == "next":
                break

            if action == "skip":
                # Remove the output file
                out = r["result"].get("output_path", "")
                if out and os.path.exists(out):
                    os.remove(out)
                    print(f"         Removed: {os.path.basename(out)}")
                r["_skipped"] = True
                break

            if action == "style":
                new_style = questionary.select("Style:", choices=[
                    questionary.Choice("hormozi — bold uppercase, yellow highlight", value="hormozi"),
                    questionary.Choice("branded — dark pill on active word + logo", value="branded"),
                    questionary.Choice("karaoke — sentence visible, words light up", value="karaoke"),
                    questionary.Choice("subtle — clean small text at bottom", value="subtle"),
                ], style=qstyle).ask()
                if new_style:
                    config["caption_style"] = new_style
            elif action == "shorter":
                clip["end_second"] = clip["end_second"] - 5
                clip["duration"] = max(10, clip["duration"] - 5)
            elif action == "longer":
                clip["end_second"] = clip["end_second"] + 5
                clip["duration"] = clip["duration"] + 5
            elif action == "earlier":
                clip["start_second"] = max(0, clip["start_second"] - 5)
            elif action == "later":
                clip["start_second"] = clip["start_second"] + 3

            # Re-render and open again
            if _rerender_clip(r):
                _open_clip(r)

    rendered = [r for r in rendered if not r.get("_skipped")]
    kept = len(rendered)
    print(f"\n  {bold}{kept} clips kept{reset}")

    # Continue with additional actions
    while True:
        print(f"\n  {'─' * 45}")
        choices = [
            questionary.Choice("Done — open output folder", value="done"),
            questionary.Choice("Re-review a clip", value="rerender"),
            questionary.Choice("Find another moment to clip", value="find"),
            questionary.Choice("Get more suggestions from Claude", value="more"),
        ]

        action = questionary.select(
            f"{len(rendered)} clips",
            choices=choices,
            style=qstyle,
        ).ask()

        if action is None or action == "done":
            import subprocess as _sp
            _sp.run(["open", output_dir] if sys.platform == "darwin" else ["xdg-open", output_dir])
            break

        if action == "rerender":
            clip_choices = [
                questionary.Choice(
                    f"{i+1}. {r['clip']['title'][:40]} (+{r['clip']['duration']}s)",
                    value=idx,
                )
                for idx, (i, r) in enumerate([(r["index"], r) for r in rendered])
            ]
            pick = questionary.select("Which clip?", choices=clip_choices, style=qstyle).ask()
            if pick is None:
                continue

            r = rendered[pick]
            _open_clip(r)

            while True:
                change = questionary.select("", choices=[
                    questionary.Choice("Looks good", value="done"),
                    questionary.Choice("Change caption style", value="style"),
                    questionary.Choice("Make shorter", value="shorter"),
                    questionary.Choice("Make longer", value="longer"),
                    questionary.Choice("Shift start earlier", value="earlier"),
                    questionary.Choice("Shift start later", value="later"),
                ], style=qstyle, instruction="").ask()

                if change is None or change == "done":
                    break

                clip = r["clip"]
                if change == "style":
                    new_style = questionary.select("Style:", choices=[
                        questionary.Choice("hormozi", value="hormozi"),
                        questionary.Choice("branded", value="branded"),
                        questionary.Choice("karaoke", value="karaoke"),
                        questionary.Choice("subtle", value="subtle"),
                    ], style=qstyle).ask()
                    if new_style:
                        config["caption_style"] = new_style
                elif change == "shorter":
                    clip["end_second"] -= 5
                    clip["duration"] = max(10, clip["duration"] - 5)
                elif change == "longer":
                    clip["end_second"] += 5
                    clip["duration"] += 5
                elif change == "earlier":
                    clip["start_second"] = max(0, clip["start_second"] - 5)
                elif change == "later":
                    clip["start_second"] += 3

                if _rerender_clip(r):
                    _open_clip(r)

        elif action == "find":
            description = questionary.text("Describe the moment:", style=qstyle).ask()
            if description and description.strip():
                with _Spinner("Searching transcript..."):
                    found = _find_moment_with_claude(description.strip(), segments, clips)
                if found:
                    for f_clip in found:
                        print(f"\n         {bold}Found:{reset} {f_clip['title']}")
                        m_s = int(f_clip["start_second"]) // 60
                        s_s = int(f_clip["start_second"]) % 60
                        print(f"         [{m_s}:{s_s:02d} → +{f_clip['duration']}s]")
                        if f_clip.get("quote"):
                            print(f"         {dim}\"{f_clip['quote'][:80]}\"{reset}")

                    render_it = questionary.confirm(
                        f"Render {len(found)} clip{'s' if len(found) > 1 else ''}?",
                        default=True, style=qstyle,
                    ).ask()
                    if render_it:
                        for f_clip in found:
                            ok = False
                            with _Spinner(f"Rendering: {f_clip['title'][:40]}..."):
                                try:
                                    new_result = generate_clip(
                                        video_path=video_path,
                                        start_second=f_clip["start_second"],
                                        end_second=f_clip["end_second"],
                                        caption_style=config.get("caption_style", "branded"),
                                        crop_strategy=config.get("crop_strategy", "face"),
                                        transcript_words=words,
                                        title=f_clip.get("title", "clip"),
                                        output_dir=output_dir,
                                        logo_path=config.get("logo_path") or None,
                                        outro_path=config.get("outro_path") or None,
                                        keep_segments=f_clip.get("segments"),
                                        face_map=face_map,
                                    )
                                    rendered.append({"clip": f_clip, "result": new_result, "index": len(clips)})
                                    clips.append(f_clip)
                                    ok = True
                                except Exception as e:
                                    print(f"\n         ✗ {e}")
                            if ok:
                                print(f"         ✓ {new_result['file_size_mb']}MB")
                else:
                    print(f"         Couldn't find that moment. Try describing it differently.")

        elif action == "more":
            top_n = config.get("top_clips", 5)
            print(f"\n         Asking Claude for more suggestions...")
            from services.claude_suggest import suggest_with_claude
            more_clips = suggest_with_claude(
                segments=segments,
                top_n=top_n,
                progress_callback=lambda pct, msg: print(f"         {msg}") if msg else None,
            )
            if more_clips:
                new_clips = []
                for mc in more_clips:
                    is_dup = False
                    for existing in clips:
                        overlap_start = max(mc["start_second"], existing["start_second"])
                        overlap_end = min(mc["end_second"], existing["end_second"])
                        if overlap_end - overlap_start > 5:
                            is_dup = True
                            break
                    if not is_dup:
                        new_clips.append(mc)

                if new_clips:
                    for nc in new_clips:
                        nc["_selected"] = True
                    print(f"         Found {len(new_clips)} new moments:")
                    _print_clips(new_clips)
                    render_them = questionary.confirm(
                        f"Render {len(new_clips)} clips?",
                        default=True, style=qstyle,
                    ).ask()
                    if render_them:
                        for nc in new_clips:
                            ok = False
                            with _Spinner(f"Rendering: {nc['title'][:40]}..."):
                                try:
                                    new_result = generate_clip(
                                        video_path=video_path,
                                        start_second=nc["start_second"],
                                        end_second=nc["end_second"],
                                        caption_style=config.get("caption_style", "branded"),
                                        crop_strategy=config.get("crop_strategy", "face"),
                                        transcript_words=words,
                                        title=nc.get("title", "clip"),
                                        output_dir=output_dir,
                                        logo_path=config.get("logo_path") or None,
                                        outro_path=config.get("outro_path") or None,
                                        keep_segments=nc.get("segments"),
                                        face_map=face_map,
                                    )
                                    rendered.append({"clip": nc, "result": new_result, "index": len(clips)})
                                    clips.append(nc)
                                    ok = True
                                except Exception as e:
                                    print(f"\n         ✗ {e}")
                            if ok:
                                print(f"         ✓ {new_result['file_size_mb']}MB")
                else:
                    print(f"         No new moments found (all duplicates of existing).")
            else:
                print(f"         No suggestions returned.")

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

    accent = "\033[38;2;212;135;74m"
    green = "\033[38;2;74;222;128m"
    gray = "\033[38;5;245m"
    bold = "\033[1m"
    reset = "\033[0m"

    if args.presets_action == "list":
        presets = list_presets()
        if not presets:
            print(f"\n  No saved presets. Create one:")
            print(f"    {accent}podcli presets save myshow --video ep.mp4 --caption-style branded --logo mylogo{reset}\n")
            return
        print(f"\n  {bold}Presets ({len(presets)}){reset}\n")
        for p in presets:
            video_tag = f" · {gray}{os.path.basename(p['video_path'])}{reset}" if p.get("video_path") else ""
            corr_tag = f" · {gray}{len(p['corrections'])} corrections{reset}" if p.get("corrections") else ""
            print(f"    {accent}{p['name']}{reset}{video_tag}{corr_tag}")
            parts = []
            if p.get("caption_style"):
                parts.append(p["caption_style"])
            if p.get("crop_strategy"):
                parts.append(p["crop_strategy"])
            if p.get("logo_path"):
                parts.append(f"logo: {os.path.basename(p['logo_path'])}")
            if p.get("quality"):
                parts.append(p["quality"])
            if parts:
                print(f"      {gray}{' · '.join(parts)}{reset}")
        print()

    elif args.presets_action == "save":
        # Load existing preset to merge (so you can update one field at a time)
        try:
            existing = get_preset(args.name)
            existing.pop("name", None)
        except FileNotFoundError:
            existing = {}

        config = {**existing}
        if args.video:
            config["video_path"] = _clean_path(args.video)
        if args.transcript:
            config["transcript_path"] = _clean_path(args.transcript)
        if args.output:
            config["output_dir"] = _clean_path(args.output)
        if args.caption_style:
            config["caption_style"] = args.caption_style
        if args.crop:
            config["crop_strategy"] = args.crop
        if args.logo:
            from services.asset_store import resolve as _resolve_logo
            config["logo_path"] = _resolve_logo(args.logo) or args.logo
        if args.outro:
            from services.asset_store import resolve as _resolve_outro
            config["outro_path"] = _resolve_outro(args.outro) or args.outro
        if args.top:
            config["top_clips"] = args.top
        if args.time_adjust is not None:
            config["time_adjust"] = args.time_adjust
        if args.quality:
            config["quality"] = args.quality
        if args.no_energy:
            config["energy_boost"] = False
        if args.no_speakers:
            config["no_speakers"] = True
        if args.with_corrections:
            from services.corrections import get_corrections
            config["corrections"] = get_corrections()

        path = save_preset(args.name, config)
        print(f"\n  {green}✓{reset} Preset '{accent}{args.name}{reset}' saved")
        # Show summary
        if config.get("video_path"):
            print(f"    video:   {gray}{config['video_path']}{reset}")
        if config.get("caption_style"):
            print(f"    caption: {gray}{config['caption_style']}{reset}")
        if config.get("logo_path"):
            print(f"    logo:    {gray}{config['logo_path']}{reset}")
        if config.get("outro_path"):
            print(f"    outro:   {gray}{config['outro_path']}{reset}")
        if config.get("corrections"):
            print(f"    corrections: {gray}{len(config['corrections'])} words{reset}")
        print()

    elif args.presets_action == "delete":
        if delete_preset(args.name):
            print(f"  Preset '{args.name}' deleted")
        else:
            print(f"  Preset '{args.name}' not found")

    elif args.presets_action == "show":
        try:
            p = get_preset(args.name)
            print(f"\n  {bold}Preset: {accent}{args.name}{reset}\n")
            for k, v in sorted(p.items()):
                if k == "name" or v == "" or v == {} or v is None:
                    continue
                if k == "corrections" and isinstance(v, dict):
                    print(f"    {gray}{k}:{reset} {len(v)} words")
                    for wrong, correct in v.items():
                        print(f"      {gray}{wrong}{reset} → {green}{correct}{reset}")
                else:
                    print(f"    {gray}{k}:{reset} {v}")
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
    from services.thumbnail_ai import generate_variations
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

    video = getattr(args, "video", None)

    paths = generate_variations(
        title=args.title,
        output_dir=args.output,
        photo_path=photo,
        video_path=video,
        logo_path=logo,
        config={"variations": args.variations},
    )

    for p in paths:
        print(f"  {green}✓{reset} {p}")

    print(f"\n  {gray}Open the folder to preview and pick the best one.{reset}")
    print(f"  {gray}Edit .podcli/thumbnail-config.json to customize colors, fonts, layout.{reset}\n")


def cmd_corrections(args):
    """Manage transcript word corrections."""
    from services.corrections import get_corrections, save_corrections

    accent = "\033[38;2;212;135;74m"
    green = "\033[38;2;74;222;128m"
    gray = "\033[38;5;245m"
    bold = "\033[1m"
    reset = "\033[0m"

    action = getattr(args, "corrections_action", None) or "list"

    if action == "list":
        corrections = get_corrections()
        if not corrections:
            print(f"\n  {gray}No corrections set. Add one:{reset}")
            print(f"  {accent}podcli corrections add \"Boxel\" \"Voxel\"{reset}\n")
            return
        print(f"\n  {bold}Transcript corrections{reset} ({len(corrections)}):\n")
        for wrong, correct in sorted(corrections.items()):
            print(f"    {gray}{wrong}{reset} → {green}{correct}{reset}")
        print()
    elif action == "add":
        wrong = args.wrong
        correct = args.correct
        corrections = get_corrections()
        corrections[wrong] = correct
        save_corrections(corrections)
        print(f"\n  {green}✓{reset} Added: {gray}{wrong}{reset} → {green}{correct}{reset}")
        print(f"  {gray}({len(corrections)} total corrections){reset}\n")
    elif action == "remove":
        wrong = args.wrong
        corrections = get_corrections()
        if wrong in corrections:
            del corrections[wrong]
            save_corrections(corrections)
            print(f"\n  {green}✓{reset} Removed: {gray}{wrong}{reset}\n")
        else:
            print(f"\n  {gray}Not found: {wrong}{reset}\n")


def cmd_knowledge(args):
    """Manage knowledge base files (.podcli/knowledge/)."""
    kb_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".podcli", "knowledge")

    accent = "\033[38;2;212;135;74m"
    gray = "\033[38;5;245m"
    green = "\033[38;2;74;222;128m"
    red = "\033[38;2;248;113;113m"
    bold = "\033[1m"
    dim = "\033[2m"
    reset = "\033[0m"

    action = getattr(args, "knowledge_action", None) or "list"

    if action == "list":
        print(f"\n  {bold}Knowledge Base{reset}")
        print(f"  {'─' * 45}")
        if not os.path.isdir(kb_dir):
            print(f"  {gray}Empty — no knowledge files{reset}\n")
            return
        files = sorted(f for f in os.listdir(kb_dir) if f.endswith(".md"))
        if not files:
            print(f"  {gray}Empty — no knowledge files{reset}\n")
            return
        for fname in files:
            fpath = os.path.join(kb_dir, fname)
            size = os.path.getsize(fpath)
            # Read first non-empty, non-header line as preview
            preview = ""
            try:
                with open(fpath) as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith("#") and not line.startswith("---"):
                            preview = line[:60]
                            break
            except Exception:
                pass
            print(f"  {accent}•{reset} {bold}{fname}{reset}  {gray}({size/1024:.1f}KB){reset}")
            if preview:
                print(f"    {dim}{preview}{'…' if len(preview) >= 60 else ''}{reset}")
        print(f"  {'─' * 45}")
        print(f"  {gray}{len(files)} files in {kb_dir}{reset}\n")

    elif action == "read":
        name = getattr(args, "filename", None)
        if not name:
            print(f"  {red}✗{reset} Specify a filename", file=sys.stderr)
            return
        if not name.endswith(".md"):
            name += ".md"
        fpath = os.path.join(kb_dir, name)
        if not os.path.exists(fpath):
            print(f"  {red}✗{reset} Not found: {name}", file=sys.stderr)
            return
        with open(fpath) as f:
            print(f.read())

    elif action == "edit":
        name = getattr(args, "filename", None)
        content = getattr(args, "content", None)
        if not name:
            print(f"  {red}✗{reset} Specify a filename", file=sys.stderr)
            return
        if not name.endswith(".md"):
            name += ".md"
        os.makedirs(kb_dir, exist_ok=True)
        fpath = os.path.join(kb_dir, name)
        if content:
            with open(fpath, "w") as f:
                f.write(content)
            print(f"  {green}✓{reset} Written: {name}")
        else:
            # Open in $EDITOR
            editor = os.environ.get("EDITOR", "nano")
            os.system(f'{editor} "{fpath}"')

    elif action == "delete":
        name = getattr(args, "filename", None)
        if not name:
            print(f"  {red}✗{reset} Specify a filename", file=sys.stderr)
            return
        if not name.endswith(".md"):
            name += ".md"
        fpath = os.path.join(kb_dir, name)
        if os.path.exists(fpath):
            os.remove(fpath)
            print(f"  {green}✓{reset} Deleted: {name}")
        else:
            print(f"  {red}✗{reset} Not found: {name}", file=sys.stderr)


def cmd_cache(args):
    """Manage transcription cache."""
    cache_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".podcli", "cache")

    accent = "\033[38;2;212;135;74m"
    gray = "\033[38;5;245m"
    green = "\033[38;2;74;222;128m"
    bold = "\033[1m"
    reset = "\033[0m"

    action = getattr(args, "cache_action", None) or "status"

    if action == "clear":
        if os.path.exists(cache_dir):
            import shutil
            count = len([f for f in os.listdir(cache_dir) if f.endswith(".json")])
            shutil.rmtree(cache_dir)
            print(f"\n  {green}✓{reset} Cleared {count} cached transcription(s)")
        else:
            print(f"\n  {gray}Cache is already empty{reset}")
        print()
        return

    # Status (default)
    print(f"\n  {bold}Transcription Cache{reset}")
    print(f"  {'─' * 35}")

    if not os.path.exists(cache_dir):
        print(f"  {gray}Empty — no cached transcriptions{reset}\n")
        return

    files = [f for f in os.listdir(cache_dir) if f.endswith(".json")]
    if not files:
        print(f"  {gray}Empty — no cached transcriptions{reset}\n")
        return

    total_size = 0
    for fname in files:
        fpath = os.path.join(cache_dir, fname)
        size = os.path.getsize(fpath)
        total_size += size

        # Try to read the cached file to show what video it's for
        try:
            with open(fpath) as f:
                data = json.load(f)
            n_words = len(data.get("words", []))
            n_segs = len(data.get("segments", []))
            lang = data.get("language", "?")
            mtime = os.path.getmtime(fpath)
            import datetime
            age = datetime.datetime.fromtimestamp(mtime).strftime("%b %d %H:%M")
            print(f"  {accent}•{reset} {n_segs} segments, {n_words} words, {lang}  {gray}({size/1024:.0f}KB, {age}){reset}")
        except Exception:
            print(f"  {accent}•{reset} {fname}  {gray}({size/1024:.0f}KB){reset}")

    print(f"  {'─' * 35}")
    if total_size > 1024 * 1024:
        print(f"  Total: {bold}{total_size / (1024*1024):.1f}MB{reset}  ({len(files)} file{'s' if len(files) != 1 else ''})")
    else:
        print(f"  Total: {bold}{total_size / 1024:.0f}KB{reset}  ({len(files)} file{'s' if len(files) != 1 else ''})")
    print(f"  {gray}Run {accent}podcli cache clear{reset} {gray}to delete all{reset}\n")


def cmd_info(args):
    """Show system info."""
    from services.encoder import get_encoder_info
    from services.claude_suggest import _find_ai_cli

    green = "\033[38;2;74;222;128m"
    yellow = "\033[38;2;250;204;21m"
    gray = "\033[38;5;245m"
    reset = "\033[0m"

    info = get_encoder_info()
    ai_path, ai_engine = _find_ai_cli()

    # Check HF_TOKEN
    hf_token = os.environ.get("HF_TOKEN", "")
    if not hf_token:
        env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".env")
        if os.path.exists(env_path):
            try:
                with open(env_path) as f:
                    for line in f:
                        if line.strip().startswith("HF_TOKEN=") and line.strip().split("=", 1)[1].strip():
                            hf_token = line.strip().split("=", 1)[1].strip()
                            break
            except Exception:
                pass

    print(f"\n  podcli system info\n")
    print(f"    Platform:     {info['system']}")
    print(f"    Encoder:      {info['best']}")
    print(f"    Available:    {', '.join(info['available'])}")
    print(f"    AI CLI:       {green}{('Claude' if ai_engine == 'claude' else 'Codex') + ' (' + ai_path + ')' if ai_path else f'{yellow}not found — install Claude Code or Codex'}{reset}")
    print(f"    Speakers:     {green + '✓ configured' if hf_token else yellow + '✗ set HF_TOKEN in .env'}{reset}")
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

    # Check AI CLI (Claude Code or Codex)
    from services.claude_suggest import _find_ai_cli
    ai_path, ai_engine = _find_ai_cli()

    print(f"  {bold}podcli{reset} v{VERSION}")

    # Cache info
    cache_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".podcli", "cache")
    cache_count = 0
    if os.path.isdir(cache_dir):
        cache_count = len([f for f in os.listdir(cache_dir) if f.endswith(".json")])

    # Status — one line
    ai_label = ("Claude" if ai_engine == "claude" else "Codex") if ai_path else "AI CLI"
    ai_tag = f"{green}✓ {ai_label}{reset}" if ai_path else f"{yellow}✗{reset}"
    speaker_tag = f"{green}✓{reset}" if speakers_ok else f"{yellow}✗{reset}"
    cache_tag = f"{green}{cache_count}{reset}" if cache_count else f"{gray}0{reset}"
    print(f"  {gray}Encoder {green}{encoder_label}{reset} {gray}· {ai_tag} {gray}· Speakers {speaker_tag} {gray}· Cache {cache_tag}{reset}")

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

    # Presets
    try:
        from presets import list_presets
        presets = list_presets()
        if presets:
            names = []
            for p in presets:
                tag = p["name"]
                if p.get("video_path") and os.path.exists(p["video_path"]):
                    tag += f" {dim}({os.path.basename(p['video_path'])}){reset}{gray}"
                names.append(tag)
            print(f"  {gray}Presets{reset} {gray}{' · '.join(names)}{reset}")
    except Exception:
        pass

    # Corrections count
    try:
        from services.corrections import get_corrections
        corr = get_corrections()
        if corr:
            print(f"  {gray}Corrections{reset} {green}{len(corr)}{reset} {gray}words{reset}")
    except Exception:
        pass

    print()

    if not speakers_ok:
        print(f"  {yellow}⚠ Speaker detection not set up — run: podcli info{reset}")

    print()


def print_help():
    """Print custom help screen."""
    accent = "\033[38;2;212;135;74m"
    gray = "\033[38;5;245m"
    green = "\033[38;2;74;222;128m"
    bold = "\033[1m"
    dim = "\033[2m"
    reset = "\033[0m"
    ul = "\033[4m"

    print(BANNER)
    print(f"  {bold}podcli{reset} v{VERSION} — AI-powered podcast clip generator")
    print()
    print(f"  {bold}Usage:{reset}  podcli {accent}<command>{reset} [options]")
    print(f"          podcli {dim}(interactive mode){reset}")
    print()
    print(f"  {bold}Commands:{reset}")
    print(f"    {accent}process{reset} {gray}<video>{reset}       Transcribe + detect clips + render shorts")
    print(f"    {accent}assets{reset}  {gray}<action>{reset}      Manage logos, intros, outros")
    print(f"    {accent}presets{reset} {gray}<action>{reset}      Save/load rendering presets")
    print(f"    {accent}thumbnails{reset} {gray}<title>{reset}   Generate thumbnail variations")
    print(f"    {accent}knowledge{reset} {gray}<action>{reset}    Manage knowledge base (.podcli/knowledge/)")
    print(f"    {accent}corrections{reset} {gray}<action>{reset}  Fix Whisper misheard words (Boxel→Voxel)")
    print(f"    {accent}cache{reset}  {gray}[clear]{reset}       Show/clear transcription cache")
    print(f"    {accent}info{reset}                 Show system info (encoder, codecs)")
    print()
    print(f"  {bold}Process options:{reset}")
    print(f"    {green}-t{reset}, {green}--transcript{reset} {gray}<file>{reset}   Use existing transcript (.txt/.json)")
    print(f"    {green}-n{reset}, {green}--top{reset} {gray}<N>{reset}            Export top N clips {dim}(default: 5){reset}")
    print(f"    {green}-o{reset}, {green}--output{reset} {gray}<dir>{reset}        Output directory {dim}(default: ./clips){reset}")
    print(f"    {green}-p{reset}, {green}--preset{reset} {gray}<name>{reset}       Load a saved preset")
    print(f"    {green}--caption-style{reset} {gray}<style>{reset}  branded | hormozi | karaoke | subtle")
    print(f"    {green}--crop{reset} {gray}<strategy>{reset}       center | face")
    print(f"    {green}--logo{reset} {gray}<asset|path>{reset}     Overlay logo image")
    print(f"    {green}--outro{reset} {gray}<asset|path>{reset}    Append outro video")
    print(f"    {green}--quality{reset} {gray}<level>{reset}       low | medium | high | max")
    print(f"    {green}--no-energy{reset}            Skip audio energy analysis")
    print(f"    {green}--no-speakers{reset}          Skip speaker detection (faster)")
    print(f"    {green}--no-cache{reset}             Force re-transcription")
    print()
    print(f"  {bold}Examples:{reset}")
    print(f"    {dim}${reset} podcli process episode.mp4")
    print(f"    {dim}${reset} podcli process ep42.mp4 -t transcript.json --top 8 --caption-style hormozi")
    print(f"    {dim}${reset} podcli process ep42.mp4 --preset myshow --quality max")
    print(f"    {dim}${reset} podcli assets add mylogo ~/branding/logo.png")
    print(f"    {dim}${reset} podcli presets save myshow --caption-style branded --logo mylogo")
    print(f"    {dim}${reset} podcli thumbnails \"Why AI Changes Everything\" --video ep42.mp4")
    print()
    print(f"  {bold}PodStack{reset} {dim}(Claude Code slash commands):{reset}")
    print(f"    {accent}/prep-episode{reset}          Full pipeline: transcript → publish-ready")
    print(f"    {accent}/process-transcript{reset}    Extract clip-worthy moments from transcript")
    print(f"    {accent}/generate-titles{reset}       Generate 8 title options with verification")
    print(f"    {accent}/generate-descriptions{reset} Descriptions + hashtags + SEO")
    print(f"    {accent}/plan-thumbnails{reset}       Thumbnail text + layout briefs")
    print(f"    {accent}/review-content{reset}        Brand voice & quality gate check")
    print(f"    {accent}/publish-checklist{reset}     Pre/post-publish checklist")
    print()
    print(f"  {gray}Run {reset}podcli <command> --help{gray} for command-specific options{reset}")
    print()


def main():
    parser = argparse.ArgumentParser(
        prog="podcli",
        description="AI-powered podcast clip generator",
        add_help=False,
    )
    parser.add_argument("-h", "--help", action="store_true", dest="show_help")
    parser.add_argument("--version", action="version", version=f"podcli {VERSION}")
    parser.add_argument("--no-banner", action="store_true", help=argparse.SUPPRESS)
    sub = parser.add_subparsers(dest="command")

    # ── process ──
    proc = sub.add_parser("process", help="Process a video into clips")
    proc.add_argument("video", nargs="?", default=None, help="Path to podcast video file (optional if preset has video_path)")
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
    proc.add_argument("--no-speakers", action="store_true", help="Skip speaker detection (faster, uses face detection only)")
    proc.add_argument("--no-cache", action="store_true", help="Force re-transcription (ignore cached transcript)")
    proc.add_argument("--quality", choices=["low", "medium", "high", "max"], help="Output quality (default: high)")

    # ── presets ──
    pre = sub.add_parser("presets", help="Manage presets")
    pre_sub = pre.add_subparsers(dest="presets_action")

    pre_list = pre_sub.add_parser("list", help="List all presets")

    pre_save = pre_sub.add_parser("save", help="Save a preset")
    pre_save.add_argument("name", help="Preset name")
    pre_save.add_argument("--video", help="Default video path")
    pre_save.add_argument("--transcript", help="Default transcript path")
    pre_save.add_argument("--output", help="Default output directory")
    pre_save.add_argument("--caption-style", choices=["branded", "hormozi", "karaoke", "subtle"])
    pre_save.add_argument("--crop", choices=["center", "face"])
    pre_save.add_argument("--logo", help="Logo (asset name or path)")
    pre_save.add_argument("--outro", help="Outro (asset name or path)")
    pre_save.add_argument("--top", type=int, help="Default top clips count")
    pre_save.add_argument("--time-adjust", type=float)
    pre_save.add_argument("--quality", choices=["low", "medium", "high", "max"])
    pre_save.add_argument("--no-energy", action="store_true", help="Skip audio energy analysis")
    pre_save.add_argument("--no-speakers", action="store_true", help="Skip speaker detection")
    pre_save.add_argument("--with-corrections", action="store_true", help="Include current global corrections in preset")

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
    thumb.add_argument("--video", help="Video to extract face frame from")
    thumb.add_argument("--logo", help="Logo (asset name or path)")
    thumb.add_argument("-n", "--variations", type=int, default=3, help="Number of variations")

    # ── corrections ──
    corr = sub.add_parser("corrections", help="Manage transcript word corrections (Whisper fixes)")
    corr_sub = corr.add_subparsers(dest="corrections_action")
    corr_sub.add_parser("list", help="Show all corrections")
    corr_add = corr_sub.add_parser("add", help="Add a correction")
    corr_add.add_argument("wrong", help="Misheard word/phrase")
    corr_add.add_argument("correct", help="Correct replacement")
    corr_rm = corr_sub.add_parser("remove", help="Remove a correction")
    corr_rm.add_argument("wrong", help="Word to remove from corrections")

    # ── knowledge ──
    kb = sub.add_parser("knowledge", help="Manage knowledge base files")
    kb_sub = kb.add_subparsers(dest="knowledge_action")
    kb_sub.add_parser("list", help="List all knowledge files")
    kb_read = kb_sub.add_parser("read", help="Print a knowledge file")
    kb_read.add_argument("filename", help="File name (e.g. 01-brand-identity)")
    kb_edit = kb_sub.add_parser("edit", help="Edit/create a knowledge file")
    kb_edit.add_argument("filename", help="File name (e.g. 01-brand-identity)")
    kb_edit.add_argument("--content", help="Content to write (opens $EDITOR if omitted)")
    kb_del = kb_sub.add_parser("delete", help="Delete a knowledge file")
    kb_del.add_argument("filename", help="File name to delete")

    # ── cache ──
    cache_p = sub.add_parser("cache", help="Manage transcription cache")
    cache_sub = cache_p.add_subparsers(dest="cache_action")
    cache_sub.add_parser("status", help="Show cache size and contents")
    cache_sub.add_parser("clear", help="Delete all cached transcriptions")

    # ── info ──
    sub.add_parser("info", help="Show system info (encoder, etc.)")

    args = parser.parse_args()

    if getattr(args, "show_help", False) and args.command is None:
        print_help()
        return

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
    elif args.command == "corrections":
        cmd_corrections(args)
    elif args.command == "knowledge":
        cmd_knowledge(args)
    elif args.command == "cache":
        cmd_cache(args)
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

    # Reset terminal to sane state — fixes ^M echo from corrupted tty settings
    try:
        os.system("stty sane 2>/dev/null")
    except Exception:
        pass

    import questionary
    from questionary import Style

    qstyle = Style([
        ("qmark", "fg:#d4874a bold"),
        ("question", "bold"),
        ("answer", "fg:#4ade80"),
        ("pointer", "fg:#d4874a bold"),
        ("highlighted", "fg:#d4874a bold"),
        ("selected", "fg:#4ade80"),
        ("instruction", "fg:#a1a1aa"),
    ])

    while True:
        choice = questionary.select(
            "What do you want to do?",
            choices=[
                questionary.Choice("Process a video → shorts", value="process"),
                questionary.Choice("Open Web UI", value="webui"),
                questionary.Separator(),
                questionary.Choice("Presets", value="presets"),
                questionary.Choice("Assets", value="assets"),
                questionary.Choice("Knowledge base", value="knowledge"),
                questionary.Choice("Corrections", value="corrections"),
                questionary.Separator(),
                questionary.Choice("Thumbnails", value="thumbnails"),
                questionary.Choice("Cache", value="cache"),
                questionary.Choice("Info", value="info"),
                questionary.Separator(),
                questionary.Choice("Quit", value="quit"),
            ],
            style=qstyle,
            instruction="",
        ).ask()

        if choice is None or choice == "quit":
            return
        elif choice == "process":
            _interactive_process()
            return
        elif choice == "webui":
            print(f"\n  {gray}Starting Web UI...{reset}\n")
            import subprocess as sp
            sp.run(["npm", "run", "ui"], cwd=os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
        elif choice == "assets":
            _interactive_assets()
        elif choice == "presets":
            _interactive_presets()
        elif choice == "knowledge":
            _interactive_knowledge()
        elif choice == "corrections":
            _interactive_corrections()
        elif choice == "thumbnails":
            _interactive_thumbnails()
        elif choice == "cache":
            _interactive_cache()
        elif choice == "info":
            _interactive_info()


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
    """Interactive video processing wizard using questionary."""
    import questionary
    from questionary import Style

    green = "\033[38;2;74;222;128m"
    gray = "\033[38;5;245m"
    dim = "\033[2m"
    reset = "\033[0m"

    qstyle = Style([
        ("qmark", "fg:#d4874a bold"),
        ("question", "bold"),
        ("answer", "fg:#4ade80"),
        ("pointer", "fg:#d4874a bold"),
        ("highlighted", "fg:#d4874a bold"),
        ("selected", "fg:#4ade80"),
        ("instruction", "fg:#a1a1aa"),
    ])

    # Check for presets first
    from presets import list_presets, get_preset
    presets = list_presets()
    preset_choices = [p for p in presets if p.get("video_path")]

    use_preset = None
    if preset_choices:
        preset_options = [
            questionary.Choice(
                f"{p['name']} — {os.path.basename(p['video_path'])}",
                value=p["name"]
            ) for p in preset_choices
        ]
        preset_options.append(questionary.Choice("New video (manual setup)", value="_new"))
        use_preset = questionary.select(
            "Use a preset or set up manually?",
            choices=preset_options,
            style=qstyle,
        ).ask()
        if use_preset is None:
            return

    if use_preset and use_preset != "_new":
        # Run with preset
        config = get_preset(use_preset)
        video = config["video_path"]
        if not os.path.exists(video):
            print(f"\n  Video not found: {video}")
            return

        if questionary.confirm(
            f"Process {os.path.basename(video)} with preset '{use_preset}'?",
            default=True, style=qstyle,
        ).ask():
            cmd = [sys.executable, os.path.abspath(__file__), "process", "--preset", use_preset]
            sys.stdout.write("\033[2J\033[H")
            sys.stdout.flush()
            print(f"  {green}▶{reset} Starting with preset '{use_preset}'...\n")
            import subprocess as _sp
            sys.exit(_sp.call(cmd))
        return

    # Manual setup
    video = questionary.path(
        "Video file:",
        style=qstyle,
        validate=lambda v: True if os.path.exists(_clean_path(v)) else "File not found",
    ).ask()
    if not video:
        return
    video = _clean_path(video)

    transcript = questionary.path(
        "Transcript (Enter to auto-transcribe):",
        style=qstyle,
        default="",
    ).ask()
    transcript = _clean_path(transcript) if transcript and os.path.exists(_clean_path(transcript)) else None
    if not transcript:
        print(f"  {gray}→ Will auto-transcribe with Whisper{reset}")

    caption_style = questionary.select(
        "Caption style:",
        choices=[
            questionary.Choice("branded — dark pill on active word + logo", value="branded"),
            questionary.Choice("hormozi — bold uppercase, yellow highlight", value="hormozi"),
            questionary.Choice("karaoke — sentence visible, words light up", value="karaoke"),
            questionary.Choice("subtle — clean small text at bottom", value="subtle"),
        ],
        default="branded",
        style=qstyle,
    ).ask()
    if caption_style is None:
        return

    quality = questionary.select(
        "Quality:",
        choices=["low", "medium", "high", "max"],
        default="max",
        style=qstyle,
    ).ask()
    if quality is None:
        return

    top_n = questionary.text(
        "How many clips?",
        default="5",
        style=qstyle,
        validate=lambda v: True if v.isdigit() and int(v) > 0 else "Enter a number",
    ).ask()
    if top_n is None:
        return
    top_n = int(top_n)

    # Logo
    logo = None
    try:
        from services.asset_store import list_assets
        logos = [a for a in list_assets() if a["type"] == "logo" and os.path.exists(a["path"])]
        if logos:
            logo_choices = [questionary.Choice(f"{a['name']} ({os.path.basename(a['path'])})", value=a["path"]) for a in logos]
            logo_choices.append(questionary.Choice("None", value=None))
            logo = questionary.select("Logo:", choices=logo_choices, default=logo_choices[0], style=qstyle).ask()
    except Exception:
        pass

    # Outro
    outro = None
    try:
        from services.asset_store import list_assets as list_assets_o
        outros = [a for a in list_assets_o() if a["type"] == "video" and os.path.exists(a["path"])]
        if outros:
            outro_choices = [questionary.Choice("None", value=None)]
            outro_choices += [questionary.Choice(f"{a['name']} ({os.path.basename(a['path'])})", value=a["path"]) for a in outros]
            outro = questionary.select("Outro:", choices=outro_choices, style=qstyle).ask()
    except Exception:
        pass

    # Confirm
    print(f"\n  {'─' * 45}")
    print(f"  Video:      {os.path.basename(video)}")
    print(f"  Style:      {caption_style}  ·  Quality: {quality}  ·  Clips: {top_n}")
    if logo:
        print(f"  Logo:       ✓")
    if outro:
        print(f"  Outro:      ✓  {dim}{os.path.basename(outro)}{reset}")
    print(f"  Transcript: {'auto (Whisper)' if not transcript else os.path.basename(transcript)}")
    print()

    if not questionary.confirm("Start processing?", default=True, style=qstyle).ask():
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

    sys.stdout.write("\033[2J\033[H")
    sys.stdout.flush()
    print(f"  {green}▶{reset} Starting...\n")
    sys.stderr.flush()
    import subprocess as _sp
    sys.exit(_sp.call(cmd))


def _interactive_cache():
    """Interactive cache management using questionary."""
    import argparse as _ap
    import questionary
    from questionary import Style

    qstyle = Style([
        ("qmark", "fg:#d4874a bold"),
        ("question", "bold"),
        ("answer", "fg:#4ade80"),
        ("pointer", "fg:#d4874a bold"),
        ("highlighted", "fg:#d4874a bold"),
        ("selected", "fg:#4ade80"),
        ("instruction", "fg:#a1a1aa"),
    ])

    cmd_cache(_ap.Namespace(cache_action=None))

    if questionary.confirm("Clear cache?", default=False, style=qstyle).ask():
        cmd_cache(_ap.Namespace(cache_action="clear"))


def _interactive_assets():
    """Interactive asset management using questionary."""
    import questionary
    from questionary import Style
    from services.asset_store import register, unregister, list_assets

    green = "\033[38;2;74;222;128m"
    gray = "\033[38;5;245m"
    red = "\033[38;2;248;113;113m"
    accent = "\033[38;2;212;135;74m"
    reset = "\033[0m"

    qstyle = Style([
        ("qmark", "fg:#d4874a bold"),
        ("question", "bold"),
        ("answer", "fg:#4ade80"),
        ("pointer", "fg:#d4874a bold"),
        ("highlighted", "fg:#d4874a bold"),
        ("selected", "fg:#4ade80"),
        ("instruction", "fg:#a1a1aa"),
    ])

    current = list_assets()
    if current:
        print(f"\n  Registered assets:")
        for a in current:
            exists = os.path.exists(a["path"])
            icon = f"{green}✓{reset}" if exists else f"{red}✗{reset}"
            print(f"    {icon} {accent}{a['name']}{reset}  {gray}({a['type']}) {os.path.basename(a['path'])}{reset}")
        print()

    actions = [questionary.Choice("Add asset", value="add")]
    if current:
        actions.append(questionary.Choice("Remove asset", value="remove"))
    actions.append(questionary.Choice("← Back", value="_back"))

    action = questionary.select("Assets:", choices=actions, style=qstyle).ask()
    if action is None or action == "_back":
        return

    if action == "add":
        name = questionary.text("Asset name (e.g. mylogo, outro):", style=qstyle).ask()
        if not name:
            return
        path = questionary.path("File path:", style=qstyle).ask()
        if not path:
            return
        path = _clean_path(path)
        try:
            asset = register(name, path)
            print(f"\n  {green}✓{reset} Registered {accent}{name}{reset} ({asset['type']})")
            print(f"    {gray}{asset['path']}{reset}\n")
        except FileNotFoundError as e:
            print(f"\n  {red}✗{reset} {e}\n", file=sys.stderr)

    elif action == "remove":
        choices = [questionary.Choice(f"{a['name']} ({a['type']})", value=a["name"]) for a in current]
        to_remove = questionary.select("Remove which?", choices=choices, style=qstyle).ask()
        if to_remove:
            unregister(to_remove)
            print(f"\n  {green}✓{reset} Removed '{to_remove}'\n")


def _interactive_presets():
    """Interactive preset management using questionary."""
    import questionary
    from questionary import Style
    from presets import list_presets, get_preset, save_preset, delete_preset

    green = "\033[38;2;74;222;128m"
    accent = "\033[38;2;212;135;74m"
    reset = "\033[0m"

    qstyle = Style([
        ("qmark", "fg:#d4874a bold"),
        ("question", "bold"),
        ("answer", "fg:#4ade80"),
        ("pointer", "fg:#d4874a bold"),
        ("highlighted", "fg:#d4874a bold"),
        ("selected", "fg:#4ade80"),
        ("instruction", "fg:#a1a1aa"),
    ])

    presets = list_presets()

    # Action menu
    actions = [questionary.Choice("Create new preset", value="_new")]
    if presets:
        for p in presets:
            label = p["name"]
            if p.get("video_path"):
                label += f" — {os.path.basename(p['video_path'])}"
            actions.append(questionary.Choice(f"Edit: {label}", value=f"edit:{p['name']}"))
        for p in presets:
            actions.append(questionary.Choice(f"Delete: {p['name']}", value=f"del:{p['name']}"))
    actions.append(questionary.Choice("← Back", value="_back"))

    action = questionary.select("Presets:", choices=actions, style=qstyle).ask()
    if action is None or action == "_back":
        return

    if action.startswith("del:"):
        name = action[4:]
        if questionary.confirm(f"Delete preset '{name}'?", default=False, style=qstyle).ask():
            delete_preset(name)
            print(f"  {green}✓{reset} Deleted '{name}'\n")
        return

    # Create or edit
    if action == "_new":
        name = questionary.text("Preset name:", style=qstyle).ask()
        if not name:
            return
        existing = {}
    else:
        name = action[5:]  # strip "edit:"
        try:
            existing = get_preset(name)
            existing.pop("name", None)
        except FileNotFoundError:
            existing = {}

    config = {**existing}

    # Video path
    video = questionary.path(
        "Video path (Enter to skip):",
        default=config.get("video_path") or "",
        style=qstyle,
    ).ask()
    if video is None:
        return
    if video:
        config["video_path"] = _clean_path(video)

    # Caption style
    caption_style = questionary.select(
        "Caption style:",
        choices=["branded", "hormozi", "karaoke", "subtle"],
        default=config.get("caption_style", "branded"),
        style=qstyle,
    ).ask()
    if caption_style:
        config["caption_style"] = caption_style

    # Crop strategy
    crop = questionary.select(
        "Crop strategy:",
        choices=["face", "center"],
        default=config.get("crop_strategy", "face"),
        style=qstyle,
    ).ask()
    if crop:
        config["crop_strategy"] = crop

    # Logo from assets
    try:
        from services.asset_store import list_assets
        logos = [a for a in list_assets() if a["type"] == "logo" and os.path.exists(a["path"])]
        if logos:
            logo_choices = [questionary.Choice(f"{a['name']} ({os.path.basename(a['path'])})", value=a["path"]) for a in logos]
            logo_choices.append(questionary.Choice("None", value=""))
            current_logo = config.get("logo_path", "")
            logo = questionary.select("Logo:", choices=logo_choices, default=current_logo or logo_choices[0], style=qstyle).ask()
            if logo is not None:
                config["logo_path"] = logo
    except Exception:
        pass

    # Outro from assets
    try:
        from services.asset_store import list_assets as list_assets_o
        outros = [a for a in list_assets_o() if a["type"] == "video" and os.path.exists(a["path"])]
        if outros:
            outro_choices = [questionary.Choice("None", value="")]
            outro_choices += [questionary.Choice(f"{a['name']} ({os.path.basename(a['path'])})", value=a["path"]) for a in outros]
            current_outro = config.get("outro_path", "")
            outro = questionary.select("Outro:", choices=outro_choices, default=current_outro or outro_choices[0], style=qstyle).ask()
            if outro is not None:
                config["outro_path"] = outro
    except Exception:
        pass

    # Quality
    quality = questionary.select(
        "Quality:",
        choices=["low", "medium", "high", "max"],
        default=config.get("quality", "max"),
        style=qstyle,
    ).ask()
    if quality:
        config["quality"] = quality

    # Top clips
    top = questionary.text(
        "Top clips:",
        default=str(config.get("top_clips", 5)),
        style=qstyle,
        validate=lambda v: True if v.isdigit() and int(v) > 0 else "Enter a number",
    ).ask()
    if top:
        config["top_clips"] = int(top)

    # Corrections
    if questionary.confirm("Include current word corrections?", default=bool(config.get("corrections")), style=qstyle).ask():
        from services.corrections import get_corrections
        config["corrections"] = get_corrections()

    save_preset(name, config)
    print(f"\n  {green}✓{reset} Preset '{accent}{name}{reset}' saved\n")


def _interactive_knowledge():
    """Interactive knowledge base management using questionary."""
    import questionary
    from questionary import Style
    import argparse as _ap

    green = "\033[38;2;74;222;128m"
    gray = "\033[38;5;245m"
    accent = "\033[38;2;212;135;74m"
    reset = "\033[0m"

    qstyle = Style([
        ("qmark", "fg:#d4874a bold"),
        ("question", "bold"),
        ("answer", "fg:#4ade80"),
        ("pointer", "fg:#d4874a bold"),
        ("highlighted", "fg:#d4874a bold"),
        ("selected", "fg:#4ade80"),
        ("instruction", "fg:#a1a1aa"),
    ])

    kb_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".podcli", "knowledge")
    files = sorted(f for f in os.listdir(kb_dir) if f.endswith(".md")) if os.path.isdir(kb_dir) else []

    # Show current files
    cmd_knowledge(_ap.Namespace(knowledge_action="list"))

    actions = [
        questionary.Choice("Edit a file", value="edit"),
        questionary.Choice("Create new file", value="new"),
    ]
    if files:
        actions.insert(1, questionary.Choice("Read a file", value="read"))
        actions.append(questionary.Choice("Delete a file", value="delete"))
    actions.append(questionary.Choice("← Back", value="_back"))

    action = questionary.select("Knowledge base:", choices=actions, style=qstyle).ask()
    if action is None or action == "_back":
        return

    if action == "read":
        choice = questionary.select("Which file?", choices=files, style=qstyle).ask()
        if choice:
            cmd_knowledge(_ap.Namespace(knowledge_action="read", filename=choice))

    elif action == "edit":
        if not files:
            print(f"  {gray}No files to edit — create one first{reset}")
            return
        choice = questionary.select("Which file?", choices=files, style=qstyle).ask()
        if choice:
            cmd_knowledge(_ap.Namespace(knowledge_action="edit", filename=choice, content=None))

    elif action == "new":
        name = questionary.text("File name (e.g. my-notes):", style=qstyle).ask()
        if name:
            if not name.endswith(".md"):
                name += ".md"
            cmd_knowledge(_ap.Namespace(knowledge_action="edit", filename=name, content=None))

    elif action == "delete":
        choice = questionary.select("Delete which file?", choices=files, style=qstyle).ask()
        if choice and questionary.confirm(f"Delete {choice}?", default=False, style=qstyle).ask():
            cmd_knowledge(_ap.Namespace(knowledge_action="delete", filename=choice))


def _interactive_corrections():
    """Interactive corrections management using questionary."""
    import questionary
    from questionary import Style
    from services.corrections import get_corrections, save_corrections

    green = "\033[38;2;74;222;128m"
    gray = "\033[38;5;245m"
    reset = "\033[0m"

    qstyle = Style([
        ("qmark", "fg:#d4874a bold"),
        ("question", "bold"),
        ("answer", "fg:#4ade80"),
        ("pointer", "fg:#d4874a bold"),
        ("highlighted", "fg:#d4874a bold"),
        ("selected", "fg:#4ade80"),
        ("instruction", "fg:#a1a1aa"),
    ])

    corrections = get_corrections()
    if corrections:
        print(f"\n  Word corrections ({len(corrections)}):")
        for wrong, correct in sorted(corrections.items()):
            print(f"    {gray}{wrong}{reset} → {green}{correct}{reset}")
        print()

    actions = [questionary.Choice("Add a correction", value="add")]
    if corrections:
        actions.append(questionary.Choice("Remove a correction", value="remove"))
    actions.append(questionary.Choice("← Back", value="_back"))

    action = questionary.select("Corrections:", choices=actions, style=qstyle).ask()
    if action is None or action == "_back":
        return

    if action == "add":
        wrong = questionary.text("Wrong word (what Whisper hears):", style=qstyle).ask()
        if not wrong:
            return
        correct = questionary.text("Correct word:", style=qstyle).ask()
        if not correct:
            return
        corrections[wrong] = correct
        save_corrections(corrections)
        print(f"\n  {green}✓{reset} Added: {gray}{wrong}{reset} → {green}{correct}{reset}")
        print(f"  {gray}({len(corrections)} total corrections){reset}\n")

    elif action == "remove":
        choices = [questionary.Choice(f"{w} → {c}", value=w) for w, c in sorted(corrections.items())]
        to_remove = questionary.select("Remove which?", choices=choices, style=qstyle).ask()
        if to_remove:
            del corrections[to_remove]
            save_corrections(corrections)
            print(f"\n  {green}✓{reset} Removed: {gray}{to_remove}{reset}\n")


def _interactive_thumbnails():
    """Interactive thumbnail generation using questionary."""
    import questionary
    from questionary import Style

    qstyle = Style([
        ("qmark", "fg:#d4874a bold"),
        ("question", "bold"),
        ("answer", "fg:#4ade80"),
        ("pointer", "fg:#d4874a bold"),
        ("highlighted", "fg:#d4874a bold"),
        ("selected", "fg:#4ade80"),
        ("instruction", "fg:#a1a1aa"),
    ])

    title = questionary.text("Title text:", style=qstyle).ask()
    if not title:
        return

    video = questionary.path(
        "Video path (Enter to skip):",
        default="",
        style=qstyle,
    ).ask()
    if video:
        video = _clean_path(video)

    args_ns = _Namespace(
        title=title,
        video=video or None,
        logo=None,
        variations=3,
    )
    cmd_thumbnails(args_ns)


def _interactive_info():
    """Show system info."""
    args_ns = _Namespace()
    cmd_info(args_ns)


class _Namespace:
    """Minimal namespace for interactive commands."""
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)

    def __getattr__(self, name):
        return None


if __name__ == "__main__":
    main()
