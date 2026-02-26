import { PythonExecutor } from "../services/python-executor.js";
import { FileManager } from "../services/file-manager.js";
import { paths } from "../config/paths.js";

const executor = new PythonExecutor();
const fileManager = new FileManager();

export const batchClipsToolDef = {
  name: "batch_create_clips",
  description:
    "Create multiple short-form clips from a podcast in one batch. " +
    "Each clip is independently cropped, captioned, and exported. " +
    "Returns status for each clip (success/error).",
  inputSchema: {
    type: "object" as const,
    properties: {
      video_path: {
        type: "string",
        description: "Path to the original podcast video",
      },
      clips: {
        type: "array",
        description: "Array of clips to create",
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
    },
    required: ["video_path", "clips", "transcript_words"],
  },
};

export async function handleBatchClips(
  input: Record<string, unknown>
): Promise<string> {
  await fileManager.ensureDirectories();

  const result = await executor.execute("batch_clips", {
    video_path: input.video_path,
    clips: input.clips,
    transcript_words: input.transcript_words || [],
    output_dir: paths.output,
  });

  const data = result.data as Record<string, unknown>;

  return JSON.stringify({
    total_clips: data.total_clips,
    successful_clips: data.successful_clips,
    results: data.results,
    message: `Batch complete: ${data.successful_clips}/${data.total_clips} clips created successfully.`,
  });
}
