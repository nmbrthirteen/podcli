import { readFileSync } from "fs";
import { PythonExecutor } from "../services/python-executor.js";
import { FileManager } from "../services/file-manager.js";
import { paths } from "../config/paths.js";
import { childLogger } from "../utils/logger.js";
import type {
  BatchClipsInput,
  BatchClipSpec,
  BatchClipsResult,
  SuggestedClip,
  UIState,
} from "../models/index.js";

const log = childLogger("batch-clips");
const executor = new PythonExecutor();
const fileManager = new FileManager();

/** Load UI state from disk. Returns null if unavailable. */
function loadState(): UIState | null {
  try {
    return JSON.parse(readFileSync(paths.uiState, "utf-8")) as UIState;
  } catch (err) {
    log.debug("UI state unavailable", { err: err instanceof Error ? err.message : err });
    return null;
  }
}

export const batchClipsToolDef = {
  name: "batch_create_clips",
  description:
    "STEP 3 — Export multiple clips at once as finished vertical shorts.\n\n" +
    "EASIEST: pass export_selected=true to export all selected clips in one go.\n" +
    "Alternative: pass clip_numbers=[1, 3, 5] for specific ones.\n" +
    "Everything (video, timestamps, settings) auto-loads from session state.\n\n" +
    "Each clip gets: 9:16 vertical crop, burned-in captions, normalized audio, H.264 MP4.",
  inputSchema: {
    type: "object" as const,
    properties: {
      export_selected: {
        type: "boolean",
        description:
          "Export all selected (non-deselected) clips from session state. Simplest option.",
      },
      clip_numbers: {
        type: "array",
        items: { type: "number" },
        description:
          "Export specific clips by number (e.g. [1, 3, 5]). " +
          "Auto-fills everything from session state.",
      },
      video_path: {
        type: "string",
        description:
          "Path to the podcast video. Auto-loaded from session state if omitted.",
      },
      clips: {
        type: "array",
        description: "Array of clips to create (not needed if using clip_numbers)",
        items: {
          type: "object",
          properties: {
            start_second: { type: "number" },
            end_second: { type: "number" },
            title: { type: "string" },
            caption_style: {
              type: "string",
              enum: ["hormozi", "karaoke", "subtle", "branded"],
            },
            crop_strategy: {
              type: "string",
              enum: ["center", "face", "speaker"],
            },
            allow_ass_fallback: {
              type: "boolean",
            },
          },
          required: ["start_second", "end_second"],
        },
      },
      clean_fillers: {
        type: "boolean",
        description:
          "Remove filler words (um, uh, hmm) from captions and compress long silences. Default: true",
        default: true,
      },
      allow_ass_fallback: {
        type: "boolean",
        description:
          "Allow fallback to legacy ASS captions if Remotion caption rendering fails. Default: false.",
        default: false,
      },
      transcript_words: {
        type: "array",
        description:
          "Word-level timestamps. Auto-loaded from session state if omitted.",
        items: {
          type: "object",
          properties: {
            word: { type: "string" },
            start: { type: "number" },
            end: { type: "number" },
            confidence: { type: "number" },
          },
        },
      },
      async_mode: {
        type: "boolean",
        description:
          "Return a job_id immediately and render in the background. Use for multi-clip " +
          "batches so Claude can poll job_status and emit live progress to the user. " +
          "Requires the Web UI to be running (npm run ui). Default: false (sync).",
        default: false,
      },
    },
    required: [],
  },
};

