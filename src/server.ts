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

/** Prepend knowledge base file listing to a tool result. */
async function withKnowledge(result: string): Promise<string> {
  try {
    const files = await kb.listFiles();
    if (!files.length) return result;
    const listing = files.map((f) => f.filename).join(", ");
    return `[Knowledge Base: ${listing} — use knowledge_base tool to read specific files]\n\n${result}`;
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

        // Push suggestions to Web UI (silent fail if UI isn't running)
        try {
          await fetch("http://localhost:3847/api/ui-state", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ suggestions, phase: "review" }),
          });
        } catch {}

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
        // Try routing through web server for real-time UI progress
        let usedWebServer = false;
        try {
          const webRes = await fetch("http://localhost:3847/api/mcp/export", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              video_path: params.video_path,
              clips: params.clips,
              transcript_words: params.transcript_words,
            }),
          });
          if (webRes.ok) {
            const webData = await webRes.json() as any;
            const jobId = webData.job_id;

            // Poll the job until completion
            let jobResult: any = null;
            while (true) {
              await new Promise((r) => setTimeout(r, 2000));
              const pollRes = await fetch(`http://localhost:3847/api/job/${jobId}`);
              if (!pollRes.ok) break;
              const job = await pollRes.json() as any;
              if (job.status === "done") {
                jobResult = job.result;
                break;
              }
              if (job.status === "error") {
                throw new Error(job.error || "Job failed");
              }
            }

            if (jobResult) {
              usedWebServer = true;
              return { content: [{ type: "text" as const, text: JSON.stringify(jobResult, null, 2) }] };
            }
          }
        } catch (webErr: unknown) {
          // Web server not running or errored — fall back to direct execution
          const webMsg = webErr instanceof Error ? webErr.message : String(webErr);
          if (!webMsg.includes("ECONNREFUSED") && !webMsg.includes("fetch failed")) {
            // Unexpected error from web server, still try fallback
          }
        }

        if (!usedWebServer) {
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
        }

        return { content: [{ type: "text" as const, text: "Export completed via web server." }] };
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
        if (action === "read_all" || action === "list") {
          const files = await kb.listFiles();
          if (files.length === 0) {
            return { content: [{ type: "text" as const, text: "Knowledge base is empty. Add .md files to .podcli/knowledge/ in the project directory." }] };
          }
          const summaries: string[] = [];
          for (const f of files) {
            let preview = "";
            try {
              const content = await kb.readFile(f.filename);
              // First non-empty, non-heading line as preview
              const lines = content.split("\n").filter((l: string) => l.trim() && !l.startsWith("#"));
              preview = lines[0]?.trim().slice(0, 100) || "";
            } catch {}
            summaries.push(`- ${f.filename} (updated ${f.updatedAt})${preview ? `\n  ${preview}` : ""}`);
          }
          return { content: [{ type: "text" as const, text: `Knowledge base (${files.length} files) — use read action with filename to get full content:\n${summaries.join("\n")}` }] };
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

  // =============================================
  // Tool: get_ui_state
  // =============================================
  server.tool(
    "get_ui_state",
    "Read the current state of the podcli Web UI — selected clips, video path, settings, transcript, and phase. Always returns the full transcript segments for clip analysis.",
    {},
    async () => {
      try {
        const res = await fetch("http://localhost:3847/api/ui-state");
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const state = await res.json() as any;

        const lines: string[] = [];
        lines.push(`Phase: ${state.phase}`);
        lines.push(`Video: ${state.videoPath || state.filePath || "(none)"}`);
        lines.push(`Settings: caption=${state.settings?.captionStyle}, crop=${state.settings?.cropStrategy}, logo=${state.settings?.logoPath || "none"}`);
        lines.push(`Transcript: ${state.transcriptWordCount} words`);
        lines.push(`Clips: ${state.selectedClips?.length || 0} selected (${state.totalSuggestions || 0} total, ${state.deselectedCount || 0} deselected)`);

        if (state.selectedClips?.length) {
          lines.push("");
          lines.push("Selected clips:");
          for (const clip of state.selectedClips) {
            const title = (clip as any).title || "untitled";
            const start = (clip as any).start_second ?? "?";
            const end = (clip as any).end_second ?? "?";
            lines.push(`  - "${title}" (${start}s – ${end}s)`);
          }
        }

        if (state.transcript) {
          const segments = state.transcript.segments || [];
          if (segments.length) {
            lines.push("");
            lines.push("=== TRANSCRIPT ===");
            for (const seg of segments) {
              const speaker = seg.speaker || "?";
              const start = Math.floor(seg.start || 0);
              const end = Math.floor(seg.end || 0);
              const text = seg.text || "";
              lines.push(`[${start}s-${end}s] ${speaker}: ${text}`);
            }
          }
        } else if (state.rawTranscriptText) {
          lines.push("");
          lines.push("=== RAW TRANSCRIPT (not yet parsed — use this to analyze and suggest clips) ===");
          lines.push(state.rawTranscriptText);
        }

        return { content: [{ type: "text" as const, text: lines.join("\n") }] };
      } catch (err: unknown) {
        const msg = err instanceof Error ? err.message : String(err);
        if (msg.includes("ECONNREFUSED") || msg.includes("fetch failed")) {
          return {
            content: [{ type: "text" as const, text: "Web UI is not running. Start with: npm run ui" }],
          };
        }
        return {
          content: [{ type: "text" as const, text: `Error reading UI state: ${msg}` }],
          isError: true,
        };
      }
    }
  );

  return server;
}
