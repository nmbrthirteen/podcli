import { readFileSync } from "fs";
import { PythonExecutor } from "../services/python-executor.js";
import { FileManager } from "../services/file-manager.js";
import { paths } from "../config/paths.js";

const executor = new PythonExecutor();
const fileManager = new FileManager();

/** Load UI state from disk. Returns null if unavailable. */
function loadState(): Record<string, unknown> | null {
  try {
    return JSON.parse(readFileSync(paths.uiState, "utf-8"));
  } catch {
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
              enum: ["center", "face"],
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
    },
    required: [],
  },
};

export async function handleBatchClips(
  input: Record<string, unknown>
): Promise<string> {
  await fileManager.ensureDirectories();

  const state = loadState();
  const settings = (state?.settings as Record<string, string>) || {};
  const suggestions = (state?.suggestions as Array<Record<string, unknown>>) || [];
  const transcript = state?.transcript as Record<string, unknown> | null;

  // Auto-resolve video path
  const videoPath =
    (input.video_path as string) || (state?.videoPath as string) || "";
  if (!videoPath) {
    return JSON.stringify({ error: "video_path is required (no video in session state)" });
  }

  // Auto-resolve transcript words
  const transcriptWords =
    (input.transcript_words as Array<unknown>) ||
    (transcript?.words as Array<unknown>) ||
    [];

  // Build clips array: from export_selected, clip_numbers, or explicit clips
  let clips: Array<Record<string, unknown>>;
  const deselected = (state?.deselectedIndices as number[]) || [];

  const buildClipFromSuggestion = (s: Record<string, unknown>, num: number) => ({
    start_second: s.start_second,
    end_second: s.end_second,
    title: s.title || `clip_${num}`,
    caption_style:
      (s.suggested_caption_style as string) || settings.captionStyle || "hormozi",
    crop_strategy: settings.cropStrategy || "face",
    logo_path: settings.logoPath || null,
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
    const numbers = input.clip_numbers as number[];
    const errors: string[] = [];
    clips = [];

    for (const num of numbers) {
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
    clips = input.clips as Array<Record<string, unknown>>;
  } else {
    return JSON.stringify({
      error: "Use export_selected=true, clip_numbers=[1, 3], or pass clips array",
    });
  }

  const result = await executor.execute("batch_clips", {
    video_path: videoPath,
    clips,
    transcript_words: transcriptWords,
    clean_fillers: input.clean_fillers !== false,
    output_dir: paths.output,
    logo_path: settings.logoPath || null,
  });

  const data = result.data as Record<string, unknown>;

  return JSON.stringify({
    total_clips: data.total_clips,
    successful_clips: data.successful_clips,
    results: data.results,
    message: `Batch complete: ${data.successful_clips}/${data.total_clips} clips created successfully.`,
  });
}
