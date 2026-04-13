import { PythonExecutor } from "../services/python-executor.js";
import { TranscriptCache } from "../services/transcript-cache.js";
import type { TranscriptResult } from "../models/index.js";

const executor = new PythonExecutor();
const cache = new TranscriptCache();

export interface TranscribeInput {
  file_path: string;
  model_size?: "tiny" | "base" | "small" | "medium" | "large";
  language?: string;
  enable_diarization?: boolean;
  num_speakers?: number;
}

export const transcribeToolDef = {
  name: "transcribe_podcast",
  description:
    "STEP 1 — Transcribe a podcast video/audio file. This is typically the first tool you call.\n\n" +
    "What it does: Uses Whisper AI for word-level timestamps + pyannote for speaker detection (who said what).\n" +
    "Returns: Full transcript with word timing, speaker labels, and segments.\n" +
    "Caching: Results are cached by file hash — same file won't be re-transcribed.\n" +
    "Supported formats: MP4, MOV, WebM, MKV, MP3, WAV.\n\n" +
    "After transcription: call get_ui_state(include_transcript: true) to read the transcript, " +
    "then analyze it for viral moments and call suggest_clips.",
  inputSchema: {
    type: "object" as const,
    properties: {
      file_path: {
        type: "string",
        description: "Absolute path to the podcast file",
      },
      model_size: {
        type: "string",
        enum: ["tiny", "base", "small", "medium", "large"],
        description:
          "Whisper model size. tiny=fastest, large=most accurate. Default: base",
        default: "base",
      },
      language: {
        type: "string",
        description: "ISO language code (e.g. 'en'). Leave empty for auto-detect.",
      },
      enable_diarization: {
        type: "boolean",
        description:
          "Enable speaker detection (who is speaking). Requires pyannote.audio. Default: true",
        default: true,
      },
      num_speakers: {
        type: "number",
        description:
          "Exact number of speakers if known (e.g. 2 for a two-person podcast). " +
          "Leave empty to auto-detect (2-5 speakers).",
      },
    },
    required: ["file_path"],
  },
};

export async function handleTranscribe(input: TranscribeInput): Promise<string> {
  const filePath = input.file_path;
  const modelSize = input.model_size ?? "base";
  const language = input.language;
  const enableDiarization = input.enable_diarization !== false; // default true
  const numSpeakers = input.num_speakers;

  // Check cache first
  const cached = await cache.get(filePath);
  if (cached) {
    return JSON.stringify({ cached: true, ...formatResult(cached) });
  }

  // Execute transcription + diarization
  const result = await executor.execute<TranscriptResult>("transcribe", {
    file_path: filePath,
    model_size: modelSize,
    language,
    enable_diarization: enableDiarization,
    num_speakers: numSpeakers,
  });

  if (!result.data) {
    throw new Error("Transcription returned no data");
  }
  const data = result.data;

  // Cache the result
  await cache.set(filePath, data);

  return JSON.stringify({ cached: false, ...formatResult(data) });
}

function formatResult(data: TranscriptResult) {
  const words = data.words ?? [];
  const segments = data.segments ?? [];

  return {
    transcript: data.transcript,
    segments,
    words,
    duration: data.duration,
    language: data.language,
    word_count: words.length,
    segment_count: segments.length,
    speakers: data.speakers ?? { num_speakers: 0, speakers: {} },
    speaker_segments: data.speaker_segments ?? [],
  };
}
