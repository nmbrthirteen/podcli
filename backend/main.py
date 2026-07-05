#!/usr/bin/env python3
"""
podcli — Python Backend Entry Point

Reads a JSON task request from stdin, dispatches to the appropriate service,
and writes a JSON result to stdout. Progress events go to stderr.
"""

import json
import os
import sys

# Windows stdout/stderr default to cp1252, which can't encode chars like '→'; IPC is UTF-8.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            pass

# Load .env file (for HF_TOKEN, etc.)
try:
    from dotenv import load_dotenv
    load_dotenv(os.environ.get("PODCLI_ENV_FILE") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".env"))
except ImportError:
    pass
import traceback
from version import VERSION


def emit_progress(task_id: str, stage: str, percent: int, message: str, **extra):
    """Write a progress event to stderr (picked up by TypeScript executor)."""
    event = {
        "task_id": task_id,
        "stage": stage,
        "percent": percent,
        "message": message,
    }
    event.update(extra)
    print(json.dumps(event), file=sys.stderr, flush=True)


def emit_result(task_id: str, status: str, data=None, error=None):
    """Write the final result to stdout."""
    result = {
        "task_id": task_id,
        "status": status,
    }
    if data is not None:
        result["data"] = data
    if error is not None:
        result["error"] = error
    print(json.dumps(result), flush=True)


def handle_ping(task_id: str, params: dict):
    """Simple health check."""
    emit_result(task_id, "success", data={"message": "pong", "version": VERSION})


def handle_transcribe(task_id: str, params: dict):
    """Transcribe a podcast video/audio file with speaker detection."""
    from services.transcription import transcribe_file
    from services.corrections import apply_corrections
    from services.transcript_packer import compute_cache_hash, engine_cache_suffix, write_packed

    emit_progress(task_id, "transcribing", 0, "Starting transcription...")
    file_path = params["file_path"]
    engine = params.get("engine")
    previous_engine = os.environ.get("PODCLI_ENGINE")
    previous_assemblyai_key = os.environ.get("ASSEMBLYAI_API_KEY")
    if engine:
        os.environ["PODCLI_ENGINE"] = engine
    if params.get("assemblyai_api_key"):
        os.environ["ASSEMBLYAI_API_KEY"] = params["assemblyai_api_key"]
    try:
        result = transcribe_file(
            file_path=file_path,
            model_size=params.get("model_size", "base"),
            engine=engine,
            language=params.get("language"),
            enable_diarization=params.get("enable_diarization", True),
            num_speakers=params.get("num_speakers"),
            progress_callback=lambda pct, msg: emit_progress(task_id, "transcribing", pct, msg),
        )
    finally:
        if previous_engine is None:
            os.environ.pop("PODCLI_ENGINE", None)
        else:
            os.environ["PODCLI_ENGINE"] = previous_engine
        if previous_assemblyai_key is None:
            os.environ.pop("ASSEMBLYAI_API_KEY", None)
        else:
            os.environ["ASSEMBLYAI_API_KEY"] = previous_assemblyai_key
    # Apply word corrections (Whisper misheard proper nouns)
    apply_corrections(result.get("words", []), result.get("segments", []))

    # Auto-pack: emit compact LLM-readable markdown alongside raw JSON.
    # Pulls energy data so the packed view includes peak moments for clip reasoning.
    try:
        from services.audio_analyzer import extract_audio_energy

        cache_hash = compute_cache_hash(file_path) + engine_cache_suffix(result.get("engine") or engine)
        energy_data = None
        try:
            energy_data = extract_audio_energy(file_path)
        except Exception:
            pass  # energy is a nice-to-have

        events_data = None
        try:
            from services.audio_events import extract_audio_events
            events_data = extract_audio_events(file_path)
        except Exception:
            pass  # reactions are a nice-to-have

        packed_path, packed_md = write_packed(
            result,
            cache_hash,
            source_label=os.path.basename(file_path),
            energy_data=energy_data,
            events_data=events_data,
        )
        result["packed_path"] = packed_path
        result["packed_size_bytes"] = len(packed_md.encode("utf-8"))
    except Exception as e:
        # Non-fatal — transcription result is still useful without the packed view
        emit_progress(task_id, "packing", 99, f"Packer skipped: {e}")

    emit_result(task_id, "success", data=result)


