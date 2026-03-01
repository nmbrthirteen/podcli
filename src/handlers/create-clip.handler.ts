import { readFileSync } from "fs";
import { PythonExecutor } from "../services/python-executor.js";
import { FileManager } from "../services/file-manager.js";
import { paths } from "../config/paths.js";
import type { ClipResult } from "../models/index.js";

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

export const createClipToolDef = {
  name: "create_clip",
  description:
    "STEP 3 — Export a single clip as a finished vertical short (1080x1920, 9:16).\n\n" +
    "EASIEST: just pass clip_number (e.g. 3) — everything else auto-loads from session state.\n" +
    "Output: H.264 MP4 with burned-in captions, normalized audio (-14 LUFS).\n\n" +
    "For batch export, use batch_create_clips instead.\n" +
    "Caption styles: branded (professional), hormozi (bold/yellow), karaoke (progressive highlight), subtle (minimal).\n" +
    "Crop modes: face (follows the speaker), center (fixed center crop).",
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
        enum: ["center", "face"],
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
      outro_path: {
        type: "string",
        description: "Path to an outro video to append at the end of the clip",
      },
      generate_thumbnail: {
        type: "boolean",
        description: "[Future] Whether to auto-generate a thumbnail image",
        default: false,
      },
    },
    required: [],
  },
};

export async function handleCreateClip(
  input: Record<string, unknown>
): Promise<string> {
  await fileManager.ensureDirectories();

  const state = loadState();
  const settings = (state?.settings as Record<string, string>) || {};
  const suggestions = (state?.suggestions as Array<Record<string, unknown>>) || [];
  const transcript = state?.transcript as Record<string, unknown> | null;

  // Resolve clip from suggestion number
  let suggestion: Record<string, unknown> | null = null;
  if (input.clip_number != null) {
    const idx = (input.clip_number as number) - 1;
    if (idx < 0 || idx >= suggestions.length) {
      return JSON.stringify({
        error: `Clip #${input.clip_number} not found. Available: 1-${suggestions.length}`,
      });
    }
    suggestion = suggestions[idx];
  }

  // Auto-resolve fields: explicit input > suggestion > state
  const videoPath =
    (input.video_path as string) || (state?.videoPath as string) || "";
  const startSecond =
    (input.start_second as number) ?? (suggestion?.start_second as number);
  const endSecond =
    (input.end_second as number) ?? (suggestion?.end_second as number);
  const title =
    (input.title as string) ||
    (suggestion?.title as string) ||
    "clip";
  const captionStyle =
    (input.caption_style as string) ||
    (suggestion?.suggested_caption_style as string) ||
    settings.captionStyle ||
    "hormozi";
  const cropStrategy =
    (input.crop_strategy as string) || settings.cropStrategy || "face";
  const logoPath =
    (input.logo_path as string) || settings.logoPath || null;
  const outroPath =
    (input.outro_path as string) || settings.outroPath || null;
  const transcriptWords =
    (input.transcript_words as Array<unknown>) ||
    (transcript?.words as Array<unknown>) ||
    [];

  // Validate required fields
  if (!videoPath) {
    return JSON.stringify({ error: "video_path is required (no video in session state)" });
  }
  if (startSecond == null || endSecond == null) {
    return JSON.stringify({
      error: "start_second and end_second are required (use clip_number to reference a suggestion)",
    });
  }

  const result = await executor.execute("create_clip", {
    video_path: videoPath,
    start_second: startSecond,
    end_second: endSecond,
    caption_style: captionStyle,
    crop_strategy: cropStrategy,
    transcript_words: transcriptWords,
    title,
    output_dir: paths.output,
    clean_fillers: input.clean_fillers !== false,
    logo_path: logoPath,
    outro_path: outroPath,
    generate_thumbnail: input.generate_thumbnail || false,
  });

  const data = result.data as unknown as ClipResult;

  return JSON.stringify({
    clip_number: input.clip_number || null,
    output_path: data.output_path,
    duration: data.duration,
    file_size_mb: data.file_size_mb,
    message: `Clip created successfully! ${data.duration}s, ${data.file_size_mb}MB`,
  });
}
