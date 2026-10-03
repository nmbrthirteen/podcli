import { readFileSync } from "fs";
import { PythonExecutor } from "../services/python-executor.js";
import { FileManager } from "../services/file-manager.js";
import { paths } from "../config/paths.js";
import type { ClipResult, CreateClipInput, SuggestedClip, UIState } from "../models/index.js";
import { childLogger } from "../utils/logger.js";
import { sliceTranscript } from "../utils/transcript.js";
import { validateClipRange } from "../utils/clip-validation.js";
import { validateHook } from "../utils/clip-hook.js";
import { transcriptVideoMismatch } from "../utils/video-identity.js";

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
    "Crop modes: speaker (speaker-aware), face (face tracking), center (fixed center crop).\n" +
    "Set keep_caption_overlay=true to retain a ProRes alpha overlay for DaVinci Resolve (export_to_davinci_resolve).",
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
      format: {
        type: "string",
        enum: ["vertical", "horizontal", "square"],
        description:
          "Output aspect ratio. vertical=9:16 shorts, horizontal=16:9, square=1:1. Auto-loaded from session settings if omitted. Default: vertical.",
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
        description: "Outro video (asset name or path) appended at the end of the clip. Uses the default outro asset if omitted.",
      },
      intro_path: {
        type: "string",
        description: "Intro video (asset name or path) prepended before the clip. Uses the default intro asset if omitted.",
      },
      name_card: {
        type: "object",
        description:
          "Lower third naming the speaker for the first seconds: { title, subtitle, seconds, accent }.",
      },
      motion: {
        type: "object",
        description:
          'How each part arrives and leaves, per part: { captions: { enter, exit, duration, feel } }. ' +
          'enter: none|fade|rise|pop, exit: none|fade|sink, feel: snap|soft|linear. ' +
          "Omit to use each caption style's own motion.",
      },
      keep_caption_overlay: {
        type: "boolean",
        description:
          "Keep ProRes 4444 alpha caption overlay for DaVinci Resolve. Returns caption_overlay_path and cropped_source_path.",
        default: false,
      },
      write_clean_variant: {
        type: "boolean",
        description:
          "Also render a second file with the same audio, loudness, and intro/outro but no burned captions. Returns clean_output_path.",
        default: false,
      },
      hook: {
        type: ["object", "null"],
        description:
          "Opening hook: { start, end, mode: repeat|move }, a 1-15s passage from inside the clip played first. " +
          "Auto-loaded from clip_number if omitted; null renders without one.",
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
  const format = input.format || settings.format || "vertical";
  const logoPath = input.logo_path || settings.logoPath || null;
  const outroPath = input.outro_path || settings.outroPath || null;
  const introPath = input.intro_path || settings.introPath || null;
  const transcriptWords = input.transcript_words ?? transcript?.words ?? [];

  // Pull multi-cut segments from suggestion (if available)
  const keepSegments = suggestion?.segments ?? null;
  const hook = input.hook !== undefined ? input.hook : suggestion?.hook ?? null;

  // When the caller relies on the session transcript (rather than passing
  // transcript_words explicitly), refuse to render against a video that was
  // swapped in after that transcript was generated. set_video clears the
  // transcript itself, but older sessions or a stale on-disk state file can
  // still carry a mismatched one.
  if (input.transcript_words == null && transcript) {
    const mismatch = transcriptVideoMismatch(state?.transcriptVideoIdentity, videoPath);
    if (mismatch) {
      return JSON.stringify({ error: mismatch });
    }
  }

  // Validate required fields
  if (!videoPath) {
    return JSON.stringify({ error: "video_path is required (no video in session state)" });
  }
  if (startSecond == null || endSecond == null) {
    return JSON.stringify({
      error: "start_second and end_second are required (use clip_number to reference a suggestion)",
    });
  }
  const rangeError = validateClipRange(startSecond, endSecond, format);
  if (rangeError) {
    return JSON.stringify({ error: rangeError });
  }
  const hookError = validateHook(hook, startSecond, endSecond, keepSegments);
  if (hookError) {
    return JSON.stringify({ error: hookError });
  }

  const result = await executor.execute<ClipResult>("create_clip", {
    video_path: videoPath,
    start_second: startSecond,
    end_second: endSecond,
    caption_style: captionStyle,
    crop_strategy: cropStrategy,
    format,
    transcript_words: transcriptWords,
    title,
    output_dir: paths.output,
    clean_fillers: input.clean_fillers ?? settings.cleanFillers ?? true,
    allow_ass_fallback: input.allow_ass_fallback === true,
    keep_caption_overlay: input.keep_caption_overlay === true,
    write_clean_variant: input.write_clean_variant === true,
    logo_path: logoPath,
    outro_path: outroPath,
    intro_path: introPath,
    ...(input.name_card ? { name_card: input.name_card } : {}),
    ...(input.motion ? { motion: input.motion } : {}),
    ...(keepSegments && { keep_segments: keepSegments }),
    ...(hook && { hook }),
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
    content_type: suggestion?.content_type,
    payoff: suggestion?.payoff,
    context_line: suggestion?.context_line,
    preview_text: suggestion?.preview_text,
    transcript_slice: sliceTranscript(transcriptWords, startSecond, endSecond),
    ...(data.srt_path && { srt_path: data.srt_path }),
    ...(data.vtt_path && { vtt_path: data.vtt_path }),
    ...(data.clean_output_path && { clean_output_path: data.clean_output_path }),
    ...(data.hook && { hook: data.hook }),
    message: `Clip created successfully! ${data.duration}s, ${data.file_size_mb}MB`,
  });
}
