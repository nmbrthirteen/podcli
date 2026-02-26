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

# Add parent dir to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def cmd_process(args):
    """Full auto pipeline: transcribe → suggest → export."""
    from services.clip_generator import generate_clip
    from services.transcript_parser import parse_speaker_transcript
    from services.audio_analyzer import get_energy_profile
    from services.encoder import get_encoder_info
    from presets import get_preset, DEFAULT_PRESET

    video_path = args.video
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
        config["logo_path"] = args.logo
    if args.time_adjust is not None:
        config["time_adjust"] = args.time_adjust
    if args.no_energy:
        config["energy_boost"] = False

    # Output directory
    output_dir = args.output or os.path.join(os.path.dirname(video_path), "clips")
    os.makedirs(output_dir, exist_ok=True)

    enc_info = get_encoder_info()
    print(f"\n  podcli — processing")
    print(f"  Encoder: {enc_info['best']} ({enc_info['system']})")
    print(f"  Video:   {os.path.basename(video_path)}")
    print()

    # ── Step 1: Get transcript ──
    transcript = None
    words = []
    segments = []

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
        print("  [1/4] Transcribing with Whisper...")
        from services.transcription import transcribe_file
        result = transcribe_file(
            file_path=video_path,
            model_size=config.get("whisper_model", "base"),
            progress_callback=lambda pct, msg: print(f"         {pct}% {msg}") if pct % 20 == 0 else None,
        )
        words = result["words"]
        segments = result["segments"]
        print(f"         Done: {len(segments)} segments, {len(words)} words")

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
    print("  [3/4] Scoring clips...")
    clips = _suggest_clips(
        segments=segments,
        energy_scores=energy_scores,
        top_n=config.get("top_clips", 5),
        min_dur=config.get("min_clip_duration", 20),
        max_dur=config.get("max_clip_duration", 90),
    )

    if not clips:
        print("  No clips found. Try a longer transcript or lower --min-duration.", file=sys.stderr)
        sys.exit(1)

    print(f"         Selected {len(clips)} clips:")
    for i, c in enumerate(clips):
        m_s = int(c["start_second"]) // 60
        s_s = int(c["start_second"]) % 60
        print(f"           {i+1}. [{m_s}:{s_s:02d} → +{c['duration']}s] {c['title'][:55]}")

    # ── Step 4: Export ──
    print(f"\n  [4/4] Exporting {len(clips)} clips to {output_dir}/")
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
                crop_strategy=config.get("crop_strategy", "center"),
                transcript_words=words,
                title=clip.get("title", f"clip_{i+1}"),
                output_dir=output_dir,
                logo_path=config.get("logo_path") or None,
            )
            results.append(result)
            print(f" ✓ {result['file_size_mb']}MB")
        except Exception as e:
            print(f" ✗ {e}")
            results.append({"status": "error", "error": str(e)})

    elapsed = time.time() - t0
    success = sum(1 for r in results if "output_path" in r)
    print(f"\n  Done! {success}/{len(clips)} clips exported in {elapsed:.1f}s")
    print(f"  Output: {output_dir}/\n")


def _suggest_clips(
    segments: list,
    energy_scores: list | None = None,
    top_n: int = 5,
    min_dur: float = 20,
    max_dur: float = 90,
) -> list:
    """
    Score and rank transcript segments into clip suggestions.

    Combines text heuristics + audio energy for scoring.
    """
    KEYWORDS = [
        "secret", "mistake", "important", "never", "always", "best",
        "how to", "why", "story", "amazing", "changed", "money",
        "learn", "truth", "actually", "problem", "solution", "believe",
        "crazy", "number one", "biggest", "first thing", "listen",
        "here's the thing", "let me tell you", "the key", "game changer",
        "nobody talks about", "most people", "don't realize",
    ]

    clips = []
    win_sizes = [6, 8, 10, 12]  # try multiple window sizes

    for win_size in win_sizes:
        step = max(1, int(win_size * 0.6))
        for i in range(0, len(segments) - win_size, step):
            win = segments[i : i + win_size]
            text = " ".join(s.get("text", "") for s in win)
            start = win[0].get("start", 0)
            end = win[-1].get("end", 0)
            dur = end - start

            if dur < min_dur or dur > max_dur:
                continue

            # Text heuristics
            score = 0
            text_lower = text.lower()
            if "?" in text:
                score += 3
            if "!" in text:
                score += 2
            if len(text) > 200:
                score += 1

            for kw in KEYWORDS:
                if kw in text_lower:
                    score += 2

            # Sentence-start bonus (if window starts at a natural boundary)
            first_text = win[0].get("text", "").strip()
            if first_text and first_text[0].isupper():
                score += 1

            # Audio energy boost
            if energy_scores:
                seg_energies = energy_scores[i : i + win_size]
                if seg_energies:
                    avg_energy = sum(seg_energies) / len(seg_energies)
                    max_energy = max(seg_energies)
                    score += avg_energy * 0.5 + max_energy * 0.3

            if score >= 3:
                clips.append({
                    "title": text[:55].strip() + "...",
                    "start_second": round(start),
                    "end_second": round(end),
                    "duration": round(dur),
                    "score": round(score, 2),
                    "preview": text[:100].strip(),
                })

    # Deduplicate overlapping clips (keep highest score)
    clips.sort(key=lambda c: c["score"], reverse=True)
    selected = []
    for clip in clips:
        overlap = False
        for sel in selected:
            if (clip["start_second"] < sel["end_second"] and
                clip["end_second"] > sel["start_second"]):
                # More than 50% overlap → skip
                overlap_amt = (min(clip["end_second"], sel["end_second"]) -
                              max(clip["start_second"], sel["start_second"]))
                if overlap_amt > clip["duration"] * 0.5:
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


def main():
    parser = argparse.ArgumentParser(
        prog="podcli",
        description="AI-powered podcast clip generator",
    )
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
    proc.add_argument("--logo", help="Path to logo image")
    proc.add_argument("--time-adjust", type=float, help="Timestamp offset in seconds")
    proc.add_argument("--no-energy", action="store_true", help="Skip audio energy analysis")

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

    # ── info ──
    sub.add_parser("info", help="Show system info (encoder, etc.)")

    args = parser.parse_args()

    if args.command == "process":
        cmd_process(args)
    elif args.command == "presets":
        cmd_presets(args)
    elif args.command == "info":
        cmd_info(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
