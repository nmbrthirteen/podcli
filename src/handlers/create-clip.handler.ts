import { readFileSync } from "fs";
import { PythonExecutor } from "../services/python-executor.js";
import { FileManager } from "../services/file-manager.js";
import { paths } from "../config/paths.js";
import type { ClipResult, CreateClipInput, SuggestedClip, UIState } from "../models/index.js";
import { childLogger } from "../utils/logger.js";

const log = childLogger("create-clip");
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

export const createClipToolDef = {
  name: "create_clip",
  description:
    "STEP 3 — Export a single clip as a finished vertical short (1080x1920, 9:16).\n\n" +
    "EASIEST: just pass clip_number (e.g. 3) — everything else auto-loads from session state.\n" +
    "Output: H.264 MP4 with burned-in captions, normalized audio (-14 LUFS).\n\n" +
    "For batch export, use batch_create_clips instead.\n" +
    "Caption styles: branded (professional), hormozi (bold/yellow), karaoke (progressive highlight), subtle (minimal).\n" +
    "Crop modes: speaker (speaker-aware), face (face tracking), center (fixed center crop).",
  inputSchema: {
    type: "object" as const,
    properties: {
      clip_number: {
        type: "number",
        description:
          "Export a suggested clip by its number (from suggest_clips). " +
          "Auto-fills video_path, start/end times, title, and transcript_words from session state.",
      },
      video_path: {
        type: "string",
        description:
          "Path to the podcast video. Auto-loaded from session state if omitted.",
      },
      start_second: {
        type: "number",
        description:
          "Clip start time in seconds. Auto-loaded from clip_number if omitted.",
      },
      end_second: {
        type: "number",
        description:
          "Clip end time in seconds. Auto-loaded from clip_number if omitted.",
      },
      caption_style: {
        type: "string",
        enum: ["hormozi", "karaoke", "subtle", "branded"],
        description:
          "Caption style. Auto-loaded from session settings if omitted.",
      },
      crop_strategy: {
        type: "string",
        enum: ["center", "face", "speaker"],
        description:
          "How to crop to vertical. Auto-loaded from session settings if omitted.",
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
      title: {
        type: "string",
        description:
          "Short title for the clip. Auto-loaded from suggestion if clip_number is used.",
      },
      logo_path: {
        type: "string",
        description:
          "Path to a PNG logo image. Auto-loaded from session settings if omitted.",
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
      outro_path: {
        type: "string",
        description: "Path to an outro video to append at the end of the clip",
      },
    },
    required: [],
  },
};

export async function handleCreateClip(input: CreateClipInput): Promise<string> {
  await fileManager.ensureDirectories();

  const state = loadState();
  const settings = state?.settings ?? {};
  const suggestions: SuggestedClip[] = state?.suggestions ?? [];
  const transcript = state?.transcript ?? null;

  // Resolve clip from suggestion number
  let suggestion: SuggestedClip | null = null;
  if (input.clip_number != null) {
    const idx = input.clip_number - 1;
    if (idx < 0 || idx >= suggestions.length) {
      return JSON.stringify({
        error: `Clip #${input.clip_number} not found. Available: 1-${suggestions.length}`,
      });
    }
    suggestion = suggestions[idx];
  }

  // Auto-resolve fields: explicit input > suggestion > state
  const videoPath = input.video_path || state?.videoPath || "";
  const startSecond = input.start_second ?? suggestion?.start_second;
  const endSecond = input.end_second ?? suggestion?.end_second;
  const title = input.title || suggestion?.title || "clip";
  const captionStyle =
    input.caption_style ||
    suggestion?.suggested_caption_style ||
    settings.captionStyle ||
    "hormozi";
  const cropStrategy = input.crop_strategy || settings.cropStrategy || "speaker";
  const logoPath = input.logo_path || settings.logoPath || null;
  const outroPath = input.outro_path || settings.outroPath || null;
  const transcriptWords = input.transcript_words ?? transcript?.words ?? [];

  // Pull multi-cut segments from suggestion (if available)
  const keepSegments = suggestion?.segments ?? null;

  // Validate required fields
  if (!videoPath) {
    return JSON.stringify({ error: "video_path is required (no video in session state)" });
  }
  if (startSecond == null || endSecond == null) {
    return JSON.stringify({
      error: "start_second and end_second are required (use clip_number to reference a suggestion)",
    });
  }

  const result = await executor.execute<ClipResult>("create_clip", {
    video_path: videoPath,
    start_second: startSecond,
    end_second: endSecond,
    caption_style: captionStyle,
    crop_strategy: cropStrategy,
    transcript_words: transcriptWords,
    title,
    output_dir: paths.output,
    clean_fillers: input.clean_fillers !== false,
    allow_ass_fallback: input.allow_ass_fallback === true,
    logo_path: logoPath,
    outro_path: outroPath,
    ...(keepSegments && { keep_segments: keepSegments }),
  });

  if (!result.data) {
    throw new Error("Clip creation returned no data");
  }
  const data = result.data;

  return JSON.stringify({
    clip_number: input.clip_number ?? null,
    output_path: data.output_path,
    duration: data.duration,
    file_size_mb: data.file_size_mb,
    message: `Clip created successfully! ${data.duration}s, ${data.file_size_mb}MB`,
  });
}
