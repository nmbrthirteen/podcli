import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { z } from "zod";

import { transcribeToolDef, handleTranscribe } from "./handlers/transcribe.handler.js";
import { suggestClipsToolDef, handleSuggestClips } from "./handlers/suggest-clips.handler.js";
import { createClipToolDef, handleCreateClip } from "./handlers/create-clip.handler.js";
import { batchClipsToolDef, handleBatchClips } from "./handlers/batch-clips.handler.js";
import { FileManager } from "./services/file-manager.js";
import { paths } from "./config/paths.js";

export function createServer(): McpServer {
  const server = new McpServer({
    name: "podcli",
    version: "1.0.0",
  });

  // =============================================
  // Tool: transcribe_podcast
  // =============================================
  server.tool(
    transcribeToolDef.name,
    transcribeToolDef.description,
    {
      file_path: z.string().describe("Absolute path to the podcast file"),
      model_size: z
        .enum(["tiny", "base", "small", "medium", "large"])
        .optional()
        .default("base")
        .describe("Whisper model size"),
      language: z.string().optional().describe("ISO language code"),
      enable_diarization: z
        .boolean()
        .optional()
        .default(true)
        .describe("Enable speaker detection (who is speaking). Default: true"),
      num_speakers: z
        .number()
        .optional()
        .describe("Exact number of speakers if known (e.g. 2). Auto-detects if omitted."),
    },
    async ({ file_path, model_size, language, enable_diarization, num_speakers }) => {
      try {
        const result = await handleTranscribe({ file_path, model_size, language, enable_diarization, num_speakers });
        return { content: [{ type: "text" as const, text: result }] };
      } catch (err: unknown) {
        const msg = err instanceof Error ? err.message : String(err);
        return {
          content: [{ type: "text" as const, text: `Error: ${msg}` }],
          isError: true,
        };
      }
    }
  );

  // =============================================
  // Tool: suggest_clips
  // =============================================
  server.tool(
    suggestClipsToolDef.name,
    suggestClipsToolDef.description,
    {
      suggestions: z
        .array(
          z.object({
            title: z.string(),
            start_second: z.number(),
            end_second: z.number(),
            reasoning: z.string(),
            preview_text: z.string().optional(),
            suggested_caption_style: z
              .enum(["hormozi", "karaoke", "subtle", "branded"])
              .optional(),
          })
        )
        .describe("Array of suggested clip moments"),
    },
    async ({ suggestions }) => {
      try {
        const result = await handleSuggestClips({ suggestions });
        return { content: [{ type: "text" as const, text: result }] };
      } catch (err: unknown) {
        const msg = err instanceof Error ? err.message : String(err);
        return {
          content: [{ type: "text" as const, text: `Error: ${msg}` }],
          isError: true,
        };
      }
    }
  );

  // =============================================
  // Tool: create_clip
  // =============================================
  server.tool(
    createClipToolDef.name,
    createClipToolDef.description,
    {
      video_path: z.string().describe("Path to the original podcast video"),
      start_second: z.number().describe("Clip start time in seconds"),
      end_second: z.number().describe("Clip end time in seconds"),
      caption_style: z
        .enum(["hormozi", "karaoke", "subtle", "branded"])
        .optional()
        .default("hormozi")
        .describe("Caption style"),
      crop_strategy: z
        .enum(["center", "face"])
        .optional()
        .default("center")
        .describe("Cropping strategy"),
      transcript_words: z
        .array(
          z.object({
            word: z.string(),
            start: z.number(),
            end: z.number(),
            confidence: z.number().optional().default(0),
          })
        )
        .describe("Word-level timestamps"),
      title: z.string().optional().default("clip").describe("Clip title"),
      logo_path: z
        .string()
        .optional()
        .describe("Path to PNG logo image. Shown in top-left (used with 'branded' style)."),
      outro_video_path: z
        .string()
        .optional()
        .describe("[Future] Path to outro video to append"),
      generate_thumbnail: z
        .boolean()
        .optional()
        .default(false)
        .describe("[Future] Auto-generate thumbnail"),
    },
    async (params) => {
      try {
        const result = await handleCreateClip(params);
        return { content: [{ type: "text" as const, text: result }] };
      } catch (err: unknown) {
        const msg = err instanceof Error ? err.message : String(err);
        return {
          content: [{ type: "text" as const, text: `Error: ${msg}` }],
          isError: true,
        };
      }
    }
  );

  // =============================================
  // Tool: batch_create_clips
  // =============================================
  server.tool(
    batchClipsToolDef.name,
    batchClipsToolDef.description,
    {
      video_path: z.string().describe("Path to the original podcast video"),
      clips: z
        .array(
          z.object({
            start_second: z.number(),
            end_second: z.number(),
            title: z.string().optional(),
            caption_style: z.enum(["hormozi", "karaoke", "subtle", "branded"]).optional(),
            crop_strategy: z.enum(["center", "face"]).optional(),
          })
        )
        .describe("Array of clips to create"),
      transcript_words: z
        .array(
          z.object({
            word: z.string(),
            start: z.number(),
            end: z.number(),
            confidence: z.number().optional().default(0),
          })
        )
        .describe("Word-level timestamps"),
    },
    async (params) => {
      try {
        const result = await handleBatchClips(params);
        return { content: [{ type: "text" as const, text: result }] };
      } catch (err: unknown) {
        const msg = err instanceof Error ? err.message : String(err);
        return {
          content: [{ type: "text" as const, text: `Error: ${msg}` }],
          isError: true,
        };
      }
    }
  );

  return server;
}
