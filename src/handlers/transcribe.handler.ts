import { basename } from "path";
import { PythonExecutor } from "../services/python-executor.js";
import { TranscriptCache, needsDiarizationRetry } from "../services/transcript-cache.js";
import { resolveTranscribeEngine, engineCanDiarize } from "../services/engine-resolve.js";
import { webServerUrl } from "../config/server.js";
import type { TranscriptResult } from "../models/index.js";

const executor = new PythonExecutor();
const cache = new TranscriptCache();

export interface TranscribeInput {
  file_path: string;
  model_size?: "tiny" | "base" | "small" | "medium" | "large";
  engine?: "whisper-py" | "whispercpp" | "assemblyai" | "omnilingual";
  language?: string;
  enable_diarization?: boolean;
  num_speakers?: number;
  start_seconds?: number;
  duration_seconds?: number;
}

export const transcribeToolDef = {
  name: "transcribe_podcast",
  description:
    "STEP 1 — Transcribe a podcast video/audio file. This is typically the first tool you call.\n\n" +
    "What it does: Uses Whisper AI for word-level timestamps, with speaker detection (who said what) on by " +
    "default. Speaker labels are what let a clip tell a question from an answer, so leave them on for any " +
    "interview. Pass enable_diarization=false to skip them and save time on a single-speaker recording.\n" +
    "Returns: Lightweight metadata only — duration, language, word/segment counts, speaker summary, and " +
    "packed_ready flag. The actual transcript body is NOT returned here (it would be 500KB+ for a typical " +
    "episode). Read the content via get_ui_state(include_transcript: true) which returns a compact " +
    "phrase-grouped markdown view (~10x smaller than raw segments).\n" +
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
      engine: {
        type: "string",
        enum: ["whisper-py", "whispercpp", "assemblyai", "omnilingual"],
        description: "Transcription engine. Default: whisper-py.",
      },
      enable_diarization: {
        type: "boolean",
        description:
          "Speaker labels (who is speaking). On by default, and required for any clip logic that needs " +
          "to tell a question from an answer. Set false only for a single-speaker recording. " +
          "Falls back to no labels with a warning where torch/pyannote.audio is unavailable.",
        default: true,
      },
      num_speakers: {
        type: "number",
        description:
          "Exact number of speakers if known (e.g. 2 for a two-person podcast). " +
          "Leave empty to auto-detect (2-5 speakers).",
      },
      start_seconds: {
        type: "number",
        description:
          "Sample mode: only transcribe a window starting here (seconds into the source), " +
          "instead of the whole file, e.g. to test a language on 40s before committing to " +
          "a full run. Pair with duration_seconds. The result is marked complete: false and " +
          "is not written to the main transcript cache.",
      },
      duration_seconds: {
        type: "number",
        description: "Sample mode window length in seconds. Defaults start_seconds to 0 if omitted.",
      },
    },
    required: ["file_path"],
  },
};

