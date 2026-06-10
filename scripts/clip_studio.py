#!/usr/bin/env python3
"""
clip_studio — cut a precise fragment from a video and wrap it with a Remotion
intro + "Follow for more" outro (with social icons).

Cut a fragment by TIMESTAMP or by PARAGRAPH text (matched against the
transcript). The fragment is rendered through podcli's existing face-crop +
caption pipeline, then an intro card and an outro card are stitched on:

    [ intro ]  →  [ captioned fragment ]  →  [ outro: Follow for more + icons ]

Usage:
  # by timestamp
  python scripts/clip_studio.py VIDEO --start 12 --end 38 --caption-style hormozi

  # by paragraph (finds the matching span in the transcript)
  python scripts/clip_studio.py VIDEO --paragraph "the part where they talk about X"

  # tune the bookends
  python scripts/clip_studio.py VIDEO --start 0 --end 30 \
      --intro-title "BIG IDEA" --outro-title "Follow for more" \
      --handle "@yourbrand" --platforms tiktok,instagram,youtube,x \
      --no-intro            # skip the intro
"""
import argparse
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Capture the caller's CWD before we chdir into backend/ so relative video
# paths on the command line still resolve correctly.
_INVOKE_CWD = os.getcwd()
sys.path.insert(0, os.path.join(ROOT, "backend"))
os.chdir(os.path.join(ROOT, "backend"))

FFMPEG = os.environ.get("PODCLI_FFMPEG", "ffmpeg")
NODE = os.environ.get("PODCLI_NODE", "node")

# Brand config: remembered handle / platforms / colors / outro title so they
# don't have to be retyped each run. CLI flags override these; these override
# the built-in defaults below.
# Note: .env may set PODCLI_HOME to an empty string, so treat empty as unset.
_PODCLI_HOME = os.environ.get("PODCLI_HOME") or os.path.join(ROOT, ".podcli")
BRAND_PATH = os.path.join(_PODCLI_HOME, "brand.json")
BRAND_DEFAULTS = {
    "handle": None,
    "platforms": "tiktok,instagram,youtube,x",
    "outro_title": "Follow for more",
    "accent": "#FFE000",
    "bg": "#0B0B0F",
}


def _load_brand() -> dict:
    try:
        with open(BRAND_PATH) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_brand(data: dict):
    os.makedirs(os.path.dirname(BRAND_PATH), exist_ok=True)
    with open(BRAND_PATH, "w") as f:
        json.dump(data, f, indent=2)


def _probe_duration(path: str) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", path],
        capture_output=True, text=True,
    )
    try:
        return float(out.stdout.strip())
    except ValueError:
        return 0.0


def _transcribe(video: str, language: str | None):
    """Transcribe (cached) and return the word list."""
    from services.transcription import transcribe_file
    print("  [transcribe] running Whisper...", flush=True)
    res = transcribe_file(
        file_path=video, model_size="base", language=language,
        enable_diarization=False,
        progress_callback=lambda p, m: print(f"    {p}% {m}", flush=True),
    )
    return res.get("words", [])