def handle_create_clip(task_id: str, params: dict):
    """Create a single finished short-form clip."""
    from services.clip_generator import generate_clip

    emit_progress(task_id, "starting", 0, "Preparing clip...")
    result = generate_clip(
        video_path=params["video_path"],
        start_second=params["start_second"],
        end_second=params["end_second"],
        caption_style=params.get("caption_style", "hormozi"),
        crop_strategy=params.get("crop_strategy", "face"),
        format=params.get("format", "vertical"),
        crop_keyframes=params.get("crop_keyframes"),
        transcript_words=params.get("transcript_words", []),
        title=params.get("title", "clip"),
        output_dir=params.get("output_dir"),
        logo_path=params.get("logo_path"),
        outro_path=params.get("outro_path"),
        clean_fillers=params.get("clean_fillers", True),
        face_map=params.get("face_map"),
        keep_segments=params.get("keep_segments"),
        allow_ass_fallback=params.get("allow_ass_fallback", False),
        use_ass_captions=params.get("use_ass_captions", False),
        keep_caption_overlay=params.get("keep_caption_overlay", False),
        progress_callback=lambda pct, msg: emit_progress(task_id, "processing", pct, msg),
    )
    emit_result(task_id, "success", data=result)


def handle_batch_clips(task_id: str, params: dict):
    """Create multiple clips in sequence."""
    from services.clip_generator import generate_clip

    clips = params["clips"]
    results = []
    total = len(clips)

    for i, clip in enumerate(clips):
        emit_progress(
            task_id,
            "batch",
            int((i / total) * 100),
            f"Processing clip {i + 1}/{total}...",
        )
        try:
            result = generate_clip(
                video_path=params["video_path"],
                start_second=clip["start_second"],
                end_second=clip["end_second"],
                caption_style=clip.get("caption_style", "hormozi"),
                crop_strategy=clip.get("crop_strategy", "face"),
                format=clip.get("format", params.get("format", "vertical")),
                transcript_words=params.get("transcript_words", []),
                title=clip.get("title", f"clip_{i + 1}"),
                output_dir=params.get("output_dir"),
                logo_path=clip.get("logo_path") or params.get("logo_path"),
                outro_path=params.get("outro_path"),
                clean_fillers=params.get("clean_fillers", True),
                face_map=params.get("face_map"),
                keep_segments=clip.get("keep_segments"),
                allow_ass_fallback=clip.get("allow_ass_fallback", params.get("allow_ass_fallback", False)),
                use_ass_captions=clip.get("use_ass_captions", params.get("use_ass_captions", False)),
                keep_caption_overlay=clip.get(
                    "keep_caption_overlay", params.get("keep_caption_overlay", False)
                ),
                progress_callback=lambda pct, msg, _i=i: emit_progress(
                    task_id, "batch", int((_i / total) * 100 + pct / total), msg
                ),
            )
            row = {
                "clip_index": i,
                "status": "success",
                "start_second": clip["start_second"],
                "end_second": clip["end_second"],
                **result,
            }
            results.append(row)
            emit_progress(
                task_id,
                "clip_complete",
                int(((i + 1) / total) * 100),
                f"Clip {i + 1}/{total} complete",
                clip_result=row,
            )
        except Exception as e:
            results.append({"clip_index": i, "status": "error", "error": str(e)})

    emit_result(
        task_id,
        "success",
        data={
            "results": results,
            "total_clips": total,
            "successful_clips": sum(1 for r in results if r["status"] == "success"),
        },
    )


