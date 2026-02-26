import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { z } from "zod";

import { transcribeToolDef, handleTranscribe } from "./handlers/transcribe.handler.js";
import { suggestClipsToolDef, handleSuggestClips } from "./handlers/suggest-clips.handler.js";
import { createClipToolDef, handleCreateClip } from "./handlers/create-clip.handler.js";
import { batchClipsToolDef, handleBatchClips } from "./handlers/batch-clips.handler.js";
import { FileManager } from "./services/file-manager.js";
import { KnowledgeBase } from "./services/knowledge-base.js";
import { AssetManager } from "./services/asset-manager.js";
import { ClipsHistory } from "./services/clips-history.js";
import { paths } from "./config/paths.js";

const kb = new KnowledgeBase();
const assets = new AssetManager();
const history = new ClipsHistory();

/** Prepend knowledge base context to a tool result if available. */
async function withKnowledge(result: string): Promise<string> {
  try {
    const context = await kb.readAll();
    if (!context) return result;
    return `[Knowledge Base]\n${context}\n\n---\n\n${result}`;
  } catch {
    return result;
  }
}

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
        return { content: [{ type: "text" as const, text: await withKnowledge(result) }] };
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
        // Include clip history so Claude avoids duplicates
        const existing = await history.list(20);
        let text = await withKnowledge(result);
        if (existing.length > 0) {
          const summary = existing.map(
            (e) => `  - "${e.title}" ${e.start_second}s–${e.end_second}s (${e.caption_style})`
          ).join("\n");
          text += `\n\n[Previously Created Clips — avoid duplicates]\n${summary}`;
        }
        return { content: [{ type: "text" as const, text }] };
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
        .describe("Path or registered asset name for PNG logo. Shown in top-left (branded style)."),
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
        // Resolve asset names to paths
        if (params.logo_path) {
          const resolved = await assets.resolve(params.logo_path);
          if (resolved) params.logo_path = resolved;
        }

        // Check for duplicates
        const dup = await history.findDuplicate(
          params.video_path as string,
          params.start_second as number,
          params.end_second as number,
          (params.caption_style || "hormozi") as string,
          (params.crop_strategy || "center") as string
        );
        if (dup) {
          return {
            content: [{
              type: "text" as const,
              text: `Duplicate found! This clip was already created on ${dup.created_at}.\nOutput: ${dup.output_path}\nUse a different time range or style to create a new clip.`,
            }],
          };
        }

        const result = await handleCreateClip(params);
        const parsed = JSON.parse(result);

        // Record to history
        await history.record({
          source_video: params.video_path as string,
          start_second: params.start_second as number,
          end_second: params.end_second as number,
          caption_style: (params.caption_style || "hormozi") as string,
          crop_strategy: (params.crop_strategy || "center") as string,
          logo_path: params.logo_path as string | undefined,
          title: (params.title || "clip") as string,
          output_path: parsed.output_path,
          file_size_mb: parsed.file_size_mb,
          duration: parsed.duration,
        });

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
        const parsed = JSON.parse(result);

        // Record each successful clip
        if (parsed.results) {
          for (const r of parsed.results as any[]) {
            if (r.status === "success" && r.output_path) {
              await history.record({
                source_video: params.video_path,
                start_second: r.start_second || 0,
                end_second: r.end_second || 0,
                caption_style: r.caption_style || "hormozi",
                crop_strategy: r.crop_strategy || "center",
                title: r.title || "clip",
                output_path: r.output_path,
                file_size_mb: r.file_size_mb || 0,
                duration: r.duration || 0,
              });
            }
          }
        }

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
  // Tool: knowledge_base
  // =============================================
  server.tool(
    "knowledge_base",
    "Read or manage the podcli knowledge base. These are .md files that provide context about the podcast (hosts, style, audience, etc). Always read the knowledge base before suggesting or creating clips.",
    {
      action: z.enum(["read_all", "list", "read", "write", "delete"]).describe("Action to perform"),
      filename: z.string().optional().describe("Filename for read/write/delete (e.g. 'style.md')"),
      content: z.string().optional().describe("Markdown content for write action"),
    },
    async ({ action, filename, content }) => {
      try {
        if (action === "read_all") {
          const text = await kb.readAll();
          return { content: [{ type: "text" as const, text: text || "Knowledge base is empty. Add .md files to .podcli/knowledge/ in the project directory." }] };
        }
        if (action === "list") {
          const files = await kb.listFiles();
          const text = files.map((f) => `- ${f.filename} (updated ${f.updatedAt})`).join("\n");
          return { content: [{ type: "text" as const, text: text || "No knowledge files found." }] };
        }
        if (action === "read" && filename) {
          const text = await kb.readFile(filename);
          return { content: [{ type: "text" as const, text }] };
        }
        if (action === "write" && filename && content) {
          await kb.writeFile(filename, content);
          return { content: [{ type: "text" as const, text: `Saved ${filename}` }] };
        }
        if (action === "delete" && filename) {
          await kb.deleteFile(filename);
          return { content: [{ type: "text" as const, text: `Deleted ${filename}` }] };
        }
        return { content: [{ type: "text" as const, text: "Invalid action or missing parameters." }] };
      } catch (err: unknown) {
        const msg = err instanceof Error ? err.message : String(err);
        return { content: [{ type: "text" as const, text: `Error: ${msg}` }], isError: true };
      }
    }
  );

  // =============================================
  // Tool: manage_assets
  // =============================================
  server.tool(
    "manage_assets",
    "Register and manage reusable assets (logos, videos). Registered assets can be referenced by name in create_clip instead of full paths.",
    {
      action: z.enum(["list", "register", "unregister", "resolve"]).describe("Action to perform"),
      name: z.string().optional().describe("Asset name (e.g. 'podcast-logo')"),
      path: z.string().optional().describe("Absolute file path (for register)"),
      type: z.enum(["logo", "video", "image", "other"]).optional().describe("Asset type (for register/list filter)"),
    },
    async ({ action, name, path, type }) => {
      try {
        if (action === "list") {
          const items = await assets.list(type || undefined);
          if (items.length === 0) return { content: [{ type: "text" as const, text: "No assets registered." }] };
          const text = items.map((a) => `- ${a.name} (${a.type}): ${a.path}`).join("\n");
          return { content: [{ type: "text" as const, text }] };
        }
        if (action === "register" && name && path) {
          const asset = await assets.register(name, path, type || "other");
          return { content: [{ type: "text" as const, text: `Registered "${asset.name}" → ${asset.path}` }] };
        }
        if (action === "unregister" && name) {
          await assets.unregister(name);
          return { content: [{ type: "text" as const, text: `Unregistered "${name}"` }] };
        }
        if (action === "resolve" && name) {
          const resolved = await assets.resolve(name);
          return { content: [{ type: "text" as const, text: resolved || `Asset "${name}" not found.` }] };
        }
        return { content: [{ type: "text" as const, text: "Invalid action or missing parameters." }] };
      } catch (err: unknown) {
        const msg = err instanceof Error ? err.message : String(err);
        return { content: [{ type: "text" as const, text: `Error: ${msg}` }], isError: true };
      }
    }
  );

  // =============================================
  // Tool: clip_history
  // =============================================
  server.tool(
    "clip_history",
    "View previously created clips to avoid duplicates. Check before creating new clips.",
    {
      action: z.enum(["list", "check"]).describe("list = recent clips, check = find duplicate"),
      source_video: z.string().optional().describe("Source video path (for check or filter)"),
      start_second: z.number().optional().describe("Start time (for check)"),
      end_second: z.number().optional().describe("End time (for check)"),
      caption_style: z.string().optional().describe("Caption style (for check)"),
      crop_strategy: z.string().optional().describe("Crop strategy (for check)"),
      limit: z.number().optional().default(20).describe("Max results for list"),
    },
    async ({ action, source_video, start_second, end_second, caption_style, crop_strategy, limit }) => {
      try {
        if (action === "list") {
          const entries = source_video
            ? await history.getBySource(source_video)
            : await history.list(limit || 20);
          if (entries.length === 0) return { content: [{ type: "text" as const, text: "No clips in history." }] };
          const text = entries
            .map((e) => `- "${e.title}" ${e.start_second}s–${e.end_second}s | ${e.caption_style} | ${e.created_at} | ${e.output_path}`)
            .join("\n");
          return { content: [{ type: "text" as const, text }] };
        }
        if (action === "check" && source_video && start_second !== undefined && end_second !== undefined) {
          const dup = await history.findDuplicate(
            source_video, start_second, end_second,
            caption_style || "hormozi", crop_strategy || "center"
          );
          if (dup) {
            return { content: [{ type: "text" as const, text: `Duplicate found: "${dup.title}" created ${dup.created_at}\nOutput: ${dup.output_path}` }] };
          }
          return { content: [{ type: "text" as const, text: "No duplicate found. Safe to create." }] };
        }
        return { content: [{ type: "text" as const, text: "Invalid action or missing parameters." }] };
      } catch (err: unknown) {
        const msg = err instanceof Error ? err.message : String(err);
        return { content: [{ type: "text" as const, text: `Error: ${msg}` }], isError: true };
      }
    }
  );

  return server;
}
