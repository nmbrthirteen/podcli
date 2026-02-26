"""
Transcription service using OpenAI Whisper + speaker diarization.

Produces word-level timestamps with speaker labels by:
1. Running Whisper for speech-to-text with word timing
2. Running pyannote speaker diarization (if available)
3. Merging speaker labels onto each word and segment
"""

import os
import subprocess
import tempfile
from typing import Optional, Callable


def transcribe_file(
    file_path: str,
    model_size: str = "base",
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

    # ================================================================
    # Step 1: Whisper transcription
    # ================================================================
    if progress_callback:
        progress_callback(5, "Loading Whisper model...")

    import whisper

    model = whisper.load_model(model_size)

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

    # ================================================================
    # Step 2: Speaker diarization (if enabled)
    # ================================================================
    speaker_segments = []
    speaker_summary = {"num_speakers": 0, "speakers": {}}
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

            # Extract audio as WAV for pyannote
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

                    # Merge speaker labels into segments and words
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
        diarization_warning = "Speaker detection disabled"

    if progress_callback:
        progress_callback(100, "Transcription complete")

    result_data = {
        "transcript": result.get("text", "").strip(),
        "segments": segments,
        "words": words,
        "duration": round(duration, 3),
        "language": detected_lang,
        "speakers": speaker_summary,
        "speaker_segments": speaker_segments,
    }

    if diarization_warning:
        result_data["diarization_warning"] = diarization_warning

    return result_data