def handle_parse_transcript(task_id: str, params: dict):
    """Parse a speaker-labeled transcript into word-level timestamps."""
    import hashlib
    from services.transcript_parser import detect_and_parse
    from services.corrections import apply_corrections
    from services.transcript_packer import write_packed

    raw_text = params.get("raw_text", "")
    total_duration = params.get("total_duration")
    time_adjust = params.get("time_adjust", 0.0)

    if not raw_text:
        emit_result(task_id, "error", error="raw_text is required")
        return

    emit_progress(task_id, "parsing", 50, "Parsing transcript...")
    result = detect_and_parse(raw_text, total_duration=total_duration, time_adjust=time_adjust)

    if "error" in result:
        emit_result(task_id, "error", error=result["error"])
        return

    # Apply word corrections (proper nouns, brand names)
    apply_corrections(result.get("words", []), result.get("segments", []))

    # Auto-pack: key pasted transcripts by content hash since there's no source file.
    try:
        content_hash = hashlib.sha256(raw_text.encode("utf-8")).hexdigest()[:16]
        packed_path, packed_md = write_packed(
            result, content_hash, source_label="pasted-transcript"
        )
        result["packed_path"] = packed_path
        result["packed_size_bytes"] = len(packed_md.encode("utf-8"))
        result["content_hash"] = content_hash
    except Exception as e:
        emit_progress(task_id, "packing", 99, f"Packer skipped: {e}")

    emit_progress(task_id, "parsing", 100, "Transcript parsed!")
    emit_result(task_id, "success", data=result)


def handle_pack_transcript(task_id: str, params: dict):
    """Pack a transcript dict into LLM-readable markdown. Used to backfill
    the packed view for caches that predate auto-packing."""
    from services.transcript_packer import write_packed

    transcript = params.get("transcript")
    cache_hash = params.get("cache_hash")
    if not transcript or not cache_hash:
        emit_result(task_id, "error", error="transcript and cache_hash are required")
        return

    energy_data = params.get("energy_data")
    events_data = params.get("events_data")
    if params.get("file_path"):
        if energy_data is None:
            try:
                from services.audio_analyzer import extract_audio_energy
                energy_data = extract_audio_energy(params["file_path"])
            except Exception:
                pass
        if events_data is None:
            try:
                from services.audio_events import extract_audio_events
                events_data = extract_audio_events(params["file_path"])
            except Exception:
                pass

    path, md = write_packed(
        transcript,
        cache_hash,
        source_label=params.get("source_label"),
        energy_data=energy_data,
        events_data=events_data,
    )
    emit_result(task_id, "success", data={
        "packed_path": path,
        "size_bytes": len(md.encode("utf-8")),
    })


def handle_analyze_energy(task_id: str, params: dict):
    """Analyze audio energy of a video to improve clip scoring."""
    from services.audio_analyzer import get_energy_profile

    video_path = params.get("video_path", "")
    segments = params.get("segments", [])

    if not video_path:
        emit_result(task_id, "error", error="video_path is required")
        return

    result = get_energy_profile(
        video_path=video_path,
        segments=segments,
        progress_callback=lambda pct, msg: emit_progress(task_id, "analyzing", pct, msg),
    )
    emit_result(task_id, "success", data=result)


def handle_detect_highlights(task_id: str, params: dict):
    """Detect highlight clips from a video's fused signal curve (party/action profiles).

    Accepts a single `video_path`, or a list of `video_paths` to pool and rank
    highlights across a whole folder of clips.
    """
    from services.saliency import detect_highlights, detect_highlights_pooled

    video_paths = params.get("video_paths")
    video_path = params.get("video_path", "")
    if not video_paths and not video_path:
        emit_result(task_id, "error", error="video_path or video_paths is required")
        return

    common = dict(
        profile_name=params.get("profile", "party"),
        min_dur=float(params.get("min_dur", 8.0)),
        max_dur=float(params.get("max_dur", 60.0)),
        progress_callback=lambda pct, msg: emit_progress(task_id, "detecting", pct, msg),
    )
    if video_paths:
        clips = detect_highlights_pooled(
            video_paths=video_paths, top_n=int(params.get("top_n", 15)), **common
        )
    else:
        clips = detect_highlights(
            video_path=video_path, top_n=int(params.get("top_n", 8)), **common
        )
    emit_result(task_id, "success", data={"clips": clips, "count": len(clips)})


