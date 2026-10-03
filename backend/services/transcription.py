"""
Transcription service using OpenAI Whisper + speaker diarization.

Produces word-level timestamps with speaker labels by:
1. Running Whisper for speech-to-text with word timing
2. Running pyannote speaker diarization (if available)
3. Merging speaker labels onto each word and segment
"""

import json
import functools
import hashlib
import http.client
import importlib.util
import os
import shutil
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Optional, Callable

from services.engines import is_assemblyai_engine, is_omnilingual_engine, normalize_engine


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



# "large" alone doesn't name a real ggml file (upstream ships v1/v2/v3/v3-turbo
# builds); provisioning always fetches large-v3, so resolve the same way here.
_WHISPERCPP_MODEL_ALIASES = {"large": "large-v3"}


def _whispercpp_model(model_size: str) -> str:
    resolved = _WHISPERCPP_MODEL_ALIASES.get(model_size, model_size)
    return os.environ.get("PODCLI_WHISPERCPP_MODEL") or os.path.join(
        _managed_home(), "models", f"ggml-{resolved}.bin"
    )


def _whispercpp_ready(model_size: str) -> bool:
    return _whispercpp_cli() is not None and os.path.exists(_whispercpp_model(model_size))


def _omnilingual_model() -> str:
    return os.environ.get("PODCLI_OMNILINGUAL_MODEL") or os.path.join(
        _managed_home(), "models", "omnilingual", "model.int8.onnx"
    )


def _omnilingual_tokens() -> str:
    return os.environ.get("PODCLI_OMNILINGUAL_TOKENS") or os.path.join(
        _managed_home(), "models", "omnilingual", "tokens.txt"
    )


def _omnilingual_ready() -> bool:
    return os.path.exists(_omnilingual_model()) and os.path.exists(_omnilingual_tokens())


def _omnilingual_model_fingerprint(model_path: str) -> str:
    """Cheap stand-in for hashing the whole model file: size+mtime changes
    whenever the file is replaced (re-provisioned, a different quantization
    swapped in), without reading potentially hundreds of MB on every run."""
    try:
        st = os.stat(model_path)
        return f"{st.st_size}-{int(st.st_mtime)}"
    except OSError:
        return "unknown"


def _whisper_threads() -> int:
    raw = os.environ.get("PODCLI_WHISPER_THREADS", "").strip()
    try:
        n = int(raw)
    except ValueError:
        n = 0
    return n if n > 0 else 4


def _transcribe_with_whispercpp(file_path, model_size, language, progress_callback, wav_path=None):
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
        wav_path=wav_path,
        threads=_whisper_threads(),
    )
    if progress_callback:
        progress_callback(50, "Transcription complete")
    return result


def _transcribe_with_omnilingual(file_path, progress_callback, wav_path=None):
    from config.paths import paths
    from services import transcribe_runs
    from services import transcription_omnilingual as omni

    if progress_callback:
        progress_callback(10, "Transcribing with omnilingual...")

    model = _omnilingual_model()
    tokens = _omnilingual_tokens()
    # Fetch only into podcli's own model folder; a custom model path is the
    # caller's to provide, and must never trigger a 1 GB download beside it.
    managed_dir = os.path.join(_managed_home(), "models", "omnilingual")
    if os.path.dirname(model) == managed_dir and os.path.dirname(tokens) == managed_dir:
        omni.ensure_model(managed_dir, progress_callback)
    if not os.path.exists(model) or not os.path.exists(tokens):
        raise FileNotFoundError(
            f"omnilingual model not found: {model}. "
            "Set PODCLI_OMNILINGUAL_MODEL/PODCLI_OMNILINGUAL_TOKENS or run provisioning."
        )

    # Resumable: a crash partway through a long file loses at most the
    # window that was decoding, not the whole run. A rerun with the same
    # file/model/language skips every window that already has a receipt.
    #
    # The key has to change whenever a resumed window's receipt would no
    # longer match what a fresh decode produces: a different model file (so
    # fold in its size+mtime, cheap to stat vs. hashing the whole model), or
    # a change to the window/context constants a receipt's timestamps are
    # only valid under (resuming under new constants would silently splice
    # old-geometry windows into a new-geometry transcript).
    model_fp = _omnilingual_model_fingerprint(model)
    run_dir = transcribe_runs.run_dir(
        paths["cache"], file_path, "omnilingual",
        model_size=f"int8-{model_fp}-w{omni.WINDOW_SECONDS}-c{omni.CONTEXT_SECONDS}",
        language="und",
    )

    def window_progress(pct, msg):
        if progress_callback:
            progress_callback(10 + int(pct * 0.4), msg)

    result = omni.transcribe_file(
        file_path,
        model_path=model,
        tokens_path=tokens,
        wav_path=wav_path,
        threads=_whisper_threads(),
        run_dir=run_dir,
        progress_callback=window_progress,
    )
    if progress_callback:
        progress_callback(50, "Transcription complete")
    return result


