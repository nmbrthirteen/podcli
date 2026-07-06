"""
Transcription service using OpenAI Whisper + speaker diarization.

Produces word-level timestamps with speaker labels by:
1. Running Whisper for speech-to-text with word timing
2. Running pyannote speaker diarization (if available)
3. Merging speaker labels onto each word and segment
"""

import json
import http.client
import os
import shutil
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Optional, Callable


def _managed_home() -> str:
    h = os.environ.get("PODCLI_HOME")
    if h:
        return h
    home = os.path.expanduser("~")
    if sys.platform == "darwin":
        return os.path.join(home, "Library", "Application Support", "podcli")
    if sys.platform == "win32":
        return os.environ.get("LOCALAPPDATA") or os.path.join(home, "AppData", "Local", "podcli")
    return os.environ.get("XDG_DATA_HOME") or os.path.join(home, ".local", "share", "podcli")


def _whispercpp_cli() -> Optional[str]:
    """Resolve the whisper.cpp binary: explicit env, PATH, then the hermetic
    runtime location the native installer provisions."""
    cli = os.environ.get("PODCLI_WHISPER_CLI")
    if cli and (os.path.exists(cli) or shutil.which(cli)):
        return cli
    found = shutil.which("whisper-cli") or shutil.which("whisper-cpp")
    if found:
        return found
    exe = "whisper-cli.exe" if sys.platform == "win32" else "whisper-cli"
    hermetic = os.path.join(_managed_home(), "runtime", "whisper", exe)
    return hermetic if os.path.exists(hermetic) else None


def _whispercpp_model(model_size: str) -> str:
    return os.environ.get("PODCLI_WHISPERCPP_MODEL") or os.path.join(
        _managed_home(), "models", f"ggml-{model_size}.bin"
    )


def _whispercpp_ready(model_size: str) -> bool:
    return _whispercpp_cli() is not None and os.path.exists(_whispercpp_model(model_size))


def _transcribe_with_whispercpp(file_path, model_size, language, progress_callback):
    from services import transcription_whispercpp as wcpp

    if progress_callback:
        progress_callback(10, "Transcribing with whisper.cpp...")

    cli = _whispercpp_cli() or "whisper-cli"
    model = _whispercpp_model(model_size)
    if not os.path.exists(model):
        raise FileNotFoundError(
            f"whisper.cpp model not found: {model}. "
            "Set PODCLI_WHISPERCPP_MODEL or run provisioning."
        )
    vad = os.environ.get("PODCLI_WHISPERCPP_VAD", "").strip().lower() in ("1", "true", "yes", "on")
    result = wcpp.transcribe_file(
        file_path,
        model_path=model,
        whisper_cli=cli,
        ffmpeg=os.environ.get("PODCLI_FFMPEG", "ffmpeg"),
        language=language,
        vad=vad,
        vad_model=os.environ.get("PODCLI_WHISPERCPP_VAD_MODEL") or None,
    )
    if progress_callback:
        progress_callback(50, "Transcription complete")
    return result


def _assemblyai_base_url() -> str:
    region = os.environ.get("ASSEMBLYAI_REGION", "").strip().lower()
    if region == "eu":
        return "https://api.eu.assemblyai.com/v2"
    return "https://api.assemblyai.com/v2"