export async function handleBatchClips(input: BatchClipsInput): Promise<string> {
  await fileManager.ensureDirectories();

  const state = loadState();
  const settings = state?.settings ?? {};
  const suggestions: SuggestedClip[] = state?.suggestions ?? [];
  const transcript = state?.transcript ?? null;

  // Auto-resolve video path
  const videoPath = input.video_path || state?.videoPath || "";
  if (!videoPath) {
    return JSON.stringify({ error: "video_path is required (no video in session state)" });
  }

  // Auto-resolve transcript words
  const transcriptWords = input.transcript_words ?? transcript?.words ?? [];

  // Build clips array: from export_selected, clip_numbers, or explicit clips
  let clips: BatchClipSpec[];
  const deselected = state?.deselectedIndices ?? [];

  const buildClipFromSuggestion = (s: SuggestedClip, num: number): BatchClipSpec => ({
    start_second: s.start_second,
    end_second: s.end_second,
    title: s.title || `clip_${num}`,
    caption_style: s.suggested_caption_style || settings.captionStyle || "hormozi",
    crop_strategy: settings.cropStrategy || "speaker",
    allow_ass_fallback: input.allow_ass_fallback === true,
    logo_path: settings.logoPath || null,
    // Preserve multi-cut segments from suggestion
    ...(s.segments && s.segments.length > 0 && { keep_segments: s.segments }),
  });

  if (input.export_selected) {
    // Export all selected (non-deselected) clips
    clips = [];
    for (let i = 0; i < suggestions.length; i++) {
      if (!deselected.includes(i)) {
        clips.push(buildClipFromSuggestion(suggestions[i], i + 1));
      }
    }
    if (clips.length === 0) {
      return JSON.stringify({
        error: `No selected clips to export. ${suggestions.length} total, all deselected.`,
      });
    }
  } else if (input.clip_numbers) {
    const errors: string[] = [];
    clips = [];

    for (const num of input.clip_numbers) {
      const idx = num - 1;
      if (idx < 0 || idx >= suggestions.length) {
        errors.push(`Clip #${num} not found`);
        continue;
      }
      clips.push(buildClipFromSuggestion(suggestions[idx], num));
    }

    if (clips.length === 0) {
      return JSON.stringify({
        error: `No valid clips found. ${errors.join(", ")}. Available: 1-${suggestions.length}`,
      });
    }
  } else if (input.clips) {
    clips = input.clips;
  } else {
    return JSON.stringify({
      error: "Use export_selected=true, clip_numbers=[1, 3], or pass clips array",
    });
  }

  // Async path — route through Web UI so caller can poll job_status for
  // live progress during a multi-minute render. Falls back to sync if the
  // UI isn't running.
  if (input.async_mode) {
    try {
      const res = await fetch("http://localhost:3847/api/batch-clips", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          video_path: videoPath,
          clips,
          transcript_words: transcriptWords,
          clean_fillers: input.clean_fillers !== false,
          logo_path: settings.logoPath || null,
          outro_path: settings.outroPath || null,
        }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}: ${await res.text()}`);
      const data = (await res.json()) as { job_id: string; status: string };
      return JSON.stringify({
        job_id: data.job_id,
        status: data.status,
        clip_count: clips.length,
        next_step: `Poll job_status("${data.job_id}", wait_seconds: 30) in a loop, emitting one terse progress line to the user between polls (e.g. "Rendering 3/7 — clip #3"). Stop when done=true.`,
      });
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err);
      if (msg.includes("ECONNREFUSED") || msg.includes("fetch failed")) {
        log.warn("Web UI unavailable, falling back to sync batch render", { err: msg });
        // fall through to sync path
      } else {
        throw err;
      }
    }
  }

  const result = await executor.execute<BatchClipsResult>("batch_clips", {
    video_path: videoPath,
    clips,
    transcript_words: transcriptWords,
    clean_fillers: input.clean_fillers !== false,
    allow_ass_fallback: input.allow_ass_fallback === true,
    output_dir: paths.output,
    logo_path: settings.logoPath || null,
  });

  if (!result.data) {
    throw new Error("Batch clip creation returned no data");
  }
  const data = result.data;

  return JSON.stringify({
    total_clips: data.total_clips,
    successful_clips: data.successful_clips,
    results: data.results,
    message: `Batch complete: ${data.successful_clips}/${data.total_clips} clips created successfully.`,
  });
}