def handle_manage_reel(task_id: str, params: dict):
    """Create and iterate on a highlights reel — the MCP surface over the reel service.

    Actions: new (detect + build), show, edit (adjust one moment + rebuild), build.
    """
    from dataclasses import asdict
    from services.reel import ReelSession, seed_session, edit_moment, build_reel

    action = params.get("action", "show")

    def payload(session, reel=None):
        return {
            "session_id": session.session_id,
            "out_dir": session.out_dir,
            "reel_path": reel,
            "moments": [asdict(m) for m in session.moments],
        }

    try:
        if action == "new":
            from services.transcript_packer import compute_cache_hash, load_cached_transcript_for_video
            video = params["video_path"]
            sid = compute_cache_hash(video)
            out_dir = params.get("out_dir") or os.path.join(os.getcwd(), f"reel_{sid[:8]}")
            cached = load_cached_transcript_for_video(video)
            words = cached.get("words") if cached else None
            session = seed_session(
                sid, video, out_dir, profile=params.get("profile", "party"),
                top_n=int(params.get("top_n", 10)), words=words,
                progress_callback=lambda p, m: emit_progress(task_id, "detecting", p, m),
            )
            reel = build_reel(session, progress_callback=lambda p, m: emit_progress(task_id, "building", p, m))
            emit_result(task_id, "success", data=payload(session, reel))
        elif action == "show":
            emit_result(task_id, "success", data=payload(ReelSession.load(params["session_id"])))
        elif action == "edit":
            session = edit_moment(
                ReelSession.load(params["session_id"]),
                int(params["index"]), params["op"], float(params.get("seconds", 0.0)),
            )
            reel = build_reel(session, progress_callback=lambda p, m: emit_progress(task_id, "building", p, m))
            emit_result(task_id, "success", data=payload(session, reel))
        elif action == "build":
            session = ReelSession.load(params["session_id"])
            reel = build_reel(session, progress_callback=lambda p, m: emit_progress(task_id, "building", p, m))
            emit_result(task_id, "success", data=payload(session, reel))
        else:
            emit_result(task_id, "error", error=f"unknown reel action {action!r}")
    except (KeyError, IndexError, ValueError, FileNotFoundError) as e:
        emit_result(task_id, "error", error=str(e))


def handle_detect_encoder(task_id: str, params: dict):
    """Detect available hardware encoders."""
    from services.encoder import get_encoder_info
    emit_result(task_id, "success", data=get_encoder_info())


def handle_presets(task_id: str, params: dict):
    """Manage presets (list, get, save, delete)."""
    from presets import list_presets, get_preset, save_preset, delete_preset

    action = params.get("action", "list")
    name = params.get("name", "default")

    if action == "list":
        emit_result(task_id, "success", data={"presets": list_presets()})
    elif action in ("get", "load"):  # MCP manage_presets uses "load"
        try:
            preset = get_preset(name)
            emit_result(task_id, "success", data={"config": preset, "name": name})
        except FileNotFoundError as e:
            emit_result(task_id, "error", error=str(e))
    elif action == "save":
        config = params.get("config", {})
        path = save_preset(name, config)
        emit_result(task_id, "success", data={"path": path, "name": name})
    elif action == "delete":
        ok = delete_preset(name)
        emit_result(task_id, "success", data={"deleted": ok})
    else:
        emit_result(task_id, "error", error=f"Unknown presets action: {action}")


def handle_corrections(task_id: str, params: dict):
    """Manage transcript word corrections (get, set, add, remove)."""
    from services.corrections import get_corrections, save_corrections

    action = params.get("action", "get")

    if action == "get":
        emit_result(task_id, "success", data={"corrections": get_corrections()})
    elif action == "set":
        corrections = params.get("corrections", {})
        path = save_corrections(corrections)
        emit_result(task_id, "success", data={"corrections": corrections, "path": path})
    elif action == "add":
        wrong = params.get("wrong", "")
        correct = params.get("correct", "")
        if not wrong or not correct:
            emit_result(task_id, "error", error="'wrong' and 'correct' are required")
            return
        current = get_corrections()
        current[wrong] = correct
        save_corrections(current)
        emit_result(task_id, "success", data={"corrections": current})
    elif action == "remove":
        wrong = params.get("wrong", "")
        current = get_corrections()
        current.pop(wrong, None)
        save_corrections(current)
        emit_result(task_id, "success", data={"corrections": current})
    else:
        emit_result(task_id, "error", error=f"Unknown corrections action: {action}")


