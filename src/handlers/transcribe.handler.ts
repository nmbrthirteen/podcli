import { PythonExecutor } from "../services/python-executor.js";
import { TranscriptCache } from "../services/transcript-cache.js";
import type { TranscriptResult } from "../models/index.js";

const executor = new PythonExecutor();
const cache = new TranscriptCache();

export const transcribeToolDef = {
  name: "transcribe_podcast",
  description:
    "Transcribe a podcast video/audio file with word-level timestamps and automatic " +
    "speaker detection. Identifies who is speaking when (supports 2-5 speakers). " +
    "Returns structured transcript with word timing and speaker labels. " +
    "Supports MP4, MOV, WebM, MKV, MP3, WAV.",
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

export async function handleTranscribe(
  input: Record<string, unknown>
): Promise<string> {
  const filePath = input.file_path as string;
  const modelSize = (input.model_size as string) || "base";
  const language = input.language as string | undefined;
  const enableDiarization = input.enable_diarization !== false; // default true
  const numSpeakers = input.num_speakers as number | undefined;

  // Check cache first
  const cached = await cache.get(filePath);
  if (cached) {
    return JSON.stringify({
      cached: true,
      ...formatResult(cached as unknown as Record<string, unknown>),
    });
  }

  // Execute transcription + diarization
  const result = await executor.execute("transcribe", {
    file_path: filePath,
    model_size: modelSize,
    language,
    enable_diarization: enableDiarization,
    num_speakers: numSpeakers,
  });

  const data = result.data as Record<string, unknown>;

  // Cache the result
  await cache.set(filePath, data as unknown as TranscriptResult);

  return JSON.stringify({
    cached: false,
    ...formatResult(data),
  });
}

function formatResult(data: Record<string, unknown>) {
  const words = (data.words as Array<Record<string, unknown>>) || [];
  const segments = (data.segments as Array<Record<string, unknown>>) || [];
  const speakers = data.speakers as Record<string, unknown> | undefined;

  return {
    transcript: data.transcript,
    segments,
    words,
    duration: data.duration,
    language: data.language,
    word_count: words.length,
    segment_count: segments.length,
    speakers: speakers || { num_speakers: 0, speakers: {} },
    speaker_segments: data.speaker_segments || [],
  };
}