export async function handleTranscribe(input: TranscribeInput): Promise<string> {
  const filePath = input.file_path;
  const modelSize = input.model_size ?? "base";
  const engine = input.engine;
  const language = input.language;
  const enableDiarization = input.enable_diarization !== false;
  const numSpeakers = input.num_speakers;
  const startSeconds = input.start_seconds;
  const durationSeconds = input.duration_seconds;
  // A sample is a positive window, not merely a present key. An explicit
  // null or a duration_seconds: 0 both mean "no sample", matching
  // backend/services/transcription.py and backend/main.py's handle_transcribe.
  const isSample = (durationSeconds ?? 0) > 0 || (startSeconds ?? 0) > 0;

  // Resolve before reading the cache: an unset engine is written under
  // whatever transcribe_file actually ran (e.g. "whispercpp" on a native
  // install), so reading with the raw unset request always misses.
  const resolvedEngine = await resolveTranscribeEngine(executor, engine, modelSize);
  const cacheKey = { engine: resolvedEngine, model: modelSize, language };

  // A sample is a throwaway check on a slice of the file. It must never
  // serve (or pollute) the main transcript cache, which is keyed by the
  // whole file and assumed complete.
  const cachedRaw = isSample ? null : await cache.get(filePath, cacheKey);
  const cached = needsDiarizationRetry(cachedRaw, enableDiarization, engineCanDiarize(resolvedEngine))
    ? null
    : cachedRaw;
  if (cached) {
    // Same combo cache.get(filePath, cacheKey) just matched: the packed
    // view has to be keyed identically, or a backfill here would write it
    // under a key the next read for this same request won't find.
    const packedKey = { engine: cached.engine ?? resolvedEngine, model: modelSize, language };
    // Backfill packed view if this cache predates auto-packing.
    let packed = await cache.getPackedMarkdown(filePath, packedKey);
    if (!packed) {
      try {
        const cacheHash = await cache.getFileHashForEngine(filePath, packedKey);
        await executor.execute("pack_transcript", {
          transcript: cached,
          cache_hash: cacheHash,
          source_label: basename(filePath),
          file_path: filePath,
        });
        packed = await cache.getPackedMarkdown(filePath, packedKey);
      } catch {
        // Non-fatal — caller still gets metadata
      }
    }
    return JSON.stringify({ cached: true, packed_ready: !!packed, ...formatResult(cached) });
  }

  // Execute transcription + diarization (Python side auto-writes the packed view)
  const result = await executor.execute<TranscriptResult>("transcribe", {
    file_path: filePath,
    model_size: modelSize,
    engine,
    language,
    enable_diarization: enableDiarization,
    num_speakers: numSpeakers,
    start_seconds: startSeconds,
    duration_seconds: durationSeconds,
  });

  if (!result.data) {
    throw new Error("Transcription returned no data");
  }
  const data = result.data;
  const actualEngine = data.engine ?? resolvedEngine;

  if (isSample) {
    // Not cached and not packed: it's a slice of the file, not the whole
    // transcript the cache/packed-view keys assume.
    return JSON.stringify({ cached: false, packed_ready: false, ...formatResult(data) });
  }

  // Cache the raw result under what it actually ran with, not the prediction
  // above: resolveTranscribeEngine can't see a model-load failure that only
  // shows up once transcribe_file tries it for real.
  await cache.set(filePath, data, { engine: actualEngine, model: modelSize, language });
  const packed = await cache.getPackedMarkdown(filePath, { engine: actualEngine, model: modelSize, language });

  return JSON.stringify({ cached: false, packed_ready: !!packed, ...formatResult(data) });
}

/**
 * Returns lightweight metadata — NOT the transcript body. The full transcript
 * is available via get_ui_state(include_transcript: true), which serves the
 * packed markdown view (~10x smaller than raw segments/words). Raw segments
 * and words arrays stay in the on-disk cache for internal Python consumers.
 */
function formatResult(data: TranscriptResult) {
  return {
    duration: data.duration,
    language: data.language,
    // Exposed so callers that need to re-read the cache (e.g. the UI state
    // push in server.ts) key it the same way this handler just wrote it.
    engine: data.engine,
    word_count: (data.words ?? []).length,
    segment_count: (data.segments ?? []).length,
    speakers: data.speakers ?? { num_speakers: 0, speakers: {} },
    ...(data.complete === false
      ? { complete: false, sample_offset_seconds: data.sample_offset_seconds ?? 0 }
      : {}),
    next_step: "Read the transcript via get_ui_state(include_transcript: true), then suggest_clips.",
  };
}

// =============================================
// Async transcription via Web UI job queue
// =============================================

export const transcribeStartToolDef = {
  name: "transcribe_start",
  description:
    "Start transcription as a background job and return a job_id immediately. " +
    "Use this instead of transcribe_podcast for long files so you can narrate progress " +
    "to the user while it runs (a 60-min episode takes 15–25 min).\n\n" +
    "Flow: call transcribe_start(file_path) → emit status text to user → " +
    "call job_status(job_id, wait_seconds: 30) in a loop until done → " +
    "then read the packed transcript via get_ui_state(include_transcript: true).\n\n" +
    "Requires the Web UI to be running (npm run ui). Returns { job_id, cached, status, estimate_minutes }.",
  inputSchema: {
    type: "object" as const,
    properties: {
      file_path: { type: "string", description: "Absolute path to the podcast file" },
      model_size: {
        type: "string",
        enum: ["tiny", "base", "small", "medium", "large"],
        default: "base",
      },
      language: { type: "string" },
      engine: {
        type: "string",
        enum: ["whisper-py", "whispercpp", "assemblyai", "omnilingual"],
      },
      enable_diarization: { type: "boolean", default: true },
      num_speakers: { type: "number" },
      start_seconds: {
        type: "number",
        description:
          "Sample mode: only transcribe a window starting here (seconds into the source). " +
          "Pair with duration_seconds. Not written to the main transcript cache.",
      },
      duration_seconds: {
        type: "number",
        description: "Sample mode window length in seconds. Defaults start_seconds to 0 if omitted.",
      },
    },
    required: ["file_path"],
  },
};