def handle_suggest_clips(task_id: str, params: dict):
    """AI-powered clip suggestion using Claude/Codex and PodStack knowledge base."""
    from services.claude_suggest import suggest_with_claude, _find_ai_cli_candidates

    segments = params.get("segments", [])
    top_n = params.get("top_n", 5)

    if not segments:
        emit_result(task_id, "error", error="segments is required")
        return

    if not _find_ai_cli_candidates():
        emit_result(
            task_id,
            "error",
            error=(
                "No AI CLI available (install Claude Code or Codex). "
                "If already installed, set the path in Config → AI CLI or PODCLI_CLAUDE_PATH."
            ),
        )
        return

    clips = suggest_with_claude(
        segments=segments,
        top_n=top_n,
        progress_callback=lambda pct, msg: emit_progress(task_id, "suggesting", pct, msg),
    )

    if clips is None:
        emit_result(
            task_id,
            "error",
            error="AI CLI found but suggestion failed — check claude/codex login and try again",
        )
        return

    emit_result(task_id, "success", data={"clips": clips})


def handle_manage_env(task_id: str, params: dict):
    """List/set/unset user secrets in the global .env (e.g. HF_TOKEN)."""
    from services.env_settings import run_env_action

    try:
        data = run_env_action(params.get("action", "list"), params.get("key"), params.get("value"))
    except ValueError as e:
        emit_result(task_id, "error", error=str(e))
        return
    emit_result(task_id, "success", data=data)


def handle_ai_cli_status(task_id: str, params: dict):
    from services.claude_suggest import get_ai_cli_status

    emit_result(task_id, "success", data=get_ai_cli_status())


def handle_find_moment(task_id: str, params: dict):
    """Locate user-pasted/described moments in the transcript via the AI CLI."""
    from services.claude_suggest import find_moments_from_text

    text = (params.get("text") or params.get("description") or "").strip()
    segments = params.get("segments", [])
    existing_clips = params.get("existing_clips", [])
    max_results = params.get("max_results", 8)

    if not text:
        emit_result(task_id, "error", error="text is required")
        return
    if not segments:
        emit_result(task_id, "error", error="segments is required")
        return

    clips = find_moments_from_text(
        text,
        segments,
        existing_clips,
        progress_callback=lambda pct, msg: emit_progress(task_id, "searching", pct, msg),
        max_results=max_results,
    )
    emit_result(task_id, "success", data={"clips": clips})


def handle_generate_content(task_id: str, params: dict):
    """Generate titles, descriptions, tags for a clip using PodStack knowledge base."""
    from services.content_generator import generate_clip_content
    from services.claude_suggest import _find_ai_cli_candidates

    clip = params.get("clip", {})
    transcript_segments = params.get("transcript_segments", [])

    if not clip:
        emit_result(task_id, "error", error="clip is required")
        return

    if not _find_ai_cli_candidates():
        emit_result(
            task_id,
            "error",
            error=(
                "No AI CLI available (install Claude Code or Codex). "
                "If already installed, set the path in Config → AI CLI or PODCLI_CLAUDE_PATH."
            ),
        )
        return

    result = generate_clip_content(
        clip=clip,
        transcript_segments=transcript_segments,
        progress_callback=lambda pct, msg: emit_progress(task_id, "generating", pct, msg),
        mode=params.get("mode", "shorts"),
        partial_callback=lambda partial: emit_progress(
            task_id, "generating", 60, "Writing content...", partial=partial
        ),
    )

    if result is None:
        emit_result(
            task_id,
            "error",
            error="AI CLI found but content generation failed — check claude/codex login and try again",
        )
        return

    emit_result(task_id, "success", data=result)


def handle_generate_custom(task_id: str, params: dict):
    """Run a free-form content request against the AI CLI with KB + transcript context."""
    from services.content_generator import generate_custom_content

    instruction = str(params.get("instruction", "")).strip()
    if not instruction:
        emit_result(task_id, "error", error="instruction is required")
        return

    result = generate_custom_content(
        instruction=instruction,
        transcript_segments=params.get("transcript_segments", []),
        mode=params.get("mode", "shorts"),
        progress_callback=lambda pct, msg: emit_progress(task_id, "generating", pct, msg),
    )

    if result is None:
        emit_result(task_id, "error", error="No AI CLI available (install Claude Code or Codex)")
        return

    emit_result(task_id, "success", data=result)