def _find_paragraph(words: list, phrase: str) -> tuple[float, float]:
    """Find the time span of a paragraph by fuzzy-matching its text against
    the transcript word stream. Returns (start, end) seconds."""
    needle = "".join(c.lower() for c in phrase if c.isalnum() or c == " ").split()
    if not needle:
        raise SystemExit("Empty --paragraph text")
    hay = [("".join(c.lower() for c in w["word"] if c.isalnum())) for w in words]

    best_i, best_score = -1, 0
    win = len(needle)
    for i in range(0, max(1, len(hay) - win + 1)):
        window = hay[i:i + win]
        score = sum(1 for a, b in zip(window, needle) if a and (a == b or a.startswith(b) or b.startswith(a)))
        if score > best_score:
            best_score, best_i = score, i
    if best_i < 0 or best_score < max(1, win // 3):
        raise SystemExit(f"Could not confidently locate paragraph in transcript (best match {best_score}/{win} words).")
    start = float(words[best_i]["start"])
    end = float(words[min(best_i + win - 1, len(words) - 1)]["end"])
    print(f"  [paragraph] matched {best_score}/{win} words → {start:.1f}s–{end:.1f}s", flush=True)
    return start, end


def _render_fragment(video, start, end, words, style, crop, title, out_dir):
    """Render the fragment with face-crop + captions via the existing engine."""
    from services.clip_generator import generate_clip
    print(f"  [fragment] rendering {start:.1f}s–{end:.1f}s ({style}, crop={crop})", flush=True)
    res = generate_clip(
        video_path=video, start_second=start, end_second=end,
        caption_style=style, crop_strategy=crop,
        transcript_words=words, title=title, output_dir=out_dir,
        clean_fillers=True, allow_ass_fallback=True,
        progress_callback=lambda p, m: print(f"    {p}% {m}", flush=True),
    )
    return res["output_path"]


def _render_bookend(mode, title, handle, platforms, seconds, out_path, accent, bg):
    cmd = [
        NODE, os.path.join(ROOT, "remotion", "render-bookend.mjs"),
        "--mode", mode, "--title", title,
        "--platforms", ",".join(platforms),
        "--seconds", str(seconds), "--output", out_path,
        "--accent", accent, "--bg", bg,
    ]
    if handle:
        cmd += ["--handle", handle]
    print(f"  [{mode}] rendering bookend...", flush=True)
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0 or not os.path.exists(out_path):
        raise SystemExit(f"Bookend ({mode}) render failed:\n{r.stderr[-800:]}")
    return out_path


def _concat(parts: list[str], out_path: str, fps: int = 30):
    """Concatenate parts (intro + clip + outro) in a single ffmpeg pass.

    Every part is normalized to 1080x1920 @ fps with stereo 44.1k audio inside
    the filtergraph, then joined with the concat filter. This gives an exact
    summed duration (no crossfade-offset drift) and a single clean re-encode.
    """
    n = len(parts)
    inputs = []
    for p in parts:
        inputs += ["-i", p]

    fc = []
    labels = []
    for i in range(n):
        # Normalize video: scale to fit 1080x1920, pad, set fps + SAR + format.
        fc.append(
            f"[{i}:v]scale=1080:1920:force_original_aspect_ratio=decrease,"
            f"pad=1080:1920:(ow-iw)/2:(oh-ih)/2:black,setsar=1,fps={fps},format=yuv420p[v{i}];"
        )
        # Normalize audio to a common format so concat doesn't choke.
        fc.append(
            f"[{i}:a]aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo[a{i}];"
        )
        labels.append(f"[v{i}][a{i}]")
    fc.append("".join(labels) + f"concat=n={n}:v=1:a=1[v][a]")
    filtergraph = "".join(fc)

    cmd = [
        FFMPEG, "-y", *inputs,
        "-filter_complex", filtergraph,
        "-map", "[v]", "-map", "[a]",
        "-c:v", "libx264", "-crf", os.environ.get("PODCLI_CONCAT_CRF", "18"),
        "-preset", "medium", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k", "-ar", "44100",
        "-movflags", "+faststart",
        out_path,
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0 or not os.path.exists(out_path):
        raise SystemExit(f"Concat failed:\n{r.stderr[-1000:]}")
    return out_path


def main():
    ap = argparse.ArgumentParser(description="Cut a fragment + add intro/outro bookends.")
    ap.add_argument("video")
    ap.add_argument("--start", type=float, help="Fragment start (seconds)")
    ap.add_argument("--end", type=float, help="Fragment end (seconds)")
    ap.add_argument("--paragraph", help="Find fragment by matching this text in the transcript")
    ap.add_argument("--language", default=None, help="Transcription language (e.g. es). Auto-detect if omitted.")
    ap.add_argument("--caption-style", default="hormozi", choices=["hormozi", "karaoke", "subtle", "branded"])
    ap.add_argument("--crop", default="face", choices=["center", "face", "speaker", "speaker-hardcut"])
    ap.add_argument("--output", default=None, help="Final output path")
    # bookends (defaults are None so we can tell what the user explicitly set;
    # unset values fall back to the saved brand config, then BRAND_DEFAULTS)
    ap.add_argument("--intro-title", default=None, help="Intro headline (default: derived from first words)")
    ap.add_argument("--outro-title", default=None, help="Outro call-to-action (brand default: 'Follow for more')")
    ap.add_argument("--handle", default=None, help="Handle shown on cards, e.g. @yourbrand")
    ap.add_argument("--platforms", default=None, help="Comma-separated: tiktok,instagram,youtube,x")
    ap.add_argument("--intro-seconds", type=float, default=2.0)
    ap.add_argument("--outro-seconds", type=float, default=3.0)
    ap.add_argument("--accent", default=None, help="Accent color hex (brand default: #FFE000)")
    ap.add_argument("--bg", default=None, help="Background color hex (brand default: #0B0B0F)")
    ap.add_argument("--no-intro", action="store_true")
    ap.add_argument("--no-outro", action="store_true")
    ap.add_argument("--save-brand", action="store_true",
                    help="Save the given handle/platforms/outro-title/accent/bg as the default brand and exit")
    args = ap.parse_args()

    # Resolve brand fields: CLI flag > saved brand.json > BRAND_DEFAULTS
    brand = {**BRAND_DEFAULTS, **_load_brand()}
    handle = args.handle if args.handle is not None else brand["handle"]
    platforms_str = args.platforms if args.platforms is not None else brand["platforms"]
    outro_title = args.outro_title if args.outro_title is not None else brand["outro_title"]
    accent = args.accent if args.accent is not None else brand["accent"]
    bg = args.bg if args.bg is not None else brand["bg"]

    if args.save_brand:
        saved = {"handle": handle, "platforms": platforms_str,
                 "outro_title": outro_title, "accent": accent, "bg": bg}
        _save_brand(saved)
        print(f"  ✓ Brand saved to {BRAND_PATH}")
        for k, v in saved.items():
            print(f"      {k}: {v}")
        return

    video = args.video if os.path.isabs(args.video) else os.path.join(_INVOKE_CWD, args.video)
    video = os.path.abspath(video)
    if not os.path.exists(video):
        raise SystemExit(f"Video not found: {video}")
    out_dir = os.path.join(ROOT, "data", "output")
    os.makedirs(out_dir, exist_ok=True)

    # Need a transcript if cutting by paragraph or if rendering captions.
    words = _transcribe(video, args.language)

    if args.paragraph:
        start, end = _find_paragraph(words, args.paragraph)
    elif args.start is not None and args.end is not None:
        start, end = args.start, args.end
    else:
        raise SystemExit("Provide either --start/--end or --paragraph")

    # 1. Fragment
    fragment = _render_fragment(
        video, start, end, words, args.caption_style, args.crop, "fragment", out_dir,
    )

    platforms = [p.strip() for p in platforms_str.split(",") if p.strip()]
    parts = []

    # 2. Intro (optional)
    if not args.no_intro:
        intro_title = args.intro_title
        if not intro_title:
            # derive a short headline from the first ~4 words of the fragment
            frag_words = [w["word"] for w in words if start <= w["start"] < end][:4]
            intro_title = " ".join(frag_words) or "Watch this"
        intro = _render_bookend(
            "intro", intro_title, handle, platforms,
            args.intro_seconds, os.path.join(out_dir, "_intro.mp4"), accent, bg,
        )
        parts.append(intro)

    parts.append(fragment)

    # 3. Outro (optional)
    if not args.no_outro:
        outro = _render_bookend(
            "outro", outro_title, handle, platforms,
            args.outro_seconds, os.path.join(out_dir, "_outro.mp4"), accent, bg,
        )
        parts.append(outro)

    # 4. Stitch
    if args.output:
        final = args.output if os.path.isabs(args.output) else os.path.join(_INVOKE_CWD, args.output)
    else:
        final = os.path.join(out_dir, "studio_final.mp4")
    if len(parts) == 1:
        # no bookends — fragment is the result
        import shutil
        shutil.copy(parts[0], final)
    else:
        _concat(parts, final)

    dur = _probe_duration(final)
    print(f"\n  ✓ DONE  {final}")
    print(f"    duration={dur:.1f}s  (intro {'on' if not args.no_intro else 'off'}, "
          f"outro {'on' if not args.no_outro else 'off'})")


if __name__ == "__main__":
    main()
