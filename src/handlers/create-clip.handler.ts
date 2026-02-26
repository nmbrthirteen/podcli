import { PythonExecutor } from "../services/python-executor.js";
import { FileManager } from "../services/file-manager.js";
import { paths } from "../config/paths.js";
import type { ClipResult } from "../models/index.js";

const executor = new PythonExecutor();
const fileManager = new FileManager();

export const createClipToolDef = {
  name: "create_clip",
  description:
    "Create a finished short-form video clip ready for TikTok/YouTube Shorts. " +
    "Takes a segment from the podcast, crops to 9:16 vertical (1080x1920), " +
    "burns styled captions, normalizes audio to -14 LUFS, and exports as H.264 MP4. " +
    "Requires transcript_words from transcribe_podcast for caption timing.",
  inputSchema: {
    type: "object" as const,
    properties: {
      video_path: {
        type: "string",
        description: "Path to the original podcast video file",
      },
      start_second: {
        type: "number",
        description: "Clip start time in seconds",
      },
      end_second: {
        type: "number",
        description: "Clip end time in seconds (max 180s clip length)",
      },
      caption_style: {
        type: "string",
        enum: ["hormozi", "karaoke", "subtle", "branded"],
        description:
          "Caption style: hormozi (bold word-by-word), karaoke (progressive highlight), subtle (clean bottom text). Default: hormozi",
        default: "hormozi",
      },
      crop_strategy: {
        type: "string",
        enum: ["center", "face"],
        description:
          "How to crop to vertical: center (fast, default) or face (detect speaker face). Default: center",
        default: "center",
      },
      transcript_words: {
        type: "array",
        description: "Word-level timestamps from transcribe_podcast",
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
        description: "Short title for the clip (used in filename)",
      },
      logo_path: {
        type: "string",
        description: "Path to a PNG logo image. Shown in top-left corner (used with 'branded' style).",
      },
      // === EXTENSIBILITY: Future pipeline steps ===
      outro_video_path: {
        type: "string",
        description: "[Future] Path to an outro video to append at the end",
      },
      generate_thumbnail: {
        type: "boolean",
        description: "[Future] Whether to auto-generate a thumbnail image",
        default: false,
      },
    },
    required: ["video_path", "start_second", "end_second", "transcript_words"],
  },
};

export async function handleCreateClip(
  input: Record<string, unknown>
): Promise<string> {
  await fileManager.ensureDirectories();

  const result = await executor.execute("create_clip", {
    video_path: input.video_path,
    start_second: input.start_second,
    end_second: input.end_second,
    caption_style: input.caption_style || "hormozi",
    crop_strategy: input.crop_strategy || "center",
    transcript_words: input.transcript_words || [],
    title: input.title || "clip",
    output_dir: paths.output,
    logo_path: input.logo_path || null,
    // Pass through future extensibility params
    outro_video_path: input.outro_video_path || null,
    generate_thumbnail: input.generate_thumbnail || false,
  });

  const data = result.data as unknown as ClipResult;

  return JSON.stringify({
    output_path: data.output_path,
    duration: data.duration,
    file_size_mb: data.file_size_mb,
    message: `Clip created successfully! ${data.duration}s, ${data.file_size_mb}MB`,
  });
}