def handle_manage_integrations(task_id: str, params: dict):
    from services.integrations import IntegrationsManager

    manager = IntegrationsManager()
    action = params.get("action", "list")

    if action == "list":
        emit_result(task_id, "success", data={"integrations": manager.list_all()})
        return

    name = params.get("name", "")
    if action in ("enable", "disable"):
        try:
            manager.set_enabled(name, action == "enable")
            emit_result(task_id, "success", data={"name": name, "enabled": action == "enable"})
        except ValueError as e:
            emit_result(task_id, "error", error=str(e))
        return

    emit_result(task_id, "error", error=f"Unknown action: {action}")


def handle_manage_config(task_id: str, params: dict):
    from config_bundle import run_config_action

    try:
        data = run_config_action(
            params.get("action", "status"),
            bundle_path=params.get("bundle_path") or params.get("bundle"),
            home=params.get("home"),
            activate=bool(params.get("activate", False)),
            dry_run=bool(params.get("dry_run", False)),
        )
        emit_result(task_id, "success", data=data)
    except ValueError as e:
        emit_result(task_id, "error", error=str(e))


def handle_run_integration_tool(task_id: str, params: dict):
    from services.integrations import IntegrationRegistry, IntegrationsManager

    integration_name = params.get("integration", "")
    tool_name = params.get("tool", "")
    tool_params = params.get("params", {})

    manager = IntegrationsManager()
    if not manager.is_enabled(integration_name):
        emit_result(
            task_id,
            "error",
            error=f"Integration '{integration_name}' is disabled. Enable via manage_integrations.",
        )
        return

    integration = IntegrationRegistry.get(integration_name)
    if integration is None:
        emit_result(task_id, "error", error=f"Unknown integration: {integration_name}")
        return

    tool = next((t for t in integration.tools() if t.name == tool_name), None)
    if tool is None:
        emit_result(task_id, "error", error=f"Unknown tool '{tool_name}' on '{integration_name}'")
        return

    try:
        result = tool.handler(tool_params)
        emit_result(task_id, "success", data=result)
    except Exception as e:
        print(traceback.format_exc(), file=sys.stderr, flush=True)
        emit_result(task_id, "error", error=f"{type(e).__name__}: {e}")


TASK_HANDLERS = {
    "ping": handle_ping,
    "transcribe": handle_transcribe,
    "parse_transcript": handle_parse_transcript,
    "create_clip": handle_create_clip,
    "batch_clips": handle_batch_clips,
    "analyze_energy": handle_analyze_energy,
    "detect_highlights": handle_detect_highlights,
    "manage_reel": handle_manage_reel,
    "pack_transcript": handle_pack_transcript,
    "detect_encoder": handle_detect_encoder,
    "presets": handle_presets,
    "corrections": handle_corrections,
    "suggest_clips": handle_suggest_clips,
    "find_moment": handle_find_moment,
    "manage_env": handle_manage_env,
    "ai_cli_status": handle_ai_cli_status,
    "generate_content": handle_generate_content,
    "generate_custom": handle_generate_custom,
    "manage_integrations": handle_manage_integrations,
    "run_integration_tool": handle_run_integration_tool,
    "manage_config": handle_manage_config,
}


def _maybe_auto_migrate_backend(task_type: str, params: dict) -> None:
    if task_type == "manage_config":
        action = params.get("action", "status")
        if action == "status" or (action == "migrate" and params.get("dry_run")):
            return
    try:
        from config_bundle import auto_migrate_legacy_if_pending

        auto_migrate_legacy_if_pending(quiet=True)
    except Exception as e:
        print(f"Warning: auto-migrate skipped: {e}", file=sys.stderr, flush=True)


def main():
    try:
        raw = sys.stdin.readline().strip()
        if not raw:
            emit_result("unknown", "error", error="Empty input")
            sys.exit(1)

        request = json.loads(raw)
        task_id = request.get("task_id", "unknown")
        task_type = request.get("task_type", "")
        params = request.get("params", {})

        _maybe_auto_migrate_backend(task_type, params)

        handler = TASK_HANDLERS.get(task_type)
        if not handler:
            emit_result(task_id, "error", error=f"Unknown task type: {task_type}")
            sys.exit(1)

        handler(task_id, params)

    except Exception as e:
        task_id = "unknown"
        try:
            task_id = json.loads(raw).get("task_id", "unknown")
        except Exception:
            pass
        emit_result(task_id, "error", error=f"{type(e).__name__}: {e}\n{traceback.format_exc()}")
        sys.exit(1)


if __name__ == "__main__":
    main()