def _assemblyai_base_url() -> str:
    region = os.environ.get("ASSEMBLYAI_REGION", "").strip().lower()
    if region == "eu":
        return "https://api.eu.assemblyai.com/v2"
    return "https://api.assemblyai.com/v2"


class AssemblyAIHTTPError(RuntimeError):
    """Carries the HTTP status so callers can tell a bad/expired resource
    (4xx, e.g. the transcript a resumed receipt points at no longer exists)
    from a transient server error, without re-parsing the message string."""

    def __init__(self, message: str, status: int):
        super().__init__(message)
        self.status = status


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
            last_error = AssemblyAIHTTPError(
                f"AssemblyAI request failed: method={method} url={url} status={e.code} body={detail}",
                status=e.code,
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
    from config.paths import paths
    from services import transcribe_runs

    api_key = os.environ.get("ASSEMBLYAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("ASSEMBLYAI_API_KEY is required when PODCLI_ENGINE=assemblyai")

    base_url = _assemblyai_base_url()
    region = os.environ.get("ASSEMBLYAI_REGION", "").strip().lower() or "us"
    key_hash = hashlib.sha256(api_key.encode("utf-8")).hexdigest()[:8]
    # A restarted job (crash, kill, machine reboot) must resume by polling
    # the transcript it already started, not re-upload the file and pay for
    # a second AssemblyAI job. The receipt is keyed by exactly what affects
    # the AssemblyAI request (language, diarization, speaker count, region,
    # which account) so a different request for the same file never adopts
    # someone else's job: a region or key mismatch would otherwise poll a
    # transcript ID that belongs to a different account/API surface.
    directory = transcribe_runs.run_dir(
        paths["cache"], file_path, "assemblyai",
        model_size=f"diar{int(bool(enable_diarization))}-spk{num_speakers or 0}-{region}-{key_hash}",
        language=language,
    )

    # A resumed transcript_id that 4xxs on the resume GET (e.g. AssemblyAI
    # deleted it after its retention window, or it belonged to a key that's
    # since been rotated) is permanently bad: retrying it forever would
    # never succeed. Clear the receipt and fall through to a fresh upload,
    # once.
    for resume_attempt in (True, False):
        receipt = transcribe_runs.read_receipt(directory, "assemblyai.json") if resume_attempt else None
        transcript_id = receipt.get("transcript_id") if receipt else None
        resumed = transcript_id is not None

        if not transcript_id:
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
            # Written before the poll loop starts: if the process dies mid-poll,
            # the next run finds this and resumes instead of re-uploading.
            transcribe_runs.write_receipt(directory, "assemblyai.json", {"transcript_id": transcript_id})
        elif progress_callback:
            progress_callback(20, f"Resuming AssemblyAI transcript {transcript_id}...")

        url = f"{base_url}/transcript/{transcript_id}"
        try:
            for _ in range(720):
                data = _assemblyai_json_request("GET", url, api_key, None, 60)
                status = data.get("status")
                if status == "completed":
                    if progress_callback:
                        progress_callback(50, "AssemblyAI transcription complete")
                    transcribe_runs.clear_run(directory)
                    return _assemblyai_result(data)
                if status == "error":
                    transcribe_runs.clear_run(directory)
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
        except AssemblyAIHTTPError as e:
            if resumed and 400 <= e.status < 500:
                transcribe_runs.clear_run(directory)
                if progress_callback:
                    progress_callback(
                        10, f"Saved AssemblyAI transcript {transcript_id} is gone (status {e.status}); re-uploading..."
                    )
                continue
            raise
    raise RuntimeError("AssemblyAI resume retry exhausted")  # unreachable: loop always returns or raises


def _attach_speakers_and_faces(
    file_path,
    base,
    enable_diarization,
    num_speakers,
    progress_callback,
    wav_path=None,
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

            shared_wav = wav_path if wav_path and os.path.exists(wav_path) else None
            if shared_wav:
                diar_wav = shared_wav
            else:
                with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
                    diar_wav = tmp.name

            try:
                if not shared_wav:
                    extract_audio_wav(file_path, diar_wav)

                if progress_callback:
                    progress_callback(60, "Running speaker diarization...")

                speaker_segments = run_diarization(
                    diar_wav,
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
                if not shared_wav and os.path.exists(diar_wav):
                    os.unlink(diar_wav)

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


@functools.lru_cache(maxsize=1)
def _whisper_py_available() -> bool:
    """Cheap availability check for the openai-whisper package.

    `import whisper` doesn't just find the module, it executes it, which
    transitively imports torch and costs hundreds of ms to seconds.
    resolve_engine_info runs this on every single transcribe request (it's
    a cache-key prediction, not an actual transcription), so it uses
    find_spec, which only locates the module and never runs its code, and
    memoizes the result, since whether the package is installed can't
    change within a process's lifetime.
    """
    try:
        return importlib.util.find_spec("whisper") is not None
    except (ImportError, ValueError):
        # find_spec itself can raise if "whisper" is already in sys.modules
        # under a module object missing __spec__ (a malformed stand-in, not
        # a real install state). Fall back to treating that as available
        # rather than crashing a cache-key prediction over it.
        return True


def _resolve_whisper_py_fallback(requested: Optional[str], model_size: str, probe_model: bool):
    """Single source of truth for "should an unset whisper-py request fall
    back to whispercpp instead": the one decision resolve_engine_info and
    transcribe_file both have to make the same way, or a cache key built
    from one's answer misses the transcript the other actually wrote.

    probe_model=False (resolve_engine_info, a cache-key prediction made on
    every request, so it can't afford to load model weights, or even
    import whisper for real, see _whisper_py_available) only checks
    whether the package is installed. probe_model=True (transcribe_file,
    about to actually transcribe) does the real import and loads the
    model, catching the one case the cheap check can't: the package is
    present but load_model fails. That gap means a cache lookup built from
    resolve_engine_info's prediction can still miss once in that rarer
    case, before the result is written under the engine transcribe_file
    actually ran with.

    Returns (fall_back_to_whispercpp, loaded_model_or_None, error_or_None).
    """
    if not probe_model:
        if _whisper_py_available():
            return False, None, None
        if not requested and _whispercpp_ready(model_size):
            return True, None, None
        return False, None, None

    try:
        import whisper

        model = whisper.load_model(model_size)
        return False, model, None
    except Exception as e:
        if not requested and _whispercpp_ready(model_size):
            return True, None, None
        return False, None, e


def resolve_engine_info(requested: Optional[str], model_size: str = "base") -> dict:
    """Predict which engine transcribe_file will actually use, without doing
    any transcription work. Callers cache transcripts by engine; the cache
    key has to match what transcribe_file resolves to, not the raw request,
    or an unset engine writes under one key and reads under another.
    """
    requested = requested if requested is not None else os.environ.get("PODCLI_ENGINE", "")
    engine = normalize_engine(requested)
    if engine != "whisper-py":
        return {"engine": engine, "model_size": model_size}
    fall_back, _model, _error = _resolve_whisper_py_fallback(requested, model_size, probe_model=False)
    return {"engine": "whispercpp" if fall_back else "whisper-py", "model_size": model_size}


def transcribe_file(
    file_path: str,
    model_size: str = "base",
    engine: Optional[str] = None,
    language: Optional[str] = None,
    enable_diarization: bool = True,
    num_speakers: Optional[int] = None,
    progress_callback: Optional[Callable[[int, str], None]] = None,
    wav_path: Optional[str] = None,
    start_seconds: Optional[float] = None,
    duration_seconds: Optional[float] = None,
) -> dict:
    """
    Transcribe a video/audio file with word-level timestamps and speaker detection.

    wav_path: optional pre-extracted 16 kHz mono WAV shared across analysis
    stages — used by whisper.cpp and diarization instead of re-decoding.

    start_seconds/duration_seconds: sample mode, transcribe only a window of
    the source (e.g. to test a language on 40s before committing to a full
    run) instead of the whole file. The result is marked complete: False and
    its timestamps are relative to the sample window, not the source;
    sample_offset_seconds carries where in the source the window started.
    Diarization and face analysis are skipped: a throwaway sample isn't
    worth the extra passes, and both would need frame/audio access to the
    original file that the trimmed clip doesn't carry.

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

    # Matches backend/main.py's handle_transcribe and both TS transcribe
    # entry points exactly: a sample is a positive window, not merely a
    # present key, so start_seconds=0/duration_seconds=0 or an explicit
    # null both mean "no sample" rather than "sample from t=0".
    is_sample = (duration_seconds or 0) > 0 or (start_seconds or 0) > 0
    if is_sample:
        from services.audio_extract import extract_wav_16k_mono

        sample_wav = extract_wav_16k_mono(
            file_path,
            start_seconds=start_seconds or 0.0,
            duration_seconds=duration_seconds,
        )
        try:
            result = _transcribe_file_inner(
                sample_wav,
                model_size=model_size,
                engine=engine,
                language=language,
                enable_diarization=False,
                num_speakers=num_speakers,
                progress_callback=progress_callback,
                wav_path=sample_wav,
            )
        finally:
            try:
                os.unlink(sample_wav)
            except OSError:
                pass
        result["complete"] = False
        result["sample_offset_seconds"] = start_seconds or 0.0
        return result

    return _transcribe_file_inner(
        file_path,
        model_size=model_size,
        engine=engine,
        language=language,
        enable_diarization=enable_diarization,
        num_speakers=num_speakers,
        progress_callback=progress_callback,
        wav_path=wav_path,
    )


def _transcribe_file_inner(
    file_path: str,
    model_size: str = "base",
    engine: Optional[str] = None,
    language: Optional[str] = None,
    enable_diarization: bool = True,
    num_speakers: Optional[int] = None,
    progress_callback: Optional[Callable[[int, str], None]] = None,
    wav_path: Optional[str] = None,
) -> dict:

    requested = engine if engine is not None else os.environ.get("PODCLI_ENGINE", "")
    engine = normalize_engine(requested)
    use_cpp = engine == "whispercpp"
    use_assemblyai = is_assemblyai_engine(engine)
    use_omnilingual = is_omnilingual_engine(engine)

    if use_assemblyai:
        base = _transcribe_with_assemblyai(
            file_path, language, enable_diarization, num_speakers, progress_callback
        )
        # AssemblyAI does its own diarization (speaker_labels in the request),
        # not the pyannote path _attach_speakers_and_faces runs below. Record
        # against the flag that actually drove that request, not the False
        # passed to attach (which only controls the pyannote attempt here).
        base["diarization_attempted"] = bool(enable_diarization)
        return _attach_speakers_and_faces(file_path, base, False, num_speakers, progress_callback)

    if use_omnilingual:
        base = _transcribe_with_omnilingual(file_path, progress_callback, wav_path=wav_path)
        base["engine"] = "omnilingual"
        # Same no-torch constraint as whisper.cpp: skip diarization, keep face analysis.
        # Diarization is never possible on this engine, so it's never
        # "attempted" regardless of what the caller asked for: a cache
        # entry from this engine must never look like a retriable miss.
        base["diarization_attempted"] = False
        return _attach_speakers_and_faces(
            file_path, base, False, num_speakers, progress_callback, wav_path=wav_path
        )

    # Native installs ship whisper.cpp, not openai-whisper. Fall back to it
    # automatically — whether whisper is missing OR a broken install fails to
    # load/run — unless the user explicitly asked for the whisper-py engine.
    if not use_cpp:
        if progress_callback:
            progress_callback(5, "Loading Whisper model...")
        fall_back, model, error = _resolve_whisper_py_fallback(requested, model_size, probe_model=True)
        if fall_back:
            use_cpp = True
        elif error is not None:
            raise RuntimeError(
                "The whisper-py engine needs the full source install (openai-whisper + torch). "
                "This native install ships whisper.cpp. Rerun with --engine whispercpp."
            ) from error

    if use_cpp:
        base = _transcribe_with_whispercpp(
            file_path, model_size, language, progress_callback, wav_path=wav_path
        )
        base["engine"] = "whispercpp"
        # whisper.cpp is the no-torch path: importing torch for diarization can
        # hard-crash native runtimes. Skip diarization, keep face analysis (OpenCV).
        # Never possible on this engine, see the omnilingual branch above.
        base["diarization_attempted"] = False
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
        "diarization_attempted": bool(enable_diarization),
    }
    return _attach_speakers_and_faces(
        file_path, base, enable_diarization, num_speakers, progress_callback,
        wav_path=wav_path,
    )