export async function handleTranscribeStart(input: TranscribeInput): Promise<string> {
  try {
    const res = await fetch(`${webServerUrl}/api/transcribe`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        file_path: input.file_path,
        model_size: input.model_size ?? "base",
        engine: input.engine,
        language: input.language,
        enable_diarization: input.enable_diarization !== false,
        num_speakers: input.num_speakers,
        start_seconds: input.start_seconds,
        duration_seconds: input.duration_seconds,
      }),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}: ${await res.text()}`);
    const data = (await res.json()) as { job_id: string; status: string; cached?: boolean };

    // Rough estimate — Whisper base + diarization is roughly 1/3× audio duration on CPU.
    // We don't know duration here; ballpark from file size.
    let estimate = "10–30 min depending on episode length";
    try {
      const { statSync } = await import("fs");
      const mb = statSync(input.file_path).size / (1024 * 1024);
      const mins = Math.max(2, Math.round(mb / 50)); // very rough: ~50MB/min of processing
      estimate = data.cached ? "instant (cached)" : `~${mins}–${mins * 2} min`;
    } catch {
      // ignore
    }

    return JSON.stringify({
      job_id: data.job_id,
      cached: !!data.cached,
      status: data.status,
      estimate,
      next_step: data.cached
        ? "Cached — skip polling, read via get_ui_state(include_transcript: true)."
        : `Poll job_status("${data.job_id}", wait_seconds: 30) in a loop, emitting one terse progress line to the user between polls.`,
    });
  } catch (err: unknown) {
    const msg = err instanceof Error ? err.message : String(err);
    if (msg.includes("ECONNREFUSED") || msg.includes("fetch failed")) {
      return JSON.stringify({
        error: "Web UI not running. Start it with `npm run ui`, or fall back to transcribe_podcast for synchronous transcription.",
      });
    }
    throw err;
  }
}

export const jobStatusToolDef = {
  name: "job_status",
  description:
    "Poll the status of any background job (transcription, clip render, batch export). " +
    "Supports long-polling: pass wait_seconds (1–60) to block until the job changes state " +
    "or the timeout elapses, whichever comes first. Paces Claude's polling naturally so " +
    "the spinner doesn't spam and the user sees steady progress text.\n\n" +
    "Returns { status: 'running'|'done'|'error', progress, message, done, result? }.\n" +
    "Use after transcribe_start or batch_create_clips(async_mode: true).",
  inputSchema: {
    type: "object" as const,
    properties: {
      job_id: { type: "string" },
      wait_seconds: {
        type: "number",
        default: 30,
        minimum: 0,
        maximum: 60,
        description: "How long to wait for a status change before returning. Default 30s.",
      },
    },
    required: ["job_id"],
  },
};

export async function handleJobStatus(input: {
  job_id: string;
  wait_seconds?: number;
  job_kind?: string;
}): Promise<string> {
  const wait = Math.max(0, Math.min(60, input.wait_seconds ?? 30));
  const deadline = Date.now() + wait * 1000;
  let lastProgress = -1;

  try {
    while (true) {
      const res = await fetch(`${webServerUrl}/api/job/${input.job_id}`);
      if (res.status === 404) {
        return JSON.stringify({ error: `Job ${input.job_id} not found` });
      }
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const job = (await res.json()) as {
        status: "running" | "done" | "error";
        progress: number;
        message: string;
        result?: unknown;
        error?: string;
      };

      const progressChanged = job.progress !== lastProgress;
      const terminal = job.status === "done" || job.status === "error";

      if (terminal || progressChanged || Date.now() >= deadline) {
        return JSON.stringify({
          status: job.status,
          progress: job.progress,
          message: job.message,
          done: terminal,
          error: job.error,
          next_step:
            job.status === "done"
              ? "Job complete. For transcription: get_ui_state(include_transcript: true). For renders: clips are on disk — the result field lists paths."
              : job.status === "error"
              ? "Job failed — check the error. Retry the original start call if appropriate."
              : `Still running — call job_status("${input.job_id}", wait_seconds: 30) again and emit one terse progress line to the user between polls.`,
        });
      }

      lastProgress = job.progress;
      await new Promise((r) => setTimeout(r, 2000));
    }
  } catch (err: unknown) {
    const msg = err instanceof Error ? err.message : String(err);
    if (msg.includes("ECONNREFUSED") || msg.includes("fetch failed")) {
      return JSON.stringify({ error: "Web UI not running." });
    }
    throw err;
  }
}