def _assemblyai_json_request(method: str, url: str, api_key: str, payload: Optional[dict], timeout: int) -> dict:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {"Authorization": api_key}
    if body is not None:
        headers["Content-Type"] = "application/json"
    last_error = None
    for attempt in range(1, 4):
        try:
            req = urllib.request.Request(url, data=body, headers=headers, method=method)
            with urllib.request.urlopen(req, timeout=timeout) as res:
                return json.loads(res.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", errors="replace")
            last_error = RuntimeError(
                f"AssemblyAI request failed: method={method} url={url} status={e.code} body={detail}"
            )
            if e.code not in (408, 409, 425, 429) and e.code < 500:
                raise last_error from e
            print(
                f"Warning: AssemblyAI request retry {attempt}/3 failed: method={method} url={url} status={e.code} body={detail}",
                file=sys.stderr,
            )
            if attempt < 3:
                time.sleep(attempt)
        except (urllib.error.URLError, TimeoutError) as e:
            last_error = e
            print(
                f"Warning: AssemblyAI request retry {attempt}/3 failed: method={method} url={url} error={e}",
                file=sys.stderr,
            )
            if attempt < 3:
                time.sleep(attempt)
    raise RuntimeError(
        f"AssemblyAI request failed after retries: method={method} url={url} error={last_error}"
    ) from last_error


def _assemblyai_upload(file_path: str, api_key: str, base_url: str) -> str:
    url = f"{base_url}/upload"
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or not parsed.netloc:
        raise RuntimeError(f"AssemblyAI upload URL is invalid: url={url}")

    last_error = None
    for attempt in range(1, 4):
        conn = None
        try:
            size = os.path.getsize(file_path)
            conn = http.client.HTTPSConnection(parsed.netloc, timeout=3600)
            conn.putrequest("POST", parsed.path)
            conn.putheader("Authorization", api_key)
            conn.putheader("Content-Type", "application/octet-stream")
            conn.putheader("Content-Length", str(size))
            conn.endheaders()
            with open(file_path, "rb") as f:
                while True:
                    chunk = f.read(1024 * 1024)
                    if not chunk:
                        break
                    conn.send(chunk)

            res = conn.getresponse()
            detail = res.read().decode("utf-8", errors="replace")
            if res.status < 200 or res.status >= 300:
                last_error = RuntimeError(
                    f"AssemblyAI upload failed: url={url} file_path={file_path} status={res.status} body={detail}"
                )
                if res.status not in (408, 409, 425, 429) and res.status < 500:
                    raise last_error
                print(
                    f"Warning: AssemblyAI upload retry {attempt}/3 failed: file_path={file_path} status={res.status} body={detail}",
                    file=sys.stderr,
                )
                if attempt < 3:
                    time.sleep(attempt)
                continue

            data = json.loads(detail)
            upload_url = data.get("upload_url")
            if not upload_url:
                raise RuntimeError(f"AssemblyAI upload response missing upload_url: body={data}")
            return upload_url
        except (OSError, TimeoutError, http.client.HTTPException) as e:
            last_error = e
            print(
                f"Warning: AssemblyAI upload retry {attempt}/3 failed: file_path={file_path} error={e}",
                file=sys.stderr,
            )
            if attempt < 3:
                time.sleep(attempt)
        finally:
            if conn:
                conn.close()
    raise RuntimeError(
        f"AssemblyAI upload failed after retries: url={url} file_path={file_path} error={last_error}"
    ) from last_error


def _assemblyai_speaker(raw_speaker: Optional[str]) -> Optional[str]:
    if raw_speaker is None:
        return None
    label = str(raw_speaker).strip()
    if not label:
        return None
    if len(label) == 1 and label.isalpha():
        return f"SPEAKER_{ord(label.upper()) - ord('A'):02d}"
    return label


def _assemblyai_words(data: dict) -> list[dict]:
    return [
        {
            "word": str(w.get("text", "")).strip(),
            "start": round(float(w.get("start", 0)) / 1000.0, 3),
            "end": round(float(w.get("end", 0)) / 1000.0, 3),
            "confidence": round(float(w.get("confidence", 0)), 3),
            "speaker": _assemblyai_speaker(w.get("speaker")),
        }
        for w in data.get("words", [])
        if str(w.get("text", "")).strip()
    ]


def _assemblyai_result(data: dict) -> dict:
    words = _assemblyai_words(data)
    utterances = data.get("utterances") or []
    if utterances:
        segments = [
            {
                "id": i,
                "start": round(float(u.get("start", 0)) / 1000.0, 3),
                "end": round(float(u.get("end", 0)) / 1000.0, 3),
                "text": str(u.get("text", "")).strip(),
                "speaker": _assemblyai_speaker(u.get("speaker")),
            }
            for i, u in enumerate(utterances)
            if str(u.get("text", "")).strip()
        ]
    else:
        segments = [{
            "id": 0,
            "start": words[0]["start"] if words else 0.0,
            "end": words[-1]["end"] if words else 0.0,
            "text": str(data.get("text") or "").strip(),
            "speaker": None,
        }]

    speaker_segments = [
        {
            "speaker": segment["speaker"],
            "start": segment["start"],
            "end": segment["end"],
        }
        for segment in segments
        if segment["speaker"]
    ]
    speakers = sorted({s["speaker"] for s in speaker_segments})
    speaker_map = {
        speaker: {
            "label": speaker,
            "total_time": round(
                sum(s["end"] - s["start"] for s in speaker_segments if s["speaker"] == speaker),
                2,
            ),
            "segments": sum(1 for s in speaker_segments if s["speaker"] == speaker),
        }
        for speaker in speakers
    }
    return {
        "transcript": str(data.get("text") or "").strip(),
        "segments": segments,
        "words": words,
        "duration": round(float(data.get("audio_duration") or (words[-1]["end"] if words else 0.0)), 3),
        "language": str(data.get("language_code") or "en"),
        "speakers": {
            "num_speakers": len(speakers),
            "speakers": speaker_map,
        },
        "speaker_segments": speaker_segments,
        "engine": "assemblyai",
    }


def _transcribe_with_assemblyai(file_path, language, enable_diarization, num_speakers, progress_callback):
    api_key = os.environ.get("ASSEMBLYAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("ASSEMBLYAI_API_KEY is required when PODCLI_ENGINE=assemblyai")

    base_url = _assemblyai_base_url()
    if progress_callback:
        progress_callback(10, "Uploading media to AssemblyAI...")
    upload_url = _assemblyai_upload(file_path, api_key, base_url)

    payload = {
        "audio_url": upload_url,
        "punctuate": True,
        "format_text": True,
        "speaker_labels": bool(enable_diarization),
    }
    if language:
        payload["language_code"] = language
    else:
        payload["language_detection"] = True
    if num_speakers:
        payload["speakers_expected"] = num_speakers

    if progress_callback:
        progress_callback(20, "Starting AssemblyAI transcript...")
    started = _assemblyai_json_request("POST", f"{base_url}/transcript", api_key, payload, 60)
    transcript_id = started.get("id")
    if not transcript_id:
        raise RuntimeError(f"AssemblyAI transcript response missing id: body={started}")

    url = f"{base_url}/transcript/{transcript_id}"
    for _ in range(720):
        data = _assemblyai_json_request("GET", url, api_key, None, 60)
        status = data.get("status")
        if status == "completed":
            if progress_callback:
                progress_callback(50, "AssemblyAI transcription complete")
            return _assemblyai_result(data)
        if status == "error":
            raise RuntimeError(
                f"AssemblyAI transcript failed: transcript_id={transcript_id} error={data.get('error')}"
            )
        if status not in ("queued", "processing"):
            raise RuntimeError(
                f"AssemblyAI transcript returned unknown status: transcript_id={transcript_id} status={status} body={data}"
            )
        if progress_callback:
            progress_callback(30, f"AssemblyAI transcript {status}...")
        time.sleep(5)
    raise TimeoutError(f"AssemblyAI transcript timed out: transcript_id={transcript_id}")


def _attach_speakers_and_faces(
    file_path,
    base,
    enable_diarization,
    num_speakers,
    progress_callback,
):
    """Merge speaker diarization + face analysis into a transcribed result.
    Shared by both engines; face analysis (OpenCV) runs even when diarization
    is unavailable."""
    segments = base.get("segments") or []
    words = base.get("words") or []
    duration = base.get("duration") or (segments[-1]["end"] if segments else 0.0)

    speaker_segments = base.get("speaker_segments") or []
    speaker_summary = base.get("speakers") or {"num_speakers": 0, "speakers": {}}
    diarization_warning = None

    if enable_diarization:
        try:
            from services.speaker_detection import (
                extract_audio_wav,
                run_diarization,
                assign_speakers_to_segments,
                assign_speakers_to_words,
                create_speaker_summary,
            )

            if progress_callback:
                progress_callback(55, "Extracting audio for speaker detection...")

            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
                wav_path = tmp.name

            try:
                extract_audio_wav(file_path, wav_path)

                if progress_callback:
                    progress_callback(60, "Running speaker diarization...")

                speaker_segments = run_diarization(
                    wav_path,
                    num_speakers=num_speakers,
                    progress_callback=lambda pct, msg: (
                        progress_callback(60 + int(pct * 0.3), msg) if progress_callback else None
                    ),
                )

                if speaker_segments:
                    if progress_callback:
                        progress_callback(92, "Assigning speakers to transcript...")

                    segments = assign_speakers_to_segments(segments, speaker_segments)
                    words = assign_speakers_to_words(words, speaker_segments)
                    speaker_summary = create_speaker_summary(speaker_segments)

                    if progress_callback:
                        progress_callback(
                            95,
                            f"Found {speaker_summary['num_speakers']} speakers",
                        )

            finally:
                if os.path.exists(wav_path):
                    os.unlink(wav_path)

        except ImportError as e:
            diarization_warning = f"Speaker detection unavailable: {e}"
            if progress_callback:
                progress_callback(90, diarization_warning)
        except PermissionError as e:
            diarization_warning = str(e)
            if progress_callback:
                progress_callback(90, diarization_warning)
        except Exception as e:
            diarization_warning = f"Speaker detection failed: {e}"
            if progress_callback:
                progress_callback(90, diarization_warning)
    else:
        if not speaker_segments:
            diarization_warning = "Speaker detection disabled"

    face_map = None
    try:
        if progress_callback:
            progress_callback(95, "Analyzing face positions...")
        from services.face_analysis import analyze_faces

        face_map = analyze_faces(
            video_path=file_path,
            speaker_segments=speaker_segments,
            duration=duration,
        )
    except Exception as e:
        print(f"Warning: face analysis failed: {e}", file=sys.stderr)

    if progress_callback:
        progress_callback(100, "Complete")

    base["segments"] = segments
    base["words"] = words
    base["duration"] = round(duration, 3)
    base["speakers"] = speaker_summary
    base["speaker_segments"] = speaker_segments
    if face_map:
        base["face_map"] = face_map
    if diarization_warning:
        base["diarization_warning"] = diarization_warning
    return base


def transcribe_file(
    file_path: str,
    model_size: str = "base",
    engine: Optional[str] = None,
    language: Optional[str] = None,
    enable_diarization: bool = True,
    num_speakers: Optional[int] = None,
    progress_callback: Optional[Callable[[int, str], None]] = None,
) -> dict:
    """
    Transcribe a video/audio file with word-level timestamps and speaker detection.

    Returns:
        {
            "transcript": str,
            "segments": [{id, start, end, text, speaker}, ...],
            "words": [{word, start, end, confidence, speaker}, ...],
            "duration": float,
            "language": str,
            "speakers": {num_speakers, speakers: {SPEAKER_00: {total_time, segments, label}, ...}},
            "speaker_segments": [{speaker, start, end}, ...]
        }
    """
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"File not found: {file_path}")

    requested = (engine if engine is not None else os.environ.get("PODCLI_ENGINE", "")).strip().lower()
    engine = requested or "whisper-py"
    use_cpp = engine in ("whispercpp", "whisper-cpp", "whisper.cpp", "cpp")
    use_assemblyai = engine in ("assemblyai", "assembly-ai", "aai")

    if use_assemblyai:
        base = _transcribe_with_assemblyai(
            file_path, language, enable_diarization, num_speakers, progress_callback
        )
        return _attach_speakers_and_faces(file_path, base, False, num_speakers, progress_callback)

    # Native installs ship whisper.cpp, not openai-whisper. Fall back to it
    # automatically — whether whisper is missing OR a broken install fails to
    # load/run — unless the user explicitly asked for the whisper-py engine.
    if not use_cpp:
        if progress_callback:
            progress_callback(5, "Loading Whisper model...")
        try:
            import whisper

            model = whisper.load_model(model_size)
        except Exception as e:
            if not requested and _whispercpp_ready(model_size):
                use_cpp = True
            else:
                raise RuntimeError(
                    "The whisper-py engine needs the full source install (openai-whisper + torch). "
                    "This native install ships whisper.cpp — rerun with --engine whispercpp."
                ) from e

    if use_cpp:
        base = _transcribe_with_whispercpp(file_path, model_size, language, progress_callback)
        base["engine"] = "whispercpp"
        # whisper.cpp is the no-torch path: importing torch for diarization can
        # hard-crash native runtimes. Skip diarization, keep face analysis (OpenCV).
        return _attach_speakers_and_faces(
            file_path, base, False, num_speakers, progress_callback
        )

    # ================================================================
    # Step 1: Whisper transcription
    # ================================================================
    if progress_callback:
        progress_callback(10, f"Transcribing with Whisper ({model_size})...")

    result = model.transcribe(
        file_path,
        language=language,
        word_timestamps=True,
        verbose=False,
    )

    if progress_callback:
        progress_callback(50, "Processing timestamps...")

    segments = []
    words = []

    for seg in result.get("segments", []):
        segments.append(
            {
                "id": seg["id"],
                "start": round(seg["start"], 3),
                "end": round(seg["end"], 3),
                "text": seg["text"].strip(),
                "speaker": None,  # Will be filled by diarization
            }
        )

        seg_words = seg.get("words", [])
        if seg_words:
            for w in seg_words:
                words.append(
                    {
                        "word": w.get("word", "").strip(),
                        "start": round(w.get("start", 0), 3),
                        "end": round(w.get("end", 0), 3),
                        "confidence": round(w.get("probability", 0), 3),
                        "speaker": None,
                    }
                )
        else:
            text = seg["text"].strip()
            if not text:
                continue
            seg_words_list = text.split()
            seg_start = seg["start"]
            seg_end = seg["end"]
            seg_duration = seg_end - seg_start

            if len(seg_words_list) == 0:
                continue

            word_duration = seg_duration / len(seg_words_list)

            for i, word_text in enumerate(seg_words_list):
                w_start = seg_start + i * word_duration
                w_end = w_start + word_duration
                words.append(
                    {
                        "word": word_text,
                        "start": round(w_start, 3),
                        "end": round(w_end, 3),
                        "confidence": 0.5,
                        "speaker": None,
                    }
                )

    duration = result.get("duration", 0)
    if not duration and segments:
        duration = segments[-1]["end"]

    detected_lang = result.get("language", language or "en")

    base = {
        "transcript": result.get("text", "").strip(),
        "segments": segments,
        "words": words,
        "duration": duration,
        "language": detected_lang,
        "engine": "whisper-py",
    }
    return _attach_speakers_and_faces(
        file_path, base, enable_diarization, num_speakers, progress_callback
    )
