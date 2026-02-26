#!/usr/bin/env python3
"""
podcli — Python Backend Entry Point

Reads a JSON task request from stdin, dispatches to the appropriate service,
and writes a JSON result to stdout. Progress events go to stderr.
"""

import json
import sys
import traceback

def emit_progress(task_id: str, stage: str, percent: int, message: str):
    """Write a progress event to stderr (picked up by TypeScript executor)."""
    event = {
        "task_id": task_id,
        "stage": stage,
        "percent": percent,
        "message": message,
    }
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
    emit_result(task_id, "success", data={"message": "pong", "version": "1.0.0"})


def handle_transcribe(task_id: str, params: dict):
    """Transcribe a podcast video/audio file with speaker detection."""
    from services.transcription import transcribe_file

    emit_progress(task_id, "transcribing", 0, "Starting transcription...")
    result = transcribe_file(
        file_path=params["file_path"],
        model_size=params.get("model_size", "base"),
        language=params.get("language"),
        enable_diarization=params.get("enable_diarization", True),
        num_speakers=params.get("num_speakers"),
        progress_callback=lambda pct, msg: emit_progress(task_id, "transcribing", pct, msg),
    )
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
        crop_strategy=params.get("crop_strategy", "center"),
        transcript_words=params.get("transcript_words", []),
        title=params.get("title", "clip"),
        output_dir=params.get("output_dir"),
        logo_path=params.get("logo_path"),
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
                crop_strategy=clip.get("crop_strategy", "center"),
                transcript_words=params.get("transcript_words", []),
                title=clip.get("title", f"clip_{i + 1}"),
                output_dir=params.get("output_dir"),
                logo_path=clip.get("logo_path") or params.get("logo_path"),
                progress_callback=lambda pct, msg: emit_progress(
                    task_id, "batch", int((i / total) * 100 + pct / total), msg
                ),
            )
            results.append({"clip_index": i, "status": "success", **result})
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
    from services.transcript_parser import parse_speaker_transcript

    raw_text = params.get("raw_text", "")
    total_duration = params.get("total_duration")
    time_adjust = params.get("time_adjust", 0.0)

    if not raw_text:
        emit_result(task_id, "error", error="raw_text is required")
        return

    emit_progress(task_id, "parsing", 50, "Parsing transcript...")
    result = parse_speaker_transcript(raw_text, total_duration=total_duration, time_adjust=time_adjust)

    if "error" in result:
        emit_result(task_id, "error", error=result["error"])
        return

    emit_progress(task_id, "parsing", 100, "Transcript parsed!")
    emit_result(task_id, "success", data=result)


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
    elif action == "get":
        try:
            preset = get_preset(name)
            emit_result(task_id, "success", data=preset)
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


TASK_HANDLERS = {
    "ping": handle_ping,
    "transcribe": handle_transcribe,
    "parse_transcript": handle_parse_transcript,
    "create_clip": handle_create_clip,
    "batch_clips": handle_batch_clips,
    "analyze_energy": handle_analyze_energy,
    "detect_encoder": handle_detect_encoder,
    "presets": handle_presets,
}


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
