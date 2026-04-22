import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { z } from "zod";

import {
  transcribeToolDef,
  handleTranscribe,
  transcribeStartToolDef,
  handleTranscribeStart,
  transcribeStatusToolDef,
  handleTranscribeStatus,
} from "./handlers/transcribe.handler.js";
import { suggestClipsToolDef, handleSuggestClips } from "./handlers/suggest-clips.handler.js";
import { createClipToolDef, handleCreateClip } from "./handlers/create-clip.handler.js";
import { batchClipsToolDef, handleBatchClips } from "./handlers/batch-clips.handler.js";
import { FileManager } from "./services/file-manager.js";
import { KnowledgeBase } from "./services/knowledge-base.js";
import { AssetManager } from "./services/asset-manager.js";
import { ClipsHistory } from "./services/clips-history.js";
import { TranscriptCache } from "./services/transcript-cache.js";
import { paths } from "./config/paths.js";
import { childLogger } from "./utils/logger.js";
import type { BatchClipsResult, UIState } from "./models/index.js";

const log = childLogger("server");

/**
 * Fire-and-forget notification to the optional Web UI.
 * Never throws — the UI may not be running, which is expected.
 */
async function uiPing(body: Record<string, unknown>): Promise<void> {
  try {
    await fetch("http://localhost:3847/api/ui-state", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  } catch (err) {
    log.debug("UI ping failed (Web UI likely not running)", {
      err: err instanceof Error ? err.message : String(err),
    });
  }
}

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

/** Append a workflow next-step hint to a tool result. */
function withNextStep(result: string, nextStep: string): string {
  return `${result}\n\n---\n[Next Step] ${nextStep}`;
}

/**
 * Read UI state from the optional Web UI server.
 * Returns null when the UI isn't running (expected in headless mode).
 *
 * NOTE: The returned shape also includes a few fields the Web UI writes
 * (transcriptWordCount, etc.) that aren't part of the canonical UIState
 * model. We widen UIState locally for those extras.
 */
interface ServerUIState extends UIState {
  transcriptWordCount?: number;
}

interface WebJob {
  job_id: string;
  status: "pending" | "running" | "done" | "error";
  result?: unknown;
  error?: string;
}

interface OutputClip {
  filename: string;
  size_mb: number;
  created?: string;
}

interface ApiError {
  error?: string;
}

interface FileInfo {
  filename?: string;
  name?: string;
  path?: string;
  size_mb?: number;
  duration?: number;
}

interface PresetConfig {
  caption_style?: string;
  crop_strategy?: string;
  logo_path?: string;
  outro_path?: string;
}

interface PresetResult extends ApiError {
  presets?: Array<string | { name?: string }>;
  preset?: unknown;
  config?: PresetConfig;
}

interface ImportTranscriptResult extends ApiError {
  data?: {
    transcript?: string;
    words?: unknown[];
    segments?: unknown[];
    duration?: number;
  };
}

async function readUIState(): Promise<ServerUIState | null> {
  try {
    const res = await fetch("http://localhost:3847/api/ui-state");
    if (!res.ok) return null;
    return (await res.json()) as ServerUIState;
  } catch (err) {
    log.debug("readUIState failed (UI likely not running)", {
      err: err instanceof Error ? err.message : String(err),
    });
    return null;
  }
}

/** Generate workflow guidance based on current state. */
async function getWorkflowGuidance(): Promise<string> {
  const state = await readUIState();
  if (!state) {
    return (
      "WORKFLOW — Start from scratch:\n" +
      "1. Set the video: use set_video or transcribe_podcast with a file path\n" +
      "2. Get a transcript: use transcribe_podcast (auto) or import_transcript / parse_transcript\n" +
      "3. Read the transcript: use get_ui_state(include_transcript: true)\n" +
      "4. Suggest clips: analyze the transcript yourself, then call suggest_clips with your picks\n" +
      "5. Export: use batch_create_clips(export_selected: true) or create_clip(clip_number: N)\n\n" +
      "Note: The Web UI is not running. Start it with: npm run ui"
    );
  }

  const phase = state.phase ?? "idle";
  const hasVideo = !!(state.videoPath || state.filePath);
  const wordCount = state.transcriptWordCount ?? 0;
  const hasTranscript = wordCount > 0;
  const rawText = state.rawTranscriptText ?? "";
  const hasRawTranscript = rawText.length > 0;
  const suggestions = state.suggestions ?? [];
  const deselected = state.deselectedIndices ?? [];
  const selectedCount = suggestions.length - deselected.length;

  const lines: string[] = [];

  if (!hasVideo) {
    lines.push(
      "NEXT: No video loaded yet. To begin:\n" +
      "  → Use transcribe_podcast(file_path: \"/path/to/episode.mp4\") to transcribe and set the video in one step\n" +
      "  → Or use set_video(file_path: ...) if you'll import a transcript separately"
    );
  } else if (!hasTranscript && !hasRawTranscript) {
    lines.push(
      `NEXT: Video is loaded but no transcript yet.\n` +
      `  → Use transcribe_podcast(file_path: \"${state.videoPath}\") to auto-transcribe with Whisper\n` +
      `  → Or the user can paste a transcript in the Web UI and you can read it with get_ui_state(include_transcript: true)`
    );
  } else if (hasRawTranscript && !hasTranscript) {
    lines.push(
      "NEXT: Raw transcript text is available but not yet parsed.\n" +
      "  → Use get_ui_state(include_transcript: true) to read the raw text\n" +
      "  → Then use parse_transcript to get word-level timestamps\n" +
      "  → Or analyze the text directly and call suggest_clips with your findings"
    );
  } else if (hasTranscript && suggestions.length === 0) {
    lines.push(
      `NEXT: Transcript is ready (${wordCount} words). Time to find viral moments!\n` +
      "  → Use get_ui_state(include_transcript: true) to read the full transcript\n" +
      "  → Analyze it for the most engaging, viral-worthy moments\n" +
      "  → Then call suggest_clips with your suggestions (title, start_second, end_second, reasoning)"
    );
  } else if (phase === "review" && selectedCount > 0) {
    lines.push(
      `NEXT: ${selectedCount} clips are ready for export!\n` +
      "  → Use batch_create_clips(export_selected: true) to export all selected clips at once\n" +
      "  → Or use create_clip(clip_number: N) to export a specific one\n" +
      "  → Use modify_clip to adjust timing or titles before export\n" +
      "  → Use toggle_clip to select/deselect clips"
    );
  } else if (phase === "done" || phase === "idle" && suggestions.length > 0) {
    lines.push(
      "DONE: Clips have been exported!\n" +
      "  → Use list_outputs to see all rendered clips\n" +
      "  → Use get_ui_state(include_transcript: true) to find more moments\n" +
      "  → Try different caption styles (hormozi, karaoke, subtle, branded) for variety"
    );
  }

  return lines.join("\n");
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

        // Push transcript to Web UI state (fire-and-forget)
        try {
          const parsed = JSON.parse(result);
          await uiPing({
            videoPath: file_path,
            filePath: file_path,
            transcript: parsed,
            phase: "idle",
          });
        } catch (err) {
          log.warn("Failed to parse transcript for UI push", {
            err: err instanceof Error ? err.message : String(err),
          });
        }

        const text = withNextStep(
          await withKnowledge(result),
          "Transcript is ready! Now read it with get_ui_state(include_transcript: true), " +
          "analyze it for viral moments, then call suggest_clips with your findings."
        );
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
  // Tool: transcribe_start (async)
  // =============================================
  server.tool(
    transcribeStartToolDef.name,
    transcribeStartToolDef.description,
    {
      file_path: z.string(),
      model_size: z.enum(["tiny", "base", "small", "medium", "large"]).optional().default("base"),
      language: z.string().optional(),
      enable_diarization: z.boolean().optional().default(true),
      num_speakers: z.number().optional(),
    },
    async (input) => {
      try {
        const text = await handleTranscribeStart(input);
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
  // Tool: transcribe_status (poll)
  // =============================================
  server.tool(
    transcribeStatusToolDef.name,
    transcribeStatusToolDef.description,
    {
      job_id: z.string(),
      wait_seconds: z.number().min(0).max(60).optional().default(30),
    },
    async (input) => {
      try {
        const text = await handleTranscribeStatus(input);
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
            segments: z
              .array(z.object({ start: z.number(), end: z.number() }))
              .optional()
              .describe("Multi-cut keep-ranges. Omit for a single continuous clip."),
            reasoning: z.string(),
            preview_text: z.string().optional(),
            content_type: z.string().optional(),
            score: z.number().optional(),
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

        // Push enriched suggestions (with clip_ids) to Web UI
        try {
          const parsed = JSON.parse(result);
          await uiPing({ suggestions: parsed.clips, phase: "review" });
        } catch (err) {
          log.warn("Failed to parse suggestions for UI push", {
            err: err instanceof Error ? err.message : String(err),
          });
        }

        // Include clip history so Claude avoids duplicates
        const existing = await history.list(20);
        let text = await withKnowledge(result);
        if (existing.length > 0) {
          const summary = existing.map(
            (e) => `  - "${e.title}" ${e.start_second}s–${e.end_second}s (${e.caption_style})`
          ).join("\n");
          text += `\n\n[Previously Created Clips — avoid duplicates]\n${summary}`;
        }

        const parsed2 = JSON.parse(result);
        const clipCount = parsed2.clip_count || 0;
        text = withNextStep(
          text,
          `${clipCount} clips suggested and sent to the UI! The user can review them in the Web UI.\n` +
          "  → To export all at once: batch_create_clips(export_selected: true)\n" +
          "  → To export specific ones: create_clip(clip_number: 1) or batch_create_clips(clip_numbers: [1, 3, 5])\n" +
          "  → To adjust: modify_clip(clip_number: N, updates: {start_second: ..., end_second: ...})\n" +
          "  → To remove one: toggle_clip(clip_number: N, selected: false)"
        );
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
      clip_number: z.number().optional().describe("Export a suggested clip by its number (from suggest_clips). Auto-fills video_path, start/end times, title, and transcript_words from session state."),
      video_path: z.string().optional().describe("Path to the original podcast video. Auto-loaded from session state if clip_number is provided."),
      start_second: z.number().optional().describe("Clip start time in seconds. Auto-loaded from clip_number if omitted."),
      end_second: z.number().optional().describe("Clip end time in seconds. Auto-loaded from clip_number if omitted."),
      caption_style: z
        .enum(["hormozi", "karaoke", "subtle", "branded"])
        .optional()
        .default("hormozi")
        .describe("Caption style"),
      crop_strategy: z
        .enum(["center", "face", "speaker"])
        .optional()
        .default("speaker")
        .describe("Cropping strategy"),
      allow_ass_fallback: z
        .boolean()
        .optional()
        .default(false)
        .describe("Allow ASS caption fallback if Remotion rendering fails (default: false)"),
      transcript_words: z
        .array(
          z.object({
            word: z.string(),
            start: z.number(),
            end: z.number(),
            confidence: z.number().optional().default(0),
          })
        )
        .optional()
        .describe("Word-level timestamps. Auto-loaded from session state if omitted."),
      title: z.string().optional().default("clip").describe("Clip title"),
      logo_path: z
        .string()
        .optional()
        .describe("Path or registered asset name for PNG logo. Shown in top-left (branded style)."),
      outro_path: z
        .string()
        .optional()
        .describe("Path to an outro video to append at the end of the clip"),
    },
    async (params) => {
      try {
        // Resolve asset names to paths
        if (params.logo_path) {
          const resolved = await assets.resolve(params.logo_path);
          if (resolved) params.logo_path = resolved;
        }

        // Resolve clip_number from UI state BEFORE routing
        let keepSegments: Array<{ start: number; end: number }> | null = null;
        if (params.clip_number != null && (params.start_second == null || params.end_second == null)) {
          const uiState = await readUIState();
          const suggestions = uiState?.suggestions ?? [];
          const settings = uiState?.settings ?? {};
          const idx = (params.clip_number as number) - 1;
          if (idx < 0 || idx >= suggestions.length) {
            return {
              content: [{
                type: "text" as const,
                text: `Clip #${params.clip_number} not found. Available: 1-${suggestions.length}`,
              }],
            };
          }
          const suggestion = suggestions[idx];
          if (params.start_second == null) params.start_second = suggestion.start_second as number;
          if (params.end_second == null) params.end_second = suggestion.end_second as number;
          if (!params.video_path) params.video_path = (uiState?.videoPath || uiState?.filePath || "") as string;
          if (!params.title || params.title === "clip") params.title = (suggestion.title as string) || "clip";
          if (!params.caption_style || params.caption_style === "hormozi") {
            params.caption_style = ((suggestion.suggested_caption_style as string) || settings.captionStyle || "hormozi") as typeof params.caption_style;
          }
          if (!params.transcript_words) {
            const transcript = uiState?.transcript;
            if (transcript?.words) params.transcript_words = transcript.words;
          }
          // Pull multi-cut segments from suggestion
          const segs = suggestion.segments as Array<{ start: number; end: number }> | undefined;
          if (segs && segs.length > 0) {
            keepSegments = segs;
          }
        }

        // Check for duplicates
        const dup = await history.findDuplicate(
          params.video_path as string,
          params.start_second as number,
          params.end_second as number,
          (params.caption_style || "hormozi") as string,
          (params.crop_strategy || "speaker") as string
        );
        if (dup) {
          return {
            content: [{
              type: "text" as const,
              text: `Duplicate found! This clip was already created on ${dup.created_at}.\nOutput: ${dup.output_path}\nUse a different time range or style to create a new clip.`,
            }],
          };
        }

        // Route through web server for real-time UI progress tracking
        let usedWebServer = false;
        let finalResult = "";
        try {
          const webRes = await fetch("http://localhost:3847/api/mcp/export", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              video_path: params.video_path,
              clips: [{
                start_second: params.start_second,
                end_second: params.end_second,
                title: (params.title || "clip") as string,
                caption_style: params.caption_style || "hormozi",
                crop_strategy: params.crop_strategy || "speaker",
                allow_ass_fallback: params.allow_ass_fallback === true,
                ...(keepSegments && { segments: keepSegments }),
              }],
              transcript_words: params.transcript_words,
              logo_path: params.logo_path || null,
              outro_path: params.outro_path || null,
            }),
          });
          if (webRes.ok) {
            const webData = (await webRes.json()) as WebJob;
            const jobId = webData.job_id;

            // Poll the job until completion (1 hour max)
            const deadline = Date.now() + 3600_000;
            while (Date.now() < deadline) {
              await new Promise((r) => setTimeout(r, 2000));
              const pollRes = await fetch(`http://localhost:3847/api/job/${jobId}`);
              if (!pollRes.ok) break;
              const job = (await pollRes.json()) as WebJob;
              if (job.status === "done") {
                usedWebServer = true;
                finalResult = JSON.stringify(job.result, null, 2);
                break;
              }
              if (job.status === "error") {
                throw new Error(job.error || "Job failed");
              }
            }
            if (!usedWebServer) {
              throw new Error("Clip creation timed out after 1 hour");
            }
          }
        } catch (webErr: unknown) {
          const webMsg = webErr instanceof Error ? webErr.message : String(webErr);
          if (!webMsg.includes("ECONNREFUSED") && !webMsg.includes("fetch failed")) {
            // Unexpected error — still try direct fallback
          }
        }

        if (!usedWebServer) {
          // Notify UI that export is starting
          await uiPing({ phase: "exporting" });

          finalResult = await handleCreateClip(params);
          const parsed = JSON.parse(finalResult);

          // Record to history
          await history.record({
            source_video: params.video_path as string,
            start_second: params.start_second as number,
            end_second: params.end_second as number,
            caption_style: (params.caption_style || "hormozi") as string,
            crop_strategy: (params.crop_strategy || "speaker") as string,
            logo_path: params.logo_path as string | undefined,
            title: (params.title || "clip") as string,
            output_path: parsed.output_path,
            file_size_mb: parsed.file_size_mb,
            duration: parsed.duration,
          });

          // Notify UI that export is done
          await uiPing({ phase: "done" });
        }

        const clipText = withNextStep(
          finalResult,
          "Clip exported to data/output/! You can:\n" +
          "  → Export more: create_clip(clip_number: N) or batch_create_clips(export_selected: true)\n" +
          "  → List all outputs: list_outputs\n" +
          "  → Try a different style: create_clip(clip_number: N, caption_style: \"karaoke\")"
        );
        return { content: [{ type: "text" as const, text: clipText }] };
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
      video_path: z.string().optional().describe("Path to the original podcast video. Auto-loaded from session state if omitted."),
      clips: z
        .array(
          z.object({
            start_second: z.number(),
            end_second: z.number(),
            title: z.string().optional(),
            caption_style: z.enum(["hormozi", "karaoke", "subtle", "branded"]).optional(),
            crop_strategy: z.enum(["center", "face", "speaker"]).optional(),
            allow_ass_fallback: z.boolean().optional(),
          })
        )
        .optional()
        .describe("Array of clips to create. Auto-loaded from suggestions if omitted."),
      transcript_words: z
        .array(
          z.object({
            word: z.string(),
            start: z.number(),
            end: z.number(),
            confidence: z.number().optional().default(0),
          })
        )
        .optional()
        .describe("Word-level timestamps. Auto-loaded from session state if omitted."),
      export_selected: z.boolean().optional().describe("If true, export all selected suggestions from the UI."),
      clip_numbers: z.array(z.number()).optional().describe("Export specific clip numbers from suggestions (e.g. [1, 3, 5])."),
    },
    async (params) => {
      try {
        // Resolve clip_numbers/export_selected from UI state BEFORE routing
        let resolvedClips = params.clips;
        let resolvedVideoPath = params.video_path;
        let resolvedTranscriptWords = params.transcript_words;
        if (!resolvedClips && (params.export_selected || params.clip_numbers)) {
          const uiState = await readUIState();
          const suggestions = uiState?.suggestions ?? [];
          const settings = uiState?.settings ?? {};
          const deselected = (uiState?.deselectedIndices as number[]) || [];
          if (!resolvedVideoPath) resolvedVideoPath = (uiState?.videoPath || uiState?.filePath || "") as string;
          if (!resolvedTranscriptWords) {
            const transcript = uiState?.transcript;
            if (transcript?.words) resolvedTranscriptWords = transcript.words;
          }

          if (params.export_selected) {
            resolvedClips = suggestions
              .filter((_, i) => !deselected.includes(i))
              .map((s, i) => ({
                start_second: s.start_second,
                end_second: s.end_second,
                title: s.title || `clip_${i + 1}`,
                caption_style: s.suggested_caption_style || settings.captionStyle || "hormozi",
                crop_strategy: settings.cropStrategy || "speaker",
                allow_ass_fallback: false,
                ...(s.segments && s.segments.length > 0 && { keep_segments: s.segments }),
              })) as any;
          } else if (params.clip_numbers) {
            resolvedClips = (params.clip_numbers as number[])
              .filter((n) => n >= 1 && n <= suggestions.length)
              .map((n) => {
                const s = suggestions[n - 1];
                return {
                  start_second: s.start_second,
                  end_second: s.end_second,
                  title: s.title || `clip_${n}`,
                  caption_style: s.suggested_caption_style || settings.captionStyle || "hormozi",
                  crop_strategy: settings.cropStrategy || "speaker",
                  allow_ass_fallback: false,
                  ...(s.segments && s.segments.length > 0 && { keep_segments: s.segments }),
                };
              }) as any;
          }
        }

        // Try routing through web server for real-time UI progress
        // The web server handles SSE broadcasts (export-started, job-update, job-complete)
        let usedWebServer = false;
        let finalResult: string = "";
        try {
          const webRes = await fetch("http://localhost:3847/api/mcp/export", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              video_path: resolvedVideoPath,
              clips: resolvedClips,
              transcript_words: resolvedTranscriptWords,
            }),
          });
          if (webRes.ok) {
            const webData = (await webRes.json()) as WebJob;
            const jobId = webData.job_id;

            // Poll the job until completion (1 hour max)
            const deadline = Date.now() + 3600_000;
            while (Date.now() < deadline) {
              await new Promise((r) => setTimeout(r, 2000));
              const pollRes = await fetch(`http://localhost:3847/api/job/${jobId}`);
              if (!pollRes.ok) break;
              const job = (await pollRes.json()) as WebJob;
              if (job.status === "done") {
                usedWebServer = true;
                finalResult = JSON.stringify(job.result, null, 2);
                break;
              }
              if (job.status === "error") {
                throw new Error(job.error || "Job failed");
              }
            }
            if (!usedWebServer) {
              throw new Error("Batch clip creation timed out after 1 hour");
            }
          }
        } catch (webErr: unknown) {
          const webMsg = webErr instanceof Error ? webErr.message : String(webErr);
          if (!webMsg.includes("ECONNREFUSED") && !webMsg.includes("fetch failed")) {
            // Unexpected error — still try fallback
          }
        }

        if (!usedWebServer) {
          // Fallback: run directly without web server (no UI progress)
          await uiPing({ phase: "exporting" });

          finalResult = await handleBatchClips(params);
          const parsed = JSON.parse(finalResult) as BatchClipsResult;

          // Record each successful clip
          if (parsed.results) {
            for (const r of parsed.results) {
              if (r.status === "success" && r.output_path) {
                await history.record({
                  source_video: params.video_path || "",
                  start_second: r.start_second || 0,
                  end_second: r.end_second || 0,
                  caption_style: r.caption_style || "hormozi",
                  crop_strategy: r.crop_strategy || "speaker",
                  title: r.title || "clip",
                  output_path: r.output_path,
                  file_size_mb: r.file_size_mb || 0,
                  duration: r.duration || 0,
                });
              }
            }
          }

          await uiPing({ phase: "done" });
        }

        const batchText = withNextStep(
          finalResult,
          "Batch export complete! Clips are in data/output/. You can:\n" +
          "  → Use list_outputs to see all rendered clips with file sizes\n" +
          "  → Find more moments: get_ui_state(include_transcript: true) and suggest_clips again\n" +
          "  → Re-export with different styles: update_settings then batch_create_clips(export_selected: true)"
        );
        return { content: [{ type: "text" as const, text: batchText }] };
      } catch (err: unknown) {
        // Notify UI of error
        await uiPing({ phase: "review" });
        log.error("batch_create_clips failed", { err: err instanceof Error ? err.stack : String(err) });
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
            } catch (err) {
              log.debug("Failed to read KB file for preview", {
                file: f.filename,
                err: err instanceof Error ? err.message : String(err),
              });
            }
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
      action: z.enum(["list", "register", "unregister", "resolve", "import"]).describe("Action to perform"),
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
        if (action === "import" && path && name) {
          const asset = await assets.importFile(path, name, type || "other");
          return { content: [{ type: "text" as const, text: `Imported "${asset.name}" → ${asset.path}` }] };
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
            caption_style || "hormozi", crop_strategy || "speaker"
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
    "Read the current podcli session state and get guidance on what to do next. " +
    "Returns: video path, transcript status, clip suggestions, settings, and workflow next steps.\n\n" +
    "IMPORTANT: Call this FIRST when starting a new conversation to understand the current state.\n" +
    "Clips are numbered #1, #2, etc. Use these numbers with create_clip(clip_number), " +
    "batch_create_clips(clip_numbers), modify_clip, and toggle_clip.\n\n" +
    "Set include_transcript=true to analyze transcript content. Returns a compact phrase-grouped " +
    "markdown view (~10x smaller than raw segments) with speaker attribution, silence gaps, and " +
    "optional energy peaks — the primary surface for reasoning about clip boundaries.",
    {
      include_transcript: z.boolean().optional().default(false).describe(
        "Include full transcript segments in the response. Set true when analyzing content for clip suggestions."
      ),
    },
    async ({ include_transcript }) => {
      try {
        const res = await fetch("http://localhost:3847/api/ui-state");
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const state = (await res.json()) as ServerUIState;

        const lines: string[] = [];
        lines.push(`Phase: ${state.phase}`);
        lines.push(`Video: ${state.videoPath || state.filePath || "(none)"}`);
        lines.push(`Settings: caption=${state.settings?.captionStyle}, crop=${state.settings?.cropStrategy}, logo=${state.settings?.logoPath || "none"}`);
        lines.push(`Transcript: ${state.transcriptWordCount ?? 0} words`);

        const allSuggestions = state.suggestions ?? [];
        const deselected = state.deselectedIndices ?? [];
        const selectedCount = allSuggestions.length - deselected.length;
        lines.push(`Clips: ${selectedCount} selected, ${allSuggestions.length} total`);

        if (allSuggestions.length) {
          lines.push("");
          lines.push("Clips (use these numbers with create_clip/batch_create_clips):");
          for (let i = 0; i < allSuggestions.length; i++) {
            const clip = allSuggestions[i];
            const num = i + 1;
            const title = clip.title || "untitled";
            const start = clip.start_second ?? "?";
            const end = clip.end_second ?? "?";
            const duration = typeof start === "number" && typeof end === "number"
              ? `${Math.round(end - start)}s`
              : "?";
            const style = clip.suggested_caption_style || "hormozi";
            const tag = deselected.includes(i) ? " [DESELECTED]" : "";
            lines.push(`  #${num}: "${title}" (${start}s–${end}s, ${duration}) [${style}]${tag}`);
          }
        }

        if (include_transcript) {
          // Prefer packed markdown (10x smaller, phrase-grouped, LLM-readable).
          // Auto-generated during transcription — see backend/services/transcript_packer.py.
          const transcriptCache = new TranscriptCache();
          let packed: string | null = null;
          if (state.videoPath) {
            packed = await transcriptCache.getPackedMarkdown(state.videoPath);
          }
          if (!packed && state.rawTranscriptText) {
            packed = await transcriptCache.getPackedMarkdownFromText(state.rawTranscriptText);
          }
          if (packed) {
            lines.push("");
            lines.push("=== PACKED TRANSCRIPT ===");
            lines.push(packed);
          } else if (state.transcript) {
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
        }

        // Append workflow guidance
        const guidance = await getWorkflowGuidance();
        lines.push("");
        lines.push("---");
        lines.push(guidance);

        return { content: [{ type: "text" as const, text: lines.join("\n") }] };
      } catch (err: unknown) {
        const msg = err instanceof Error ? err.message : String(err);
        if (msg.includes("ECONNREFUSED") || msg.includes("fetch failed")) {
          const guidance = await getWorkflowGuidance();
          return {
            content: [{ type: "text" as const, text: `Web UI is not running. Start with: npm run ui\n\n${guidance}` }],
          };
        }
        return {
          content: [{ type: "text" as const, text: `Error reading UI state: ${msg}` }],
          isError: true,
        };
      }
    }
  );

  // =============================================
  // Tool: modify_clip
  // =============================================
  server.tool(
    "modify_clip",
    "Adjust a suggested clip before exporting. Change timing, title, or caption style. " +
    "Use action='delete' to remove a clip entirely. Reference clips by clip_number (from get_ui_state).",
    {
      clip_number: z.number().optional().describe("Clip number (1-based, from get_ui_state)"),
      clip_id: z.string().optional().describe("UUID of the clip (alternative to clip_number)"),
      index: z.number().optional().describe("0-based index (deprecated, use clip_number)"),
      action: z.enum(["update", "delete"]).optional().default("update").describe("Action: 'update' (default) or 'delete'"),
      updates: z.object({
        title: z.string().optional(),
        start_second: z.number().optional(),
        end_second: z.number().optional(),
        reasoning: z.string().optional(),
        preview_text: z.string().optional(),
        suggested_caption_style: z.enum(["hormozi", "karaoke", "subtle", "branded"]).optional(),
      }).optional().describe("Partial fields to update on the clip (ignored when action='delete')"),
    },
    async ({ clip_number, clip_id, index, action, updates }) => {
      try {
        // 1. Read current UI state
        const res = await fetch("http://localhost:3847/api/ui-state");
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const state = (await res.json()) as ServerUIState;
        const suggestions = state.suggestions ?? [];

        if (!suggestions.length) {
          return { content: [{ type: "text" as const, text: "No suggestions in UI state." }] };
        }

        // 2. Find target clip (clip_number is 1-based)
        let targetIdx = -1;
        if (clip_number !== undefined) {
          targetIdx = clip_number - 1;
        } else if (clip_id) {
          targetIdx = suggestions.findIndex((s) => s.clip_id === clip_id);
        } else if (index !== undefined) {
          targetIdx = index;
        }

        if (targetIdx < 0 || targetIdx >= suggestions.length) {
          return { content: [{ type: "text" as const, text: `Clip not found. Use get_ui_state to see available clips.` }] };
        }

        // --- DELETE ---
        if (action === "delete") {
          const removed = suggestions[targetIdx];
          suggestions.splice(targetIdx, 1);
          // Adjust deselectedIndices: remove the deleted index, shift higher ones down
          const deselected: number[] = state.deselectedIndices || [];
          const adjusted = deselected
            .filter((i: number) => i !== targetIdx)
            .map((i: number) => (i > targetIdx ? i - 1 : i));

          await fetch("http://localhost:3847/api/ui-state", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ suggestions, deselectedIndices: adjusted }),
          });

          return {
            content: [{
              type: "text" as const,
              text: `Deleted clip #${targetIdx + 1}: "${removed.title}". ${suggestions.length} clips remaining.`,
            }],
          };
        }

        // --- UPDATE ---
        const upd = updates || {};
        if (Object.keys(upd).length === 0) {
          return { content: [{ type: "text" as const, text: "No updates provided. Specify at least one field: title, start_second, end_second, reasoning, preview_text, or suggested_caption_style." }] };
        }
        const clip = suggestions[targetIdx];
        if (upd.title !== undefined) clip.title = upd.title;
        if (upd.start_second !== undefined) clip.start_second = upd.start_second;
        if (upd.end_second !== undefined) clip.end_second = upd.end_second;
        if (upd.reasoning !== undefined) clip.reasoning = upd.reasoning;
        if (upd.preview_text !== undefined) clip.preview_text = upd.preview_text;
        if (upd.suggested_caption_style !== undefined) clip.suggested_caption_style = upd.suggested_caption_style;

        // Recalculate derived fields
        clip.duration = Math.round((clip.end_second - clip.start_second) * 10) / 10;
        const fmtTime = (s: number) => `${Math.floor(s / 60)}:${Math.floor(s % 60).toString().padStart(2, "0")}`;
        clip.timestamp_display = `${fmtTime(clip.start_second)} → ${fmtTime(clip.end_second)}`;

        suggestions[targetIdx] = clip;
        await fetch("http://localhost:3847/api/ui-state", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ suggestions }),
        });

        return {
          content: [{
            type: "text" as const,
            text: `Updated clip #${targetIdx + 1}: "${clip.title}" (${clip.start_second}s–${clip.end_second}s, ${clip.duration}s)`,
          }],
        };
      } catch (err: unknown) {
        const msg = err instanceof Error ? err.message : String(err);
        if (msg.includes("ECONNREFUSED") || msg.includes("fetch failed")) {
          return { content: [{ type: "text" as const, text: "Web UI is not running. Start with: npm run ui" }] };
        }
        return { content: [{ type: "text" as const, text: `Error: ${msg}` }], isError: true };
      }
    }
  );

  // =============================================
  // Tool: toggle_clip
  // =============================================
  server.tool(
    "toggle_clip",
    "Select or deselect a suggested clip by clip_number. Selected clips are exported with export_selected.",
    {
      clip_number: z.number().optional().describe("Clip number (1-based, from get_ui_state)"),
      clip_id: z.string().optional().describe("UUID of the clip (alternative to clip_number)"),
      index: z.number().optional().describe("0-based index (deprecated, use clip_number)"),
      selected: z.boolean().describe("true = select, false = deselect"),
    },
    async ({ clip_number, clip_id, index, selected }) => {
      try {
        const res = await fetch("http://localhost:3847/api/ui-state");
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const state = (await res.json()) as ServerUIState;
        const suggestions = state.suggestions ?? [];
        const deselected: number[] = state.deselectedIndices ?? [];

        // Find target index (clip_number is 1-based)
        let targetIdx = -1;
        if (clip_number !== undefined) {
          targetIdx = clip_number - 1;
        } else if (clip_id) {
          targetIdx = suggestions.findIndex((s) => s.clip_id === clip_id);
        } else if (index !== undefined) {
          targetIdx = index;
        }

        if (targetIdx < 0 || targetIdx >= suggestions.length) {
          return { content: [{ type: "text" as const, text: "Clip not found. Use get_ui_state to see available clips." }] };
        }

        let updated: number[];
        if (selected) {
          updated = deselected.filter((i: number) => i !== targetIdx);
        } else {
          updated = deselected.includes(targetIdx) ? deselected : [...deselected, targetIdx];
        }

        await fetch("http://localhost:3847/api/ui-state", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ deselectedIndices: updated }),
        });

        const clip = suggestions[targetIdx];
        return {
          content: [{
            type: "text" as const,
            text: `Clip #${targetIdx + 1} "${clip.title}" is now ${selected ? "selected" : "deselected"}. (${suggestions.length - updated.length}/${suggestions.length} selected)`,
          }],
        };
      } catch (err: unknown) {
        const msg = err instanceof Error ? err.message : String(err);
        if (msg.includes("ECONNREFUSED") || msg.includes("fetch failed")) {
          return { content: [{ type: "text" as const, text: "Web UI is not running. Start with: npm run ui" }] };
        }
        return { content: [{ type: "text" as const, text: `Error: ${msg}` }], isError: true };
      }
    }
  );

  // =============================================
  // Tool: update_settings
  // =============================================
  server.tool(
    "update_settings",
    "Update rendering settings (caption style, crop strategy, logo, outro) in the Web UI.",
    {
      caption_style: z.enum(["hormozi", "karaoke", "subtle", "branded"]).optional().describe("Caption style"),
      crop_strategy: z.enum(["center", "face", "speaker"]).optional().describe("Cropping strategy"),
      logo_path: z.string().optional().describe("Path or registered asset name for PNG logo"),
      outro_path: z.string().optional().describe("Path or registered asset name for outro video"),
    },
    async ({ caption_style, crop_strategy, logo_path, outro_path }) => {
      try {
        const settings: Record<string, string> = {};
        if (caption_style) settings.captionStyle = caption_style;
        if (crop_strategy) settings.cropStrategy = crop_strategy;
        if (logo_path) {
          const resolved = await assets.resolve(logo_path);
          settings.logoPath = resolved || logo_path;
        }
        if (outro_path) {
          const resolved = await assets.resolve(outro_path);
          settings.outroPath = resolved || outro_path;
        }

        if (Object.keys(settings).length === 0) {
          return { content: [{ type: "text" as const, text: "No settings provided. Specify at least one of: caption_style, crop_strategy, logo_path, outro_path." }] };
        }

        await fetch("http://localhost:3847/api/ui-state", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ settings }),
        });

        const parts = Object.entries(settings).map(([k, v]) => `${k}=${v}`);
        return { content: [{ type: "text" as const, text: `Settings updated: ${parts.join(", ")}` }] };
      } catch (err: unknown) {
        const msg = err instanceof Error ? err.message : String(err);
        if (msg.includes("ECONNREFUSED") || msg.includes("fetch failed")) {
          return { content: [{ type: "text" as const, text: "Web UI is not running. Start with: npm run ui" }] };
        }
        return { content: [{ type: "text" as const, text: `Error: ${msg}` }], isError: true };
      }
    }
  );

  // =============================================
  // Tool: list_outputs
  // =============================================
  server.tool(
    "list_outputs",
    "List all rendered clip files in the output directory with file sizes and dates.",
    {},
    async () => {
      try {
        const res = await fetch("http://localhost:3847/api/outputs");
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const clips = (await res.json()) as OutputClip[];

        if (!clips.length) {
          return { content: [{ type: "text" as const, text: "No rendered clips found." }] };
        }

        const lines = clips.map((c) => {
          const date = c.created ? c.created.split("T")[0] : "unknown";
          return `  - ${c.filename} (${c.size_mb} MB, ${date})`;
        });

        return {
          content: [{ type: "text" as const, text: `${clips.length} rendered clip${clips.length === 1 ? "" : "s"}:\n${lines.join("\n")}` }],
        };
      } catch (err: unknown) {
        const msg = err instanceof Error ? err.message : String(err);
        if (msg.includes("ECONNREFUSED") || msg.includes("fetch failed")) {
          return { content: [{ type: "text" as const, text: "Web UI is not running. Start with: npm run ui" }] };
        }
        return { content: [{ type: "text" as const, text: `Error: ${msg}` }], isError: true };
      }
    }
  );

  // =============================================
  // Tool: manage_presets
  // =============================================
  server.tool(
    "manage_presets",
    "Save, load, list, or delete rendering presets. Presets store caption_style, crop_strategy, logo_path, and outro_path for quick reuse.",
    {
      action: z.enum(["list", "save", "load", "delete"]).describe("Preset action"),
      name: z.string().optional().describe("Preset name (required for save/load/delete)"),
      config: z.object({
        caption_style: z.enum(["hormozi", "karaoke", "subtle", "branded"]).optional(),
        crop_strategy: z.enum(["center", "face", "speaker"]).optional(),
        logo_path: z.string().optional(),
        outro_path: z.string().optional(),
      }).optional().describe("Preset config (for save action)"),
    },
    async ({ action, name, config }) => {
      try {
        if (["save", "load", "delete"].includes(action) && !name) {
          return { content: [{ type: "text" as const, text: `Error: 'name' is required for action '${action}'.` }], isError: true };
        }

        const res = await fetch("http://localhost:3847/api/presets", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ action, name, config }),
        });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = (await res.json()) as PresetResult;

        if (data.error) {
          return { content: [{ type: "text" as const, text: `Error: ${data.error}` }], isError: true };
        }

        if (action === "list") {
          const presets = data.presets ?? [];
          if (!presets.length) return { content: [{ type: "text" as const, text: "No presets saved." }] };
          return { content: [{ type: "text" as const, text: `Presets:\n${presets.map((p) => `  - ${typeof p === "string" ? p : p.name || JSON.stringify(p)}`).join("\n")}` }] };
        }

        if (action === "load" && data.config) {
          // Push loaded config to UI settings
          const settings: Record<string, string> = {};
          if (data.config.caption_style) settings.captionStyle = data.config.caption_style;
          if (data.config.crop_strategy) settings.cropStrategy = data.config.crop_strategy;
          if (data.config.logo_path) settings.logoPath = data.config.logo_path;
          if (data.config.outro_path) settings.outroPath = data.config.outro_path;
          if (Object.keys(settings).length) {
            await fetch("http://localhost:3847/api/ui-state", {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ settings }),
            });
          }
          return { content: [{ type: "text" as const, text: `Loaded preset "${name}" and applied to UI settings.` }] };
        }

        if (action === "save") {
          return { content: [{ type: "text" as const, text: `Saved preset "${name}".` }] };
        }

        if (action === "delete") {
          return { content: [{ type: "text" as const, text: `Deleted preset "${name}".` }] };
        }

        return { content: [{ type: "text" as const, text: JSON.stringify(data) }] };
      } catch (err: unknown) {
        const msg = err instanceof Error ? err.message : String(err);
        if (msg.includes("ECONNREFUSED") || msg.includes("fetch failed")) {
          return { content: [{ type: "text" as const, text: "Web UI is not running. Start with: npm run ui" }] };
        }
        return { content: [{ type: "text" as const, text: `Error: ${msg}` }], isError: true };
      }
    }
  );

  // =============================================
  // Tool: analyze_energy
  // =============================================
  server.tool(
    "analyze_energy",
    "Analyze audio energy levels for a video or specific segments. Useful for finding high-energy moments. Defaults to the current UI video and suggestions if not specified.",
    {
      video_path: z.string().optional().describe("Path to video file (defaults to current UI video)"),
      segments: z.array(z.object({
        start: z.number(),
        end: z.number(),
      })).optional().describe("Specific segments to analyze (defaults to current suggestions)"),
    },
    async ({ video_path, segments }) => {
      try {
        let vPath = video_path;
        let segs = segments;

        // If no video_path, read from UI state
        if (!vPath || !segs) {
          const stateRes = await fetch("http://localhost:3847/api/ui-state");
          if (stateRes.ok) {
            const state = (await stateRes.json()) as ServerUIState;
            if (!vPath) vPath = state.videoPath || state.filePath;
            if (!segs) {
              const suggestions = state.suggestions ?? [];
              segs = suggestions.map((s) => ({ start: s.start_second, end: s.end_second }));
            }
          }
        }

        if (!vPath) {
          return { content: [{ type: "text" as const, text: "No video path. Set a video first or provide video_path." }] };
        }

        if (!segs || segs.length === 0) {
          return { content: [{ type: "text" as const, text: "No segments to analyze. Provide segments explicitly or suggest clips first." }] };
        }

        const res = await fetch("http://localhost:3847/api/analyze-energy", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ video_path: vPath, segments: segs }),
        });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = (await res.json()) as ApiError & Record<string, unknown>;

        if (data.error) {
          return { content: [{ type: "text" as const, text: `Error: ${data.error}` }], isError: true };
        }

        return { content: [{ type: "text" as const, text: JSON.stringify(data, null, 2) }] };
      } catch (err: unknown) {
        const msg = err instanceof Error ? err.message : String(err);
        if (msg.includes("ECONNREFUSED") || msg.includes("fetch failed")) {
          return { content: [{ type: "text" as const, text: "Web UI is not running. Start with: npm run ui" }] };
        }
        return { content: [{ type: "text" as const, text: `Error: ${msg}` }], isError: true };
      }
    }
  );

  // =============================================
  // Tool: set_video
  // =============================================
  server.tool(
    "set_video",
    "Set the working video file without transcribing. Use this when you'll import a transcript separately. " +
    "After this, either transcribe_podcast or import a transcript via import_transcript / parse_transcript.",
    {
      file_path: z.string().describe("Absolute path to the video file"),
    },
    async ({ file_path }) => {
      try {
        // Validate via select-file endpoint
        const selectRes = await fetch("http://localhost:3847/api/select-file", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ file_path }),
        });
        if (!selectRes.ok) {
          const err = (await selectRes.json()) as ApiError;
          return { content: [{ type: "text" as const, text: `Error: ${err.error || "File not found"}` }], isError: true };
        }
        const fileInfo = (await selectRes.json()) as FileInfo;

        // Push to UI state
        await fetch("http://localhost:3847/api/ui-state", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ videoPath: file_path, filePath: file_path }),
        });

        const setText = withNextStep(
          `Video set: ${fileInfo.filename} (${fileInfo.size_mb} MB)`,
          `Now transcribe it: transcribe_podcast(file_path: "${file_path}")\n` +
          "  Or if the user pastes a transcript in the UI, read it with get_ui_state(include_transcript: true)"
        );
        return { content: [{ type: "text" as const, text: setText }] };
      } catch (err: unknown) {
        const msg = err instanceof Error ? err.message : String(err);
        if (msg.includes("ECONNREFUSED") || msg.includes("fetch failed")) {
          return { content: [{ type: "text" as const, text: "Web UI is not running. Start with: npm run ui" }] };
        }
        return { content: [{ type: "text" as const, text: `Error: ${msg}` }], isError: true };
      }
    }
  );

  // =============================================
  // Tool: import_transcript
  // =============================================
  server.tool(
    "import_transcript",
    "Import an external transcript (e.g. from a transcription service) into the UI. Skips Whisper entirely. The transcript must include word-level timestamps.",
    {
      file_path: z.string().describe("Path to the video file the transcript belongs to"),
      transcript: z.object({
        words: z.array(z.object({
          word: z.string(),
          start: z.number(),
          end: z.number(),
          speaker: z.string().optional(),
        })).describe("Word-level timestamps"),
        segments: z.array(z.object({
          text: z.string(),
          start: z.number(),
          end: z.number(),
          speaker: z.string().optional(),
        })).optional().describe("Segment-level transcript"),
        duration: z.number().optional().describe("Total duration in seconds"),
        language: z.string().optional().describe("ISO language code"),
        text: z.string().optional().describe("Full transcript text"),
      }).describe("Transcript data with word-level timestamps"),
    },
    async ({ file_path, transcript }) => {
      try {
        const res = await fetch("http://localhost:3847/api/import-transcript", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ file_path, transcript }),
        });
        if (!res.ok) {
          const err = (await res.json()) as ApiError;
          return { content: [{ type: "text" as const, text: `Error: ${err.error || "Import failed"}` }], isError: true };
        }
        const result = (await res.json()) as ImportTranscriptResult;

        // Push transcript to UI state
        await fetch("http://localhost:3847/api/ui-state", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            transcript: result.data,
            rawTranscriptText: result.data?.transcript || "",
            phase: "review",
            videoPath: file_path,
            filePath: file_path,
          }),
        });

        const wordCount = result.data?.words?.length || 0;
        const duration = result.data?.duration || 0;
        const importText = withNextStep(
          `Transcript imported: ${wordCount} words, ${Math.round(duration)}s duration.`,
          "Now read the transcript with get_ui_state(include_transcript: true), " +
          "analyze it for viral moments, then call suggest_clips with your picks."
        );
        return { content: [{ type: "text" as const, text: importText }] };
      } catch (err: unknown) {
        const msg = err instanceof Error ? err.message : String(err);
        if (msg.includes("ECONNREFUSED") || msg.includes("fetch failed")) {
          return { content: [{ type: "text" as const, text: "Web UI is not running. Start with: npm run ui" }] };
        }
        return { content: [{ type: "text" as const, text: `Error: ${msg}` }], isError: true };
      }
    }
  );

  // =============================================
  // Tool: parse_transcript
  // =============================================
  server.tool(
    "parse_transcript",
    "Parse a raw speaker-labeled plain text transcript into word-level timestamps. Input format: 'Speaker (MM:SS)\\ntext...\\n\\nSpeaker2 (MM:SS)\\ntext...'. Uses the Python backend to generate accurate word timings.",
    {
      file_path: z.string().describe("Path to the video file the transcript belongs to"),
      raw_text: z.string().describe("Raw speaker-labeled transcript text"),
      total_duration: z.number().optional().describe("Total video duration in seconds (helps accuracy)"),
      time_adjust: z.number().optional().default(0).describe("Offset in seconds to add to all timestamps"),
    },
    async ({ file_path, raw_text, total_duration, time_adjust }) => {
      try {
        const res = await fetch("http://localhost:3847/api/parse-transcript", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ file_path, raw_text, total_duration, time_adjust }),
        });
        if (!res.ok) {
          const err = (await res.json()) as ApiError;
          return { content: [{ type: "text" as const, text: `Error: ${err.error || "Parse failed"}` }], isError: true };
        }
        const result = (await res.json()) as ImportTranscriptResult;

        // Push parsed transcript to UI state
        await fetch("http://localhost:3847/api/ui-state", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            transcript: result.data,
            rawTranscriptText: result.data?.transcript || "",
            phase: "review",
            videoPath: file_path,
            filePath: file_path,
          }),
        });

        const wordCount = result.data?.words?.length || 0;
        const segCount = result.data?.segments?.length || 0;
        const parseText = withNextStep(
          `Transcript parsed: ${wordCount} words, ${segCount} segments.`,
          "Now read the transcript with get_ui_state(include_transcript: true), " +
          "analyze it for viral moments, then call suggest_clips with your picks."
        );
        return { content: [{ type: "text" as const, text: parseText }] };
      } catch (err: unknown) {
        const msg = err instanceof Error ? err.message : String(err);
        if (msg.includes("ECONNREFUSED") || msg.includes("fetch failed")) {
          return { content: [{ type: "text" as const, text: "Web UI is not running. Start with: npm run ui" }] };
        }
        return { content: [{ type: "text" as const, text: `Error: ${msg}` }], isError: true };
      }
    }
  );

  // =============================================
  // MCP Prompt: workflow guide
  // =============================================
  server.prompt(
    "workflow",
    "Complete podcli workflow guide — from podcast file to finished clips",
    async () => ({
      messages: [{
        role: "user" as const,
        content: {
          type: "text" as const,
          text: [
            "You are a podcast clip extraction assistant using podcli MCP tools.",
            "Follow this workflow to create viral short-form clips from podcasts:",
            "",
            "## Step 1: Check current state",
            "Call get_ui_state() to see what's already loaded (video, transcript, clips).",
            "",
            "## Step 2: Load the podcast",
            "If no video is set, use transcribe_podcast(file_path: \"/path/to/file.mp4\") to transcribe.",
            "This both sets the video AND generates a transcript with word-level timestamps.",
            "If the user already pasted a transcript in the UI, you can skip to Step 3.",
            "",
            "## Step 3: Read the transcript",
            "Call get_ui_state(include_transcript: true) to read the full transcript.",
            "Also check if there's a knowledge base with podcast context (host names, show style, etc).",
            "",
            "## Step 4: Analyze and suggest clips",
            "Read through the transcript carefully. Look for:",
            "- Controversial or surprising statements",
            "- Strong emotional moments (laughter, passion, anger)",
            "- Clear actionable advice or insights",
            "- Story hooks and cliffhangers",
            "- Quotable one-liners",
            "- Questions that hook the viewer",
            "",
            "For each moment, note the start/end timestamps and craft a catchy title.",
            "Aim for 15-45 second clips (target 20-35s). Then call suggest_clips with your picks.",
            "",
            "## Step 5: Export",
            "Call batch_create_clips(export_selected: true) to render all clips.",
            "Or use create_clip(clip_number: N) for individual clips.",
            "",
            "## Available caption styles:",
            "- branded: Professional look with dark highlight box, gradient, optional logo",
            "- hormozi: Bold uppercase, yellow highlight, high energy pop-on reveal",
            "- karaoke: Full sentence visible, words progressively highlight",
            "- subtle: Clean minimal white text, no effects",
            "",
            "## Tips:",
            "- Use modify_clip to adjust timing before export",
            "- Use toggle_clip to select/deselect clips",
            "- Use update_settings to change the default caption style or crop strategy",
            "- The user can review and adjust clips in the Web UI at http://localhost:3847",
          ].join("\n"),
        },
      }],
    })
  );

  return server;
}
