#!/usr/bin/env node
/**
 * podcli — Web UI Server
 *
 * Express server that provides:
 * - File upload endpoint for podcast videos
 * - Transcription with SSE progress streaming
 * - Clip creation with real-time progress
 * - Static file serving for the frontend
 */

import express from "express";
import multer from "multer";
import {
  createReadStream,
  existsSync,
  statSync,
  readFileSync,
  writeFileSync,
  chmodSync,
  realpathSync,
} from "fs";
import { mkdir, readdir, unlink } from "fs/promises";
import path from "path";
import { join, dirname, basename, extname, resolve } from "path";
import { execSync, execFileSync, spawn } from "child_process";
import { lookup } from "dns/promises";
import { isIP } from "net";
import { tmpdir } from "os";
import { fileURLToPath } from "url";
import { v4 as uuidv4 } from "uuid";

import { PythonExecutor, terminateProcessTree } from "../services/python-executor.js";
import { TranscriptCache } from "../services/transcript-cache.js";
import { FileManager } from "../services/file-manager.js";
import { AssetManager, inferType, safeName } from "../services/asset-manager.js";
import { ClipsHistory } from "../services/clips-history.js";
import { KnowledgeBase } from "../services/knowledge-base.js";
import { paths, pythonEnv } from "../config/paths.js";
import { webServerPort } from "../config/server.js";
import { writeFileAtomicSync } from "../utils/atomic-file.js";
import { maxClipSeconds, validateClipRange, validateSuggestionRange } from "../utils/clip-validation.js";
import { advanceProgress, tagSubmittedClip, tagSubmittedClips } from "../utils/clip-results.js";
import { DEMO_ASSETS_DIR } from "./demo-fixtures.js";
import { registerConfigIntegrationRoutes } from "../handlers/integrations.routes.js";
import { childLogger } from "../utils/logger.js";
import { sliceTranscript, sliceWords, findContentType, findSuggestionSegments } from "../utils/transcript.js";
import { errMsg } from "../utils/errors.js";
import type {
  AssetType,
  BatchClipsResult,
  ClipHistoryEntry,
  ClipResult,
  Format,
  ProgressEvent,
  SuggestedClip,
  TranscriptResult,
  WordTimestamp,
} from "../models/index.js";

const log = childLogger("web-server");

const __dirname = dirname(fileURLToPath(import.meta.url));

const publicDir = existsSync(join(__dirname, "public", "index.html"))
  ? join(__dirname, "public")
  : resolve(__dirname, "..", "..", "dist", "ui", "public");

const app = express();
const PORT = webServerPort;
const DEMO = process.env.PODCLI_DEMO === "1";

// --- Services ---
const executor = new PythonExecutor();
const cache = new TranscriptCache();
const fileManager = new FileManager();
const assetManager = new AssetManager();
const clipsHistory = new ClipsHistory();
const knowledgeBase = new KnowledgeBase();

// --- Path Traversal Protection ---
function safePath(base: string, filename: string): string | null {
  const root = path.resolve(base);
  const resolved = path.resolve(base, filename);
  // Compare on a path boundary so "/data/output-evil" isn't accepted as inside "/data/output".
  if (resolved !== root && !resolved.startsWith(root + path.sep)) return null;
  return resolved;
}

// --- State ---
// Track active jobs so the UI can poll progress
interface JobState {
  id: string;
  type: "transcribe" | "create_clip" | "batch_clips" | "download_video";
  status: "pending" | "running" | "done" | "error";
  progress: number;
  message: string;
  result?: unknown;
  error?: string;
  createdAt: number;
  finishedAt?: number;
  clip_results?: unknown[];
}

const jobs = new Map<string, JobState>();

// Sweep terminal jobs so the map doesn't grow forever. Completion isn't
// stamped at the many done/error sites; the sweeper stamps it on first sight,
// so a job survives at least one full retention window after finishing.
const JOB_RETENTION_MS = 30 * 60_000;
setInterval(() => {
  const now = Date.now();
  for (const [id, job] of jobs) {
    if (job.status !== "done" && job.status !== "error") continue;
    if (job.finishedAt === undefined) {
      job.finishedAt = now;
    } else if (now - job.finishedAt > JOB_RETENTION_MS) {
      jobs.delete(id);
    }
  }
}, 60_000).unref();

/** Transcript data stored per file, plus optional face-tracking hints. */
type ServerTranscript = TranscriptResult & { face_map?: unknown };

// Store the latest transcript per uploaded file for the session
const sessionTranscripts = new Map<string, ServerTranscript>();

// --- MCP ↔ UI Bridge State ---
interface UIState {
  videoPath: string;
  filePath: string;
  activeExportJobId: string | null;
  transcript: ServerTranscript | null;
  rawTranscriptText: string;
  suggestions: SuggestedClip[];
  deselectedIndices: number[];
  settings: {
    captionStyle: string;
    cropStrategy: string;
    format: string;
    logoPath: string;
    outroPath: string;
    introPath: string;
    cleanFillers: boolean;
    onboardingDismissed: boolean;
  };
  phase: string;
  results: unknown[];
  energyData: Record<string, unknown>;
  lastUpdated: number;
}

// Load persisted state or use defaults
function loadPersistedState(): UIState {
  try {
    if (existsSync(paths.uiState)) {
      const raw = readFileSync(paths.uiState, "utf-8");
      const saved = JSON.parse(raw);
      // Validate video still exists
      if (saved.videoPath && !existsSync(saved.videoPath)) {
        saved.videoPath = "";
        saved.filePath = "";
        saved.phase = "idle";
      }
      return {
        videoPath: saved.videoPath || "",
        filePath: saved.filePath || "",
        activeExportJobId: null,
        transcript: saved.transcript || null,
        rawTranscriptText: saved.rawTranscriptText || "",
        suggestions: saved.suggestions || [],
        deselectedIndices: saved.deselectedIndices || [],
        settings: {
          captionStyle: saved.settings?.captionStyle || "branded",
          cropStrategy: saved.settings?.cropStrategy || "speaker",
          format: saved.settings?.format || "vertical",
          logoPath: saved.settings?.logoPath || "",
          outroPath: saved.settings?.outroPath || "",
          introPath: saved.settings?.introPath || "",
          cleanFillers: saved.settings?.cleanFillers !== false,
          onboardingDismissed: !!saved.settings?.onboardingDismissed,
        },
        // Never restore mid-export phases
        phase: ["exporting", "parsing", "suggesting"].includes(saved.phase)
          ? "idle"
          : saved.phase || "idle",
        results: ["exporting", "parsing", "suggesting"].includes(saved.phase)
          ? []
          : saved.results || [],
        energyData: saved.energyData || {},
        lastUpdated: saved.lastUpdated || 0,
      };
    }
  } catch (err) {
    log.warn("Failed to load persisted UI state; using defaults", {
      err: errMsg(err),
    });
  }
  return {
    videoPath: "",
    filePath: "",
    activeExportJobId: null,
    transcript: null,
    rawTranscriptText: "",
    suggestions: [],
    deselectedIndices: [],
    settings: {
      captionStyle: "branded",
      cropStrategy: "speaker",
      format: "vertical",
      logoPath: "",
      outroPath: "",
      introPath: "",
      cleanFillers: true,
      onboardingDismissed: false,
    },
    phase: "idle",
    results: [],
    energyData: {},
    lastUpdated: 0,
  };
}

const uiState: UIState = loadPersistedState();

// Files the server confirmed the user selected; /api/stream-source serves only
// these, so a forged /api/ui-state can't grant reads of arbitrary files.
const allowedSourcePaths = new Set<string>();

// Recent source videos, referenced in place (never copied) so they can be
// re-picked without re-uploading. Persisted as paths only — no GB bloat.
const sourcesFile = join(paths.home, "sources.json");
const VIDEO_SOURCE_EXTS = new Set([".mp4", ".mov", ".mkv", ".webm", ".m4v", ".avi"]);
let recentSources: string[] = loadRecentSources();

function loadRecentSources(): string[] {
  try {
    const raw = JSON.parse(readFileSync(sourcesFile, "utf-8"));
    return Array.isArray(raw) ? raw.filter((p) => typeof p === "string") : [];
  } catch {
    return [];
  }
}

function rememberSource(realPath: string): void {
  if (!VIDEO_SOURCE_EXTS.has(extname(realPath).toLowerCase())) return;
  recentSources = [realPath, ...recentSources.filter((p) => p !== realPath)].slice(0, 40);
  try {
    writeFileAtomicSync(sourcesFile, JSON.stringify(recentSources, null, 2));
  } catch {}
}

function registerSourcePath(p: string | undefined | null): void {
  if (!p) return;
  try {
    const real = realpathSync(path.resolve(p));
    allowedSourcePaths.add(real);
    rememberSource(real);
  } catch {}
}
registerSourcePath(uiState.videoPath);

// Debounced save to disk
let saveTimer: ReturnType<typeof setTimeout> | null = null;
function persistState() {
  if (saveTimer) clearTimeout(saveTimer);
  saveTimer = setTimeout(() => {
    try {
      writeFileAtomicSync(paths.uiState, JSON.stringify(uiState, null, 2));
    } catch (err) {
      log.warn("Failed to persist UI state to disk", { err: errMsg(err) });
    }
  }, 500);
}

// SSE clients for the global event bus
import type { Request, Response } from "express";
const sseClients: Response[] = [];

function streamVideo(req: Request, res: Response, filePath: string, contentType = "video/mp4") {
  const fileSize = statSync(filePath).size;
  const range = req.headers.range;
  const onErr = (stream: ReturnType<typeof createReadStream>) =>
    stream.on("error", () => res.destroy());
  if (range) {
    const [s, e] = range.replace(/bytes=/, "").split("-");
    const start = parseInt(s, 10);
    const end = e ? parseInt(e, 10) : fileSize - 1;
    if (Number.isNaN(start) || Number.isNaN(end) || start > end || start < 0 || end >= fileSize) {
      res.writeHead(416, { "Content-Range": `bytes */${fileSize}` }).end();
      return;
    }
    res.writeHead(206, {
      "Content-Range": `bytes ${start}-${end}/${fileSize}`,
      "Accept-Ranges": "bytes",
      "Content-Length": end - start + 1,
      "Content-Type": contentType,
    });
    const stream = createReadStream(filePath, { start, end });
    onErr(stream);
    stream.pipe(res);
  } else {
    res.writeHead(200, { "Content-Length": fileSize, "Content-Type": contentType });
    const stream = createReadStream(filePath);
    onErr(stream);
    stream.pipe(res);
  }
}

function broadcastSSE(event: string, data: unknown) {
  const payload = `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;
  for (let i = sseClients.length - 1; i >= 0; i--) {
    try {
      sseClients[i].write(payload);
    } catch {
      sseClients.splice(i, 1);
    }
  }
}

function broadcastHistoryUpdated(jobId: string | null, clips: unknown[]) {
  if (clips.length === 0) return;
  broadcastSSE("history-updated", { jobId, count: clips.length });
}

function setExportState(phase: string, activeExportJobId: string | null) {
  uiState.phase = phase;
  uiState.activeExportJobId = activeExportJobId;
  uiState.lastUpdated = Date.now();
  persistState();
}

function enrichClipWithSegments<T extends { start_second: number; end_second: number; keep_segments?: Array<{ start: number; end: number }> }>(
  clip: T,
): T {
  if (clip.keep_segments?.length) return clip;
  const segments = findSuggestionSegments(uiState.suggestions, clip.start_second, clip.end_second);
  return segments?.length ? { ...clip, keep_segments: segments } : clip;
}

function createBatchHistoryRecorder({
  jobId,
  sourceVideo,
  transcriptWords,
  defaultCaptionStyle,
  defaultCropStrategy,
  defaultFormat,
  label,
  clipSpecs,
  logoPath,
  outroPath,
  introPath,
  cleanFillers,
}: {
  jobId: string;
  sourceVideo: string;
  transcriptWords: WordTimestamp[];
  defaultCaptionStyle?: string;
  defaultCropStrategy?: string;
  defaultFormat?: Format;
  label: string;
  clipSpecs?: Array<{
    start_second: number;
    end_second: number;
    caption_style?: string;
    crop_strategy?: string;
    format?: Format;
    keep_segments?: Array<{ start: number; end: number }>;
  }>;
  logoPath?: string | null;
  outroPath?: string | null;
  introPath?: string | null;
  cleanFillers?: boolean;
}) {
  const recordedClipIndexes = new Set<number>();
  const pendingWrites: Promise<void>[] = [];

  const recordRows = async (rows: BatchClipsResult["results"]) => {
    const recorded = await clipsHistory.recordBatchResults(rows, {
      sourceVideo,
      transcriptWords,
      defaultCaptionStyle,
      defaultCropStrategy,
      defaultFormat,
      contentTypeFor: (s, e) => findContentType(uiState.suggestions, s, e),
    });
    let recordedIdx = 0;
    for (const row of rows) {
      if (row.status !== "success" || !row.output_path) continue;
      const rec = recorded[recordedIdx++];
      if (!rec) continue;
      const spec =
        typeof row.clip_index === "number" ? clipSpecs?.[row.clip_index] : undefined;
      try {
        await clipsHistory.persistClipRecipe(rec, {
          transcriptWords,
          logoPath,
          outroPath,
          introPath,
          cleanFillers,
          keepSegments: spec?.keep_segments,
        });
      } catch (err) {
        log.warn(`Failed to save recipe for ${label} clip`, { err: errMsg(err) });
      }
    }
    for (const row of rows) {
      if (row.status === "success" && row.output_path && typeof row.clip_index === "number") {
        recordedClipIndexes.add(row.clip_index);
      }
    }
    broadcastHistoryUpdated(jobId, recorded);
  };

  return {
    recordProgress(event: ProgressEvent) {
      if (event.stage !== "clip_complete" || !event.clip_result) return;
      const write = recordRows([event.clip_result]).catch((err) => {
        log.warn(`Failed to record completed ${label} clip to history`, {
          err: errMsg(err),
        });
      });
      pendingWrites.push(write);
    },
    async recordRemaining(results: BatchClipsResult["results"] | undefined) {
      await Promise.allSettled(pendingWrites);
      const remaining = results?.filter(
        (row) =>
          typeof row.clip_index !== "number" ||
          !recordedClipIndexes.has(row.clip_index),
      );
      if (remaining?.length) await recordRows(remaining);
    },
  };
}

// --- Middleware ---
app.use(express.json({ limit: "50mb" }));

// Serve static frontend. redirect:false so the /assets page route isn't shadowed
// by a 301 to the Vite bundle's assets/ directory; it falls through to the SPA.
app.use(express.static(publicDir, { redirect: false }));

// File upload config
const uploadDir = join(paths.working, "uploads");
const upload = multer({
  storage: multer.diskStorage({
    destination: async (_req, _file, cb) => {
      await mkdir(uploadDir, { recursive: true });
      cb(null, uploadDir);
    },
    filename: (_req, file, cb) => {
      const ext = extname(file.originalname);
      cb(null, `${uuidv4()}${ext}`);
    },
  }),
  limits: { fileSize: 10 * 1024 * 1024 * 1024 }, // 10 GB
  fileFilter: (_req, file, cb) => {
    const allowed = [
      ".mp4",
      ".mov",
      ".mkv",
      ".webm",
      ".mp3",
      ".wav",
      ".m4a",
      ".png",
      ".jpg",
      ".jpeg",
      ".svg",
    ];
    const ext = extname(file.originalname).toLowerCase();
    if (allowed.includes(ext)) {
      cb(null, true);
    } else {
      cb(
        new Error(
          `Unsupported format: ${ext}. Use MP4, MOV, MKV, WebM, MP3, WAV, M4A.`,
        ),
      );
    }
  },
});

function clearEpisodeSessionState(): void {
  sessionTranscripts.clear();
  allowedSourcePaths.clear();
  uiState.videoPath = "";
  uiState.filePath = "";
  uiState.activeExportJobId = null;
  uiState.transcript = null;
  uiState.rawTranscriptText = "";
  uiState.suggestions = [];
  uiState.deselectedIndices = [];
  uiState.phase = "idle";
  uiState.results = [];
  uiState.energyData = {};
  uiState.lastUpdated = Date.now();
}

function activeBlockingJobs(): JobState[] {
  return [...jobs.values()].filter(
    (job) =>
      job.status === "running" &&
      ["transcribe", "create_clip", "batch_clips"].includes(job.type),
  );
}

function isPublicIp(address: string): boolean {
  const family = isIP(address);
  const mapped = address.toLowerCase().startsWith("::ffff:") ? address.slice(7) : "";
  if (mapped) {
    if (isIP(mapped) === 4) return isPublicIp(mapped);
    const parts = mapped.split(":").map((part) => Number.parseInt(part, 16));
    if (parts.length === 2 && parts.every((part) => Number.isFinite(part))) {
      return isPublicIp([
        parts[0] >> 8,
        parts[0] & 255,
        parts[1] >> 8,
        parts[1] & 255,
      ].join("."));
    }
  }
  if (family === 4) {
    const parts = address.split(".").map((part) => Number(part));
    const [a, b] = parts;
    return !(
      a === 0 ||
      a === 10 ||
      a === 127 ||
      (a === 100 && b >= 64 && b <= 127) ||
      (a === 169 && b === 254) ||
      (a === 172 && b >= 16 && b <= 31) ||
      (a === 192 && b === 168) ||
      (a === 198 && (b === 18 || b === 19)) ||
      a >= 224
    );
  }
  if (family === 6) {
    const normalized = address.toLowerCase();
    return !(
      normalized === "::1" ||
      normalized === "::" ||
      normalized.startsWith("fc") ||
      normalized.startsWith("fd") ||
      normalized.startsWith("fe80:")
    );
  }
  return false;
}

async function validateDownloadUrl(rawUrl: unknown): Promise<string> {
  if (typeof rawUrl !== "string" || !rawUrl.trim()) {
    throw new Error("url is required");
  }
  let parsed: URL;
  try {
    parsed = new URL(rawUrl.trim());
  } catch {
    throw new Error(`Invalid URL: ${rawUrl}`);
  }
  if (!["http:", "https:"].includes(parsed.protocol)) {
    throw new Error(`Unsupported URL protocol: ${parsed.protocol}. Use http or https.`);
  }
  // Best-effort SSRF preflight: yt-dlp still resolves redirects itself at download time.
  const addresses = await lookup(parsed.hostname, { all: true, verbatim: false });
  if (addresses.length === 0 || addresses.some((entry) => !isPublicIp(entry.address))) {
    throw new Error(`Download URL host is not allowed: ${parsed.hostname}. Use a public video URL.`);
  }
  return parsed.toString();
}

// --- API Routes ---

/**
 * POST /api/session-cache/clear - Clear in-memory UI state.
 */
app.post("/api/session-cache/clear", async (_req, res) => {
  try {
    const active = activeBlockingJobs();
    if (active.length > 0) {
      res.status(409).json({
        error: "Cannot clear session cache while jobs are running",
        jobs: active.map((job) => ({ id: job.id, type: job.type, status: job.status })),
      });
      return;
    }
    clearEpisodeSessionState();
    persistState();
    broadcastSSE("state-sync", uiState);
    res.json({ ok: true });
  } catch (err) {
    res.status(500).json({ error: `Failed to clear session cache: ${errMsg(err)}` });
  }
});

/**
 * POST /api/upload — Upload a podcast file
 */
app.post("/api/upload", upload.single("file"), (req, res) => {
  if (!req.file) {
    res.status(400).json({ error: "No file uploaded" });
    return;
  }
  registerSourcePath(req.file.path);
  res.json({
    file_path: req.file.path,
    filename: req.file.originalname,
    size_mb: Math.round((req.file.size / (1024 * 1024)) * 100) / 100,
  });
});

/**
 * POST /api/download-video — Download a video URL with yt-dlp into uploads.
 */
app.post("/api/download-video", async (req, res) => {
  let url: string;
  try {
    url = await validateDownloadUrl(req.body?.url);
  } catch (err) {
    res.status(400).json({ error: errMsg(err) });
    return;
  }

  try {
    await mkdir(uploadDir, { recursive: true });
  } catch (err) {
    res.status(500).json({ error: `Failed to create upload directory: ${errMsg(err)}` });
    return;
  }

  const jobId = uuidv4();
  const job: JobState = {
    id: jobId,
    type: "download_video",
    status: "running",
    progress: 0,
    message: "Downloading video",
    createdAt: Date.now(),
  };
  jobs.set(jobId, job);

  const args = [
    "-m",
    "yt_dlp",
    // Node is enabled only as a local JS runtime; remote EJS components stay disabled.
    "--js-runtimes",
    `node:${process.execPath}`,
    "--no-playlist",
    // Best video+audio up to 1080p merged to mp4. A bare muxed stream (b[ext=mp4])
    // is 360p on YouTube, which then upscales into a terrible-looking reel.
    "--format",
    "bv*[height<=1080]+ba/b[height<=1080]/bv*+ba/b",
    "--merge-output-format",
    "mp4",
    "--ffmpeg-location",
    paths.ffmpegPath,
    "--restrict-filenames",
    "--windows-filenames",
    "--paths",
    uploadDir,
    "--output",
    "%(title).200B [%(id)s].%(ext)s",
    "--newline",
    "--progress",
    "--progress-template",
    "download:podcli-progress:%(progress._percent_str)s",
    "--print",
    "after_move:podcli-filepath:%(filepath)s",
    url,
  ];
  // Detached so a kill takes out yt-dlp's ffmpeg children with it.
  const proc = spawn(paths.pythonPath, args, {
    env: pythonEnv(),
    detached: process.platform !== "win32",
  });

  let stdout = "";
  let stderr = "";
  let outputFilePath = "";
  let settled = false;
  let responded = false;
  const timer = setTimeout(() => terminateProcessTree(proc), 3600_000);
  const finish = (action: () => void): void => {
    if (settled) return;
    settled = true;
    clearTimeout(timer);
    action();
  };

  const readYtDlpOutput = (raw: string): void => {
    for (const line of raw.split(/\r?\n/)) {
      const progress = line.match(/podcli-progress:\s*(\d+(?:\.\d+)?)%/);
      if (progress) {
        job.progress = Number(progress[1]);
        job.message = "Downloading video";
        broadcastSSE("job-update", { jobId, progress: job.progress, message: job.message });
        continue;
      }
      const trimmed = line.trim();
      if (trimmed.startsWith("podcli-filepath:")) {
        outputFilePath = trimmed.slice("podcli-filepath:".length);
      }
    }
  };

  proc.stdout.on("data", (chunk: Buffer) => {
    const raw = chunk.toString();
    stdout += raw;
    readYtDlpOutput(raw);
  });
  proc.stderr.on("data", (chunk: Buffer) => {
    const raw = chunk.toString();
    stderr += raw;
    readYtDlpOutput(raw);
    if (stderr && job.progress === 0) {
      job.message = "Downloading video";
    }
  });
  proc.on("error", (err) => {
    finish(() => {
      job.status = "error";
      job.error = `Failed to start yt-dlp with ${paths.pythonPath}: ${err.message}`;
      job.message = job.error;
      broadcastSSE("job-error", { jobId, error: job.error });
    });
  });
  proc.on("close", (code) => {
    finish(() => {
      if (code !== 0) {
        job.status = "error";
        job.error = `yt-dlp failed for ${url} with exit code ${code}. stderr: ${stderr.slice(-1200)}`;
        job.message = job.error;
        broadcastSSE("job-error", { jobId, error: job.error });
        return;
      }

      const filePath = outputFilePath;
      if (!filePath || !existsSync(filePath)) {
        job.status = "error";
        job.error = `yt-dlp finished but did not report an output file. stdout: ${stdout.slice(-1200)} stderr: ${stderr.slice(-1200)}`;
        job.message = job.error;
        broadcastSSE("job-error", { jobId, error: job.error });
        return;
      }

      const stat = statSync(filePath);
      registerSourcePath(filePath);
      uiState.videoPath = filePath;
      uiState.filePath = filePath;
      uiState.lastUpdated = Date.now();
      persistState();
      broadcastSSE("state-sync", uiState);
      job.status = "done";
      job.progress = 100;
      job.message = "Download complete";
      job.result = {
        file_path: filePath,
        filename: basename(filePath),
        size_mb: Math.round((stat.size / (1024 * 1024)) * 100) / 100,
      };
      broadcastSSE("job-complete", { jobId, result: job.result });
    });
  });
  req.on("close", () => {
    if (!responded && !settled) terminateProcessTree(proc);
  });
  responded = true;
  res.json({ job_id: jobId, status: "running" });
});

/**
 * POST /api/select-file — Use an existing local file (no upload needed)
 */
app.post("/api/select-file", (req, res) => {
  const { file_path } = req.body;
  if (!file_path || !existsSync(file_path)) {
    res.status(400).json({ error: "File not found" });
    return;
  }
  const stat = statSync(file_path);
  registerSourcePath(file_path);
  res.json({
    file_path,
    filename: basename(file_path),
    size_mb: Math.round((stat.size / (1024 * 1024)) * 100) / 100,
  });
});

/**
 * GET /api/browse-file — Open native OS file dialog and return the selected path
 */
app.get("/api/browse-file", (req, res) => {
  const multiple = req.query.multiple === "1" || req.query.multiple === "true";
  try {
    let raw: string;
    if (process.platform === "darwin") {
      const mult = multiple ? " with multiple selections allowed" : "";
      raw = execFileSync(
        "osascript",
        [
          "-e", `set sel to choose file of type {"mp4","mov","mkv","webm","mp3","wav","m4a"}${mult}`,
          "-e", 'if class of sel is not list then set sel to {sel}',
          "-e", "set out to \"\"",
          "-e", "repeat with f in sel",
          "-e", "set out to out & POSIX path of f & linefeed",
          "-e", "end repeat",
          "-e", "return out",
        ],
        { encoding: "utf-8", timeout: 120_000 },
      );
    } else if (process.platform === "win32") {
      // EncodedCommand (UTF-16LE base64) sidesteps cmd→PowerShell quoting; -STA is required by WinForms dialogs.
      const ps = [
        "Add-Type -AssemblyName System.Windows.Forms;",
        "$f = New-Object System.Windows.Forms.OpenFileDialog;",
        "$f.Filter = 'Media files|*.mp4;*.mov;*.mkv;*.webm;*.mp3;*.wav;*.m4a';",
        multiple ? "$f.Multiselect = $true;" : "",
        "if ($f.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) { $f.FileNames -join [Environment]::NewLine }",
      ].join(" ");
      const encoded = Buffer.from(ps, "utf16le").toString("base64");
      raw = execSync(`powershell -NoProfile -STA -EncodedCommand ${encoded}`, {
        encoding: "utf-8",
        timeout: 120_000,
      });
    } else {
      const args = ["--file-selection", "--file-filter=Media files|*.mp4 *.mov *.mkv *.webm *.mp3 *.wav *.m4a"];
      if (multiple) args.push("--multiple", "--separator=\n");
      raw = execFileSync("zenity", args, { encoding: "utf-8", timeout: 120_000 });
    }

    const paths = raw.split("\n").map((p) => p.trim()).filter((p) => p && existsSync(p));
    if (paths.length === 0) {
      res.json({ error: "cancelled" });
      return;
    }
    for (const p of paths) registerSourcePath(p);
    const stat = statSync(paths[0]);
    res.json({
      file_path: paths[0],
      file_paths: paths,
      filename: basename(paths[0]),
      size_mb: Math.round((stat.size / (1024 * 1024)) * 100) / 100,
    });
  } catch {
    // User cancelled the dialog (non-zero exit) or command not found
    res.json({ error: "cancelled" });
  }
});

/**
 * POST /api/import-transcript — Import an existing transcript (skip Whisper)
 * Accepts: { file_path, transcript } where transcript is an object with:
 *   - text: string (full transcript text)
 *   - words: Array<{ word, start, end, speaker? }>
 *   - segments?: Array<{ text, start, end, speaker? }>
 *   - duration?: number
 */
app.post("/api/import-transcript", (req, res) => {
  const { file_path, transcript } = req.body;

  if (!file_path) {
    res.status(400).json({ error: "file_path is required" });
    return;
  }
  if (!transcript || !transcript.words || !Array.isArray(transcript.words)) {
    res.status(400).json({
      error:
        "transcript must include a 'words' array with { word, start, end } objects",
    });
    return;
  }

  // Build a full transcript result from the imported data
  const result: Record<string, unknown> = {
    transcript:
      transcript.text || transcript.words.map((w: any) => w.word).join(" "),
    words: transcript.words,
    segments: transcript.segments || [],
    duration:
      transcript.duration ||
      (transcript.words.length > 0
        ? transcript.words[transcript.words.length - 1].end
        : 0),
    language: transcript.language || "en",
    speakers: transcript.speakers || null,
    speaker_segments: transcript.speaker_segments || null,
    imported: true,
  };

  sessionTranscripts.set(file_path, result as unknown as ServerTranscript);

  res.json({
    status: "done",
    cached: false,
    imported: true,
    data: result,
  });
});

/**
 * POST /api/parse-transcript — Parse a speaker-labeled plain text transcript
 * Format: "Speaker (MM:SS)\ntext...\n\nSpeaker2 (MM:SS)\ntext..."
 * Uses Python backend to generate word-level timestamps.
 */
app.post("/api/parse-transcript", async (req, res) => {
  const { file_path, raw_text, total_duration, time_adjust = 0 } = req.body;

  if (!file_path) {
    res.status(400).json({ error: "file_path is required" });
    return;
  }
  if (!raw_text) {
    res.status(400).json({ error: "raw_text is required" });
    return;
  }

  try {
    const result = await executor.execute("parse_transcript", {
      raw_text,
      total_duration: total_duration || null,
      time_adjust: time_adjust || 0,
    });

    if (result.data) {
      sessionTranscripts.set(
        file_path,
        result.data as unknown as ServerTranscript,
      );
    }

    res.json({
      status: "done",
      imported: true,
      data: result.data,
    });
  } catch (err: any) {
    res
      .status(500)
      .json({ error: err.message || "Failed to parse transcript" });
  }
});

/**
 * POST /api/transcribe — Start transcription job
 */
app.post("/api/transcribe", async (req, res) => {
  const {
    file_path,
    model_size = "base",
    engine,
    assemblyai_api_key,
    language,
    enable_diarization = false,
    num_speakers,
  } = req.body;

  if (!file_path || !existsSync(file_path)) {
    res.status(400).json({ error: "File not found" });
    return;
  }
  // Check cache first
  const cached = await cache.get(file_path, engine);
  if (cached) {
    const jobId = uuidv4();
    sessionTranscripts.set(file_path, cached as unknown as ServerTranscript);
    uiState.transcript = cached as unknown as typeof uiState.transcript;
    uiState.videoPath = file_path;
    uiState.filePath = file_path;
    registerSourcePath(file_path);
    uiState.lastUpdated = Date.now();
    persistState();
    res.json({
      job_id: jobId,
      status: "done",
      cached: true,
      data: cached,
    });
    return;
  }

  const jobId = uuidv4();
  const job: JobState = {
    id: jobId,
    type: "transcribe",
    status: "running",
    progress: 0,
    message: "Starting transcription...",
    createdAt: Date.now(),
  };
  jobs.set(jobId, job);

  res.json({ job_id: jobId, status: "running" });

  // Run async
  executor
    .execute(
      "transcribe",
      { file_path, model_size, engine, assemblyai_api_key, language, enable_diarization, num_speakers },
      (event) => {
        job.progress = event.percent;
        job.message = event.message;
      },
    )
    .then(async (result) => {
      job.status = "done";
      job.progress = 100;
      job.message = "Transcription complete";
      job.result = result.data;
      sessionTranscripts.set(
        file_path,
        result.data as unknown as ServerTranscript,
      );
      // Populate uiState.transcript with the FULL result so downstream
      // batch_create_clips can resolve transcript_words for caption burn-in.
      uiState.transcript = result.data as unknown as typeof uiState.transcript;
      uiState.videoPath = file_path;
      uiState.filePath = file_path;
      registerSourcePath(file_path);
      uiState.lastUpdated = Date.now();
      persistState();
      // Cache it
      try {
        await cache.set(file_path, result.data as unknown as TranscriptResult, engine);
      } catch (err) {
        log.warn("Failed to cache transcript", { file_path, err: errMsg(err) });
      }
    })
    .catch((err) => {
      job.status = "error";
      job.error = err.message;
      job.message = `Error: ${err.message}`;
    });
});

/**
 * POST /api/create-clip — Start clip creation job
 */
app.post("/api/create-clip", async (req, res) => {
  const {
    video_path,
    start_second,
    end_second,
    caption_style = "hormozi",
    crop_strategy = "speaker",
    format = "vertical",
    transcript_words = [],
    title = "clip",
    clean_fillers = false,
    allow_ass_fallback = false,
    content_type = null,
    keep_segments,
  } = req.body;

  if (!video_path || !existsSync(video_path)) {
    res.status(400).json({ error: "Video file not found" });
    return;
  }

  // Resolve asset names (or paths) to real paths; reject only if provided-and-unresolvable.
  let logo_path: string | null = null;
  let outro_path: string | null = null;
  let intro_path: string | null = null;
  for (const [key, raw] of [["logo", req.body.logo_path], ["outro", req.body.outro_path], ["intro", req.body.intro_path]] as const) {
    if (!raw) continue;
    const resolved = await assetManager.resolve(raw);
    if (!resolved) {
      res.status(400).json({ error: `${key} not found: ${raw}` });
      return;
    }
    if (key === "logo") logo_path = resolved;
    else if (key === "outro") outro_path = resolved;
    else intro_path = resolved;
  }

  // Validate clip params before spawning Python
  if (typeof start_second !== "number" || typeof end_second !== "number") {
    res
      .status(400)
      .json({ error: "start_second and end_second must be numbers" });
    return;
  }
  if (end_second <= start_second) {
    res
      .status(400)
      .json({ error: "end_second must be greater than start_second" });
    return;
  }
  const duration = end_second - start_second;
  const maxDur = format === "horizontal" ? 300 : 180;
  if (duration > maxDur) {
    res.status(400).json({
      error: `Clip too long (${Math.round(duration)}s). Max ${maxDur} seconds.`,
    });
    return;
  }
  const validStyles = ["hormozi", "karaoke", "subtle", "branded"];
  if (!validStyles.includes(caption_style)) {
    res
      .status(400)
      .json({ error: `Invalid caption style. Use: ${validStyles.join(", ")}` });
    return;
  }
  const validCrops = ["center", "face", "speaker"];
  if (!validCrops.includes(crop_strategy)) {
    res
      .status(400)
      .json({ error: `Invalid crop strategy. Use: ${validCrops.join(", ")}` });
    return;
  }
  const validFormats = ["vertical", "horizontal", "square"];
  if (!validFormats.includes(format)) {
    res
      .status(400)
      .json({ error: `Invalid format. Use: ${validFormats.join(", ")}` });
    return;
  }

  await fileManager.ensureDirectories();

  const enriched = enrichClipWithSegments({
    start_second,
    end_second,
    keep_segments: Array.isArray(keep_segments) ? keep_segments : undefined,
  });

  const jobId = uuidv4();
  const job: JobState = {
    id: jobId,
    type: "create_clip",
    status: "running",
    progress: 0,
    message: "Preparing clip...",
    createdAt: Date.now(),
  };
  jobs.set(jobId, job);

  res.json({ job_id: jobId, status: "running" });

  executor
    .execute<ClipResult>(
      "create_clip",
      {
        video_path,
        start_second: enriched.start_second,
        end_second: enriched.end_second,
        caption_style,
        crop_strategy,
        format,
        transcript_words,
        title,
        output_dir: paths.output,
        logo_path,
        outro_path,
        intro_path,
        clean_fillers,
        allow_ass_fallback,
        ...(enriched.keep_segments?.length && { keep_segments: enriched.keep_segments }),
      },
      (event) => {
        job.progress = event.percent;
        job.message = event.message;
      },
    )
    .then(async (result) => {
      job.status = "done";
      job.progress = 100;
      job.message = "Clip created!";
      job.result = result.data;
      // Record to history
      try {
        const d = result.data;
        const rec = await clipsHistory.record({
          source_video: video_path,
          start_second,
          end_second,
          caption_style,
          crop_strategy,
          format,
          logo_path: logo_path || undefined,
          outro_path: outro_path || undefined,
          intro_path: intro_path || undefined,
          title,
          output_path: d?.output_path || "",
          file_size_mb: d?.file_size_mb || 0,
          duration: d?.duration || 0,
          content_type: content_type || undefined,
          transcript_slice: sliceTranscript(transcript_words, start_second, end_second),
        });
        await clipsHistory.persistClipRecipe(rec, {
          transcriptWords: transcript_words,
          logoPath: logo_path,
          outroPath: outro_path,
          introPath: intro_path,
          cleanFillers: clean_fillers,
          keepSegments: enriched.keep_segments,
        });
        broadcastHistoryUpdated(jobId, [rec]);
      } catch (err) {
        log.warn("Failed to record clip to history", {
          title,
          err: errMsg(err),
        });
      }
      broadcastSSE("job-complete", { jobId, result: result.data });
    })
    .catch((err) => {
      job.status = "error";
      job.error = err.message;
      job.message = `Error: ${err.message}`;
      broadcastSSE("job-error", { jobId, error: err.message });
    });
});

/**
 * POST /api/batch-clips — Create multiple clips
 */
app.post("/api/batch-clips", async (req, res) => {
  const {
    video_path,
    clips,
    transcript_words = [],
    clean_fillers = false,
    keep_caption_overlay = false,
    format = "vertical",
  } = req.body;

  if (!video_path || !existsSync(video_path)) {
    res.status(400).json({ error: "Video file not found" });
    return;
  }
  if (!clips || !Array.isArray(clips) || clips.length === 0) {
    res.status(400).json({ error: "No clips provided" });
    return;
  }

  let logo_path: string | null = null;
  let outro_path: string | null = null;
  let intro_path: string | null = null;
  for (const [key, raw] of [["logo", req.body.logo_path], ["outro", req.body.outro_path], ["intro", req.body.intro_path]] as const) {
    if (!raw) continue;
    const resolved = await assetManager.resolve(raw);
    if (!resolved) {
      res.status(400).json({ error: `${key} not found: ${raw}` });
      return;
    }
    if (key === "logo") logo_path = resolved;
    else if (key === "outro") outro_path = resolved;
    else intro_path = resolved;
  }
  // Validate each clip's timing
  for (let i = 0; i < clips.length; i++) {
    const c = clips[i];
    const dur = (c.end_second || 0) - (c.start_second || 0);
    if (dur <= 0) {
      res.status(400).json({ error: `Clip ${i + 1}: end must be after start` });
      return;
    }
    if (c.format && !["vertical", "horizontal", "square"].includes(c.format)) {
      res.status(400).json({ error: `Clip ${i + 1}: invalid format "${c.format}". Use: vertical, horizontal, square` });
      return;
    }
    const maxDur = maxClipSeconds(c.format || format);
    if (dur > maxDur) {
      res.status(400).json({
        error: `Clip ${i + 1}: too long (${Math.round(dur)}s). Max ${maxDur}s.`,
      });
      return;
    }
  }

  await fileManager.ensureDirectories();

  const enrichedClips = clips.map((c: { start_second: number; end_second: number; keep_segments?: Array<{ start: number; end: number }> }) =>
    enrichClipWithSegments(c),
  );

  const jobId = uuidv4();
  const job: JobState = {
    id: jobId,
    type: "batch_clips",
    status: "running",
    progress: 0,
    message: "Starting batch...",
    createdAt: Date.now(),
  };
  jobs.set(jobId, job);

  const historyRecorder = createBatchHistoryRecorder({
    jobId,
    sourceVideo: video_path,
    transcriptWords: transcript_words,
    label: "batch",
    clipSpecs: enrichedClips,
    logoPath: logo_path,
    outroPath: outro_path,
    introPath: intro_path,
    cleanFillers: clean_fillers,
  });

  broadcastSSE("export-started", { jobId, clipCount: clips.length });
  setExportState("exporting", jobId);

  res.json({ job_id: jobId, status: "running" });

  executor
    .execute<BatchClipsResult>(
      "batch_clips",
      {
        video_path,
        clips: enrichedClips,
        format,
        transcript_words,
        output_dir: paths.output,
        logo_path,
        outro_path,
        intro_path,
        clean_fillers,
        keep_caption_overlay: keep_caption_overlay === true,
        face_map: uiState.transcript?.face_map,
      },
      (event) => {
        const progress = advanceProgress(job, event.percent);
        job.message = event.message;
        historyRecorder.recordProgress(event);
        const clipResult = event.clip_result
          ? tagSubmittedClip(event.clip_result, enrichedClips)
          : undefined;
        if (event.stage === "clip_complete" && clipResult) {
          (job.clip_results ??= []).push(clipResult);
        }
        broadcastSSE("job-update", {
          jobId,
          progress,
          message: event.message,
          stage: event.stage,
          clip_result: clipResult,
        });
      },
    )
    .then(async (result) => {
      const data = tagSubmittedClips(result.data, enrichedClips);
      job.status = "done";
      job.progress = 100;
      job.message = "Batch complete!";
      job.result = data;
      // Record successful clips to history
      try {
        await historyRecorder.recordRemaining(result.data?.results);
      } catch (err) {
        log.warn("Failed to record batch clips to history", {
          err: errMsg(err),
        });
      }
      setExportState("done", null);
      broadcastSSE("job-complete", { jobId, result: data });
    })
    .catch((err) => {
      job.status = "error";
      job.error = err.message;
      job.message = `Error: ${err.message}`;
      setExportState("review", null);
      broadcastSSE("job-error", { jobId, error: err.message });
    });
});

/**
 * GET /api/job/:id — Poll job status + progress
 */
app.get("/api/job/:id", (req, res) => {
  const job = jobs.get(req.params.id);
  if (!job) {
    res.status(404).json({ error: "Job not found" });
    return;
  }
  res.json(job);
});

/**
 * GET /api/job/:id/stream — SSE progress stream
 */
app.get("/api/job/:id/stream", (req, res) => {
  const jobId = req.params.id;
  const job = jobs.get(jobId);
  if (!job) {
    res.status(404).json({ error: "Job not found" });
    return;
  }

  res.writeHead(200, {
    "Content-Type": "text/event-stream",
    "Cache-Control": "no-cache",
    Connection: "keep-alive",
  });

  const interval = setInterval(() => {
    const current = jobs.get(jobId);
    if (!current) {
      clearInterval(interval);
      res.end();
      return;
    }

    res.write(
      `data: ${JSON.stringify({
        status: current.status,
        progress: current.progress,
        message: current.message,
        result: current.result,
        error: current.error,
        clip_results: current.clip_results,
      })}\n\n`,
    );

    if (current.status === "done" || current.status === "error") {
      clearInterval(interval);
      setTimeout(() => res.end(), 500);
    }
  }, 500);

  req.on("close", () => clearInterval(interval));
});

/**
 * GET /api/outputs — List finished clips
 */
app.get("/api/outputs", async (_req, res) => {
  try {
    await mkdir(paths.output, { recursive: true });
    const files = await readdir(paths.output);
    const clips = files
      .filter((f) => f.endsWith(".mp4"))
      .map((f) => {
        const fullPath = join(paths.output, f);
        const stat = statSync(fullPath);
        return {
          filename: f,
          path: fullPath,
          size_mb: Math.round((stat.size / (1024 * 1024)) * 100) / 100,
          created: stat.mtime.toISOString(),
        };
      })
      .sort(
        (a, b) => new Date(b.created).getTime() - new Date(a.created).getTime(),
      );
    res.json(clips);
  } catch {
    res.json([]);
  }
});

/**
 * GET /api/download/:filename — Download a finished clip
 */
app.get("/api/download/:filename", (req, res) => {
  const filePath = safePath(paths.output, req.params.filename);
  if (!filePath) {
    res.status(400).json({ error: "Invalid filename" });
    return;
  }
  if (!existsSync(filePath)) {
    res.status(404).json({ error: "File not found" });
    return;
  }
  res.download(filePath);
});

/**
 * GET /api/preview/:filename — Stream a video clip for in-browser playback
 */
app.get("/api/preview/:filename", (req, res) => {
  const filePath = safePath(paths.output, req.params.filename);
  if (!filePath) {
    res.status(400).json({ error: "Invalid filename" });
    return;
  }
  if (!existsSync(filePath)) {
    res.status(404).json({ error: "File not found" });
    return;
  }
  streamVideo(req, res, filePath);
});

// Clips now render into the user's working dir, not a single output root, so the
// library streams them by history id from wherever they live. The output_path
// recorded in clips.json IS the allowlist: only files podcli itself logged are
// servable, and only as regular files (symlinks resolved, extension checked).
async function serveClipById(
  req: Request,
  res: Response,
  id: string,
  mode: "preview" | "download",
) {
  const entry = await clipsHistory.findById(id);
  if (!entry || !entry.output_path) {
    res.status(404).json({ error: "Clip not found" });
    return;
  }
  let real: string;
  try {
    real = realpathSync(entry.output_path);
  } catch {
    res.status(404).json({ error: "File no longer exists" });
    return;
  }
  if (!statSync(real).isFile() || !/\.(mp4|mov|mkv|webm)$/i.test(real)) {
    res.status(400).json({ error: "Unsupported clip file" });
    return;
  }
  if (mode === "download") {
    res.download(real);
    return;
  }
  const mimeTypes: Record<string, string> = {
    ".webm": "video/webm",
    ".mov": "video/quicktime",
    ".mkv": "video/x-matroska",
  };
  streamVideo(req, res, real, mimeTypes[extname(real).toLowerCase()] || "video/mp4");
}

app.get("/api/clips/:id/preview", (req, res) => {
  void serveClipById(req, res, req.params.id, "preview");
});

app.get("/api/clips/:id/download", (req, res) => {
  void serveClipById(req, res, req.params.id, "download");
});

/**
 * GET /api/stream-source — Stream the source video for in-browser preview
 * Accepts ?path= query param (must be a file previously validated via /select-file or /upload)
 */
app.get("/api/stream-source", (req, res) => {
  const filePath = req.query.path as string;
  if (!filePath || !existsSync(filePath)) {
    res.status(404).json({ error: "File not found" });
    return;
  }

  // Extension gate: only media/image types stream, so this never serves
  // .env/key/token files regardless of the path checks below.
  const mediaMime: Record<string, string> = {
    ".mp4": "video/mp4",
    ".m4v": "video/mp4",
    ".webm": "video/webm",
    ".mov": "video/quicktime",
    ".avi": "video/x-msvideo",
    ".mkv": "video/x-matroska",
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".m4a": "audio/mp4",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".svg": "image/svg+xml",
  };
  const mime = mediaMime[extname(filePath).toLowerCase()];
  if (!mime) {
    res.status(403).json({ error: "Access denied: unsupported media type" });
    return;
  }

  // realpath defeats symlinks pointing outside the allowed set.
  let resolvedPath: string;
  try {
    resolvedPath = realpathSync(path.resolve(filePath));
  } catch {
    res.status(404).json({ error: "File not found" });
    return;
  }

  const uploadsRoot = path.resolve(join(paths.working, "uploads"));
  const relativeToUploads = path.relative(uploadsRoot, resolvedPath);
  const isUploadedFile =
    relativeToUploads !== "" &&
    !relativeToUploads.startsWith("..") &&
    !path.isAbsolute(relativeToUploads);
  if (!allowedSourcePaths.has(resolvedPath) && !isUploadedFile) {
    res
      .status(403)
      .json({ error: "Access denied: path not in allowed sources" });
    return;
  }

  streamVideo(req, res, filePath, mime);
});

// Route through the CLI so the Studio and the renderer resolve the same config
// path (a direct read here can miss it under the launcher's data dir).
app.get("/api/thumbnail-config", async (_req, res) => {
  const r = await runCli(["thumbnail-config", "show"]);
  if (r.code !== 0) { res.json({}); return; }
  try { res.json(JSON.parse(r.stdout)); } catch { res.json({}); }
});

app.put("/api/thumbnail-config", async (req, res) => {
  const tmp = join(tmpdir(), `podcli-tc-${uuidv4().slice(0, 8)}.json`);
  try {
    writeFileSync(tmp, JSON.stringify(req.body || {}), "utf-8");
    const r = await runCli(["thumbnail-config", "import", tmp]);
    if (r.code !== 0) throw new Error(stripAnsi(r.stderr || r.stdout) || "save failed");
    res.json({ ok: true });
  } catch (e: any) {
    res.status(500).json({ error: e.message });
  } finally {
    try { await unlink(tmp); } catch { /* best effort */ }
  }
});

app.get("/api/thumbnail-config/export", async (_req, res) => {
  const r = await runCli(["thumbnail-config", "show"]);
  if (r.code !== 0) { res.status(500).json({ error: "export failed" }); return; }
  res.setHeader("Content-Disposition", 'attachment; filename="thumbnail-config.json"');
  res.type("application/json").send(r.stdout);
});

app.post("/api/thumbnail-config/reset", async (_req, res) => {
  const r = await runCli(["thumbnail-config", "reset"]);
  if (r.code !== 0) { res.status(500).json({ error: stripAnsi(r.stderr || r.stdout) || "reset failed" }); return; }
  res.json({ ok: true });
});

app.get("/api/image", (req, res) => {
  const raw = req.query.path as string;
  const mimes: Record<string, string> = { ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp", ".gif": "image/gif" };
  const mime = raw ? mimes[extname(raw).toLowerCase()] : undefined;
  if (!mime) {
    res.status(403).json({ error: "unsupported type" });
    return;
  }
  // realpath defeats symlinks pointing outside the allowed roots; home is
  // excluded so the integrations/token files under it are never servable.
  let resolved: string;
  try {
    resolved = realpathSync(path.resolve(raw));
  } catch {
    res.status(404).json({ error: "not found" });
    return;
  }
  const allowedRoots = [paths.output, paths.working, paths.assets].map((p) => path.resolve(p));
  if (DEMO) allowedRoots.push(path.resolve(DEMO_ASSETS_DIR));
  if (!allowedRoots.some((root) => resolved === root || resolved.startsWith(root + path.sep))) {
    res.status(403).json({ error: "access denied" });
    return;
  }
  res.writeHead(200, { "Content-Type": mime, "Cache-Control": "no-cache" });
  const stream = createReadStream(resolved);
  stream.on("error", () => res.destroy());
  stream.pipe(res);
});

registerConfigIntegrationRoutes(app, { executor, uploadDir });

// --- Transcript export (SRT/VTT) ---
app.get("/api/export-transcript", (_req, res) => {
  const format = (_req.query.format as string) || "srt";
  const transcript = uiState.transcript;

  if (!transcript?.words?.length) {
    res.status(400).json({ error: "No transcript available" });
    return;
  }

  const words = transcript.words;

  // Group words into subtitle lines (~8 words each)
  const lineSize = 8;
  const lines: Array<{ text: string; start: number; end: number }> = [];
  for (let i = 0; i < words.length; i += lineSize) {
    const chunk = words.slice(i, i + lineSize);
    lines.push({
      text: chunk.map((w: any) => w.word).join(" "),
      start: chunk[0].start,
      end: chunk[chunk.length - 1].end,
    });
  }

  const fmtSrt = (s: number) => {
    const h = Math.floor(s / 3600);
    const m = Math.floor((s % 3600) / 60);
    const sec = Math.floor(s % 60);
    const ms = Math.round((s % 1) * 1000);
    return `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}:${String(sec).padStart(2, "0")},${String(ms).padStart(3, "0")}`;
  };

  const fmtVtt = (s: number) => fmtSrt(s).replace(",", ".");

  if (format === "vtt") {
    let vtt = "WEBVTT\n\n";
    lines.forEach((line, i) => {
      vtt += `${i + 1}\n${fmtVtt(line.start)} --> ${fmtVtt(line.end)}\n${line.text}\n\n`;
    });
    res.setHeader("Content-Type", "text/vtt");
    res.setHeader("Content-Disposition", "attachment; filename=transcript.vtt");
    res.send(vtt);
  } else if (format === "json") {
    res.setHeader(
      "Content-Disposition",
      "attachment; filename=transcript.json",
    );
    res.json(transcript);
  } else {
    // SRT
    let srt = "";
    lines.forEach((line, i) => {
      srt += `${i + 1}\n${fmtSrt(line.start)} --> ${fmtSrt(line.end)}\n${line.text}\n\n`;
    });
    res.setHeader("Content-Type", "application/x-subrip");
    res.setHeader("Content-Disposition", "attachment; filename=transcript.srt");
    res.send(srt);
  }
});

// --- Analyze audio energy ---
app.post("/api/analyze-energy", async (req, res) => {
  const { video_path, segments } = req.body;
  if (!video_path)
    return res.status(400).json({ error: "video_path required" });
  try {
    const result = await executor.execute("analyze_energy", {
      video_path,
      segments: segments || [],
    });
    res.json(result.data || {});
  } catch (err: any) {
    res.status(500).json({ error: err.message });
  }
});

// --- Highlight reel: detect once, then iterate on moments ---
app.post("/api/reel", async (req, res) => {
  try {
    const result = await executor.execute("manage_reel", req.body || {});
    const data = (result.data || {}) as any;
    if (typeof data.source === "string") registerSourcePath(data.source);
    if (Array.isArray(data.sources)) {
      for (const s of data.sources) if (typeof s === "string") registerSourcePath(s);
    }
    if (typeof data.reel_path === "string") registerSourcePath(data.reel_path);
    if (Array.isArray(data.moments)) {
      for (const m of data.moments) {
        if (m && typeof m.clip_path === "string" && m.clip_exists) registerSourcePath(m.clip_path);
      }
    }
    res.json(data);
  } catch (err: any) {
    res.status(500).json({ error: err.message });
  }
});

app.get("/api/reel-download", (req, res) => {
  const filePath = req.query.path as string;
  if (!filePath) {
    res.status(400).json({ error: "path required" });
    return;
  }
  let resolved: string;
  try {
    resolved = realpathSync(path.resolve(filePath));
  } catch {
    res.status(404).json({ error: "File not found" });
    return;
  }
  if (extname(resolved).toLowerCase() !== ".mp4" || !allowedSourcePaths.has(resolved)) {
    res.status(403).json({ error: "Access denied" });
    return;
  }
  res.download(resolved);
});

// --- Encoder info ---
app.get("/api/encoder-info", async (_req, res) => {
  try {
    const result = await executor.execute("detect_encoder", {});
    res.json(result.data || {});
  } catch (err: any) {
    res.status(500).json({ error: err.message });
  }
});

// --- Speaker Detection Status ---
app.get("/api/speaker-status", (_req, res) => {
  if (DEMO) { res.json({ configured: true, setup_url: "", token_url: "" }); return; }
  const envPath = join(process.cwd(), ".env");
  let token = process.env.HF_TOKEN || "";
  if (!token && existsSync(envPath)) {
    const envContent = readFileSync(envPath, "utf-8");
    const match = envContent.match(/^HF_TOKEN=(.+)$/m);
    if (match) token = match[1].trim();
  }
  res.json({
    configured: !!token,
    setup_url: "https://huggingface.co/pyannote/speaker-diarization-3.1",
    token_url: "https://huggingface.co/settings/tokens",
  });
});

// --- Presets ---
app.get("/api/presets", async (_req, res) => {
  try {
    const result = await executor.execute("presets", { action: "list" });
    res.json(result.data || { presets: [] });
  } catch (err: any) {
    res.status(500).json({ error: err.message });
  }
});

app.post("/api/presets", async (req, res) => {
  const { action, name, config } = req.body;
  try {
    const result = await executor.execute("presets", { action, name, config });
    res.json(result.data || {});
  } catch (err: any) {
    res.status(500).json({ error: err.message });
  }
});

// --- Assets ---
function uniqueAssetName(base: string, taken: Set<string>): string {
  const slug = (base || "asset").replace(/[^a-zA-Z0-9._-]/g, "-").replace(/^-+|-+$/g, "") || "asset";
  if (!taken.has(slug)) return slug;
  let i = 2;
  while (taken.has(`${slug}-${i}`)) i++;
  return `${slug}-${i}`;
}

app.get("/api/assets", async (req, res) => {
  try {
    const items = await assetManager.list(req.query.type as string | undefined);
    res.json(items);
  } catch (err: unknown) {
    res.status(500).json({ error: errMsg(err) });
  }
});

app.post("/api/assets/register", async (req, res) => {
  const { name, path: filePath, type } = req.body;
  try {
    const asset = await assetManager.register(name, filePath, type || inferType(filePath));
    broadcastSSE("assets-updated", { name });
    res.json(asset);
  } catch (err: unknown) {
    res.status(400).json({ error: errMsg(err) });
  }
});

app.post("/api/assets/upload", upload.single("file"), async (req, res) => {
  if (!req.file) {
    res.status(400).json({ error: "No file uploaded" });
    return;
  }
  try {
    const existing = new Set((await assetManager.list()).map((a) => a.name));
    const wanted = safeName((req.body?.name as string) || basename(req.file.originalname, extname(req.file.originalname)));
    const name = existing.has(wanted) && !req.body?.overwrite ? uniqueAssetName(wanted, existing) : wanted;
    const type = (req.body?.type as AssetType) || inferType(req.file.originalname);
    const asset = await assetManager.importFile(req.file.path, name, type);
    broadcastSSE("assets-updated", { name });
    res.json(asset);
  } catch (err: unknown) {
    res.status(400).json({ error: errMsg(err) });
  } finally {
    await unlink(req.file.path).catch(() => {});
  }
});

app.post("/api/assets/url", async (req, res) => {
  let url: string;
  try {
    url = await validateDownloadUrl(req.body?.url);
  } catch (err: unknown) {
    res.status(400).json({ error: errMsg(err) });
    return;
  }
  const jobId = uuidv4();
  const job: JobState = {
    id: jobId,
    type: "download_video",
    status: "running",
    progress: 0,
    message: "Downloading asset",
    createdAt: Date.now(),
  };
  jobs.set(jobId, job);
  res.json({ job_id: jobId });

  (async () => {
    try {
      const existing = new Set((await assetManager.list()).map((a) => a.name));
      const wanted = safeName((req.body?.name as string) || basename(new URL(url).pathname) || "asset");
      const name = existing.has(wanted) ? uniqueAssetName(wanted, existing) : wanted;
      const type = req.body?.type as AssetType | undefined;
      const asset = await assetManager.importUrl(url, name, type);
      job.status = "done";
      job.progress = 100;
      job.result = asset;
      broadcastSSE("job-complete", { jobId, result: asset });
      broadcastSSE("assets-updated", { name });
    } catch (err: unknown) {
      job.status = "error";
      job.error = errMsg(err);
      broadcastSSE("job-error", { jobId, error: job.error });
    }
  })();
});

app.post("/api/assets/:name/default", async (req, res) => {
  try {
    const asset = await assetManager.setDefault(req.params.name);
    broadcastSSE("assets-updated", { name: asset.name });
    res.json(asset);
  } catch (err: unknown) {
    res.status(400).json({ error: errMsg(err) });
  }
});

app.delete("/api/assets/:name/default", async (req, res) => {
  try {
    await assetManager.clearDefault(req.params.name);
    broadcastSSE("assets-updated", { name: req.params.name });
    res.json({ ok: true });
  } catch (err: unknown) {
    res.status(400).json({ error: errMsg(err) });
  }
});

app.post("/api/assets/:name/rename", async (req, res) => {
  try {
    const asset = await assetManager.rename(req.params.name, req.body?.new_name);
    broadcastSSE("assets-updated", { name: asset.name });
    res.json(asset);
  } catch (err: unknown) {
    res.status(400).json({ error: errMsg(err) });
  }
});

app.get("/api/assets/:name/download", async (req, res) => {
  try {
    // Look up by registered name only — never fall through to treating the
    // param as a filesystem path (that would allow arbitrary file reads).
    const asset = (await assetManager.list()).find((a) => a.name === req.params.name);
    if (!asset || !existsSync(asset.path)) {
      res.status(404).json({ error: `Asset "${req.params.name}" not found` });
      return;
    }
    if (req.query.dl) {
      res.download(asset.path, basename(asset.path));
    } else {
      res.sendFile(asset.path);
    }
  } catch (err: unknown) {
    res.status(500).json({ error: errMsg(err) });
  }
});

app.delete("/api/assets/:name", async (req, res) => {
  try {
    await assetManager.unregister(req.params.name);
    broadcastSSE("assets-updated", { name: req.params.name });
    res.json({ ok: true });
  } catch (err: unknown) {
    res.status(400).json({ error: errMsg(err) });
  }
});

app.post("/api/assets/unregister", async (req, res) => {
  try {
    await assetManager.unregister(req.body.name);
    broadcastSSE("assets-updated", { name: req.body.name });
    res.json({ ok: true });
  } catch (err: unknown) {
    res.status(400).json({ error: errMsg(err) });
  }
});

// Recent source videos (referenced in place, not copied).
app.get("/api/sources", (_req, res) => {
  const items = recentSources
    .map((p) => ({ path: p, name: basename(p), exists: existsSync(p) }))
    .filter((s) => s.exists);
  res.json(items);
});

// --- Clip History ---
app.get("/api/history", async (req, res) => {
  try {
    const source = req.query.source as string | undefined;
    const limit = parseInt(req.query.limit as string) || 50;
    const entries = source
      ? await clipsHistory.getBySource(source)
      : await clipsHistory.list(limit);
    res.json(entries);
  } catch (err: any) {
    res.json([]);
  }
});

// Clip edits route through the Python CLI so the web UI and `podcli clips`
// share one history writer (preserves unknown fields like Phase 2 metrics).
const stripAnsi = (s: string) => s.replace(/\x1b\[[0-9;]*m/g, "").trim();

const RUN_PY_TIMEOUT_MS = 15 * 60_000;

function runPy(scriptAndArgs: string[]): Promise<{ code: number; stdout: string; stderr: string }> {
  return new Promise((resolve) => {
    // Detached so a timeout kill takes out the whole process group (ffmpeg etc.).
    const proc = spawn(paths.pythonPath, scriptAndArgs, {
      env: pythonEnv({ PODCLI_HOME: paths.home, PODCLI_DATA: paths.dataDir }),
      detached: process.platform !== "win32",
    });
    let stdout = "", stderr = "";
    let settled = false;
    const finish = (result: { code: number; stdout: string; stderr: string }) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      resolve(result);
    };
    const timer = setTimeout(() => {
      terminateProcessTree(proc);
      finish({
        code: 1,
        stdout,
        stderr: `${stderr}\nTimed out after ${RUN_PY_TIMEOUT_MS / 1000}s`.trim(),
      });
    }, RUN_PY_TIMEOUT_MS);
    proc.stdout.on("data", (d) => (stdout += d));
    proc.stderr.on("data", (d) => (stderr += d));
    proc.on("close", (code) => finish({ code: code ?? 1, stdout, stderr }));
    proc.on("error", (e) => finish({ code: 1, stdout, stderr: String(e) }));
  });
}

const runCli = (args: string[]) =>
  runPy([join(paths.backendDir, "cli.py"), "--no-banner", ...args]);

// Composite a thumbnail PNG onto the start of a clip. stripStart > 0 removes a
// prior card first (avoids stacking on re-bake). Returns the bake's success.
async function bakeThumbnailCard(clipPath: string, image: string, stripStart = 0): Promise<{ ok: boolean; error?: string }> {
  const r = await runCli([
    "bake-thumbnail", clipPath, image, "--position", "start",
    ...(stripStart ? ["--strip-start", String(stripStart)] : []),
  ]);
  return r.code === 0 ? { ok: true } : { ok: false, error: stripAnsi(r.stderr || r.stdout) };
}

app.patch("/api/clips/:id", async (req, res) => {
  if (DEMO) { res.json({ ok: true }); return; } // fixtures are read-only
  const { title, caption_style, thumbnail_config } = req.body || {};
  const args = ["clips", "edit", req.params.id];
  if (title != null) args.push(`--title=${title}`);
  if (caption_style != null) args.push("--caption-style", String(caption_style));
  if (thumbnail_config != null) args.push("--thumbnail-config", JSON.stringify(thumbnail_config));
  if (args.length === 3) {
    res.status(400).json({ error: "nothing to update" });
    return;
  }
  const r = await runCli(args);
  if (r.code !== 0) {
    res.status(400).json({ error: stripAnsi(r.stderr || r.stdout) || "edit failed" });
    return;
  }
  res.json({ ok: true });
});

app.delete("/api/clips/:id", async (req, res) => {
  if (DEMO) { res.json({ ok: true }); return; } // fixtures are read-only
  const r = await runCli(["clips", "delete", req.params.id, "--yes"]);
  if (r.code !== 0) {
    res.status(400).json({ error: stripAnsi(r.stderr || r.stdout) || "delete failed" });
    return;
  }
  broadcastSSE("history-updated", { jobId: null, count: 1 });
  res.json({ ok: true });
});

app.post("/api/clips/:id/reopen", async (req, res) => {
  const r = await runCli(["clips", "reopen", req.params.id]);
  if (r.code !== 0) {
    res.status(400).json({ error: stripAnsi(r.stderr || r.stdout) || "reopen failed" });
    return;
  }
  res.json({ ok: true });
});

app.post("/api/clips/:id/thumbnail", async (req, res) => {
  const clip = await clipsHistory.findById(req.params.id);
  if (!clip) {
    res.status(404).json({ error: "clip not found" });
    return;
  }
  const tc = clip.thumbnail_config || {};
  // Standalone thumbnail generation — produces variation PNGs, never touches the clip video.
  const outDir = join(paths.output, "thumbnails", String(clip.id));
  // Free-text values ride behind "--" (positionals) or as --flag=value so a
  // title starting with "-" can't be parsed as an option.
  const args = [
    "thumbnails",
    "--output", outDir,
    "--variations", "3",
    "--json",
    "--video", clip.source_video,
    "--start", String(clip.start_second),
    "--end", String(clip.end_second),
  ];
  if (tc.image_path) args.push("--photo", String(tc.image_path));
  else if (typeof tc.timestamp === "number") args.push("--timestamp", String(tc.timestamp));
  if (tc.line1) args.push(`--line1=${tc.line1}`);
  if (tc.line2) args.push(`--line2=${tc.line2}`);
  args.push("--", tc.text || clip.title);
  const r = await runCli(args);
  if (r.code !== 0) {
    res.status(400).json({ error: stripAnsi(r.stderr || r.stdout) || "thumbnail failed" });
    return;
  }
  const jsonLine = r.stdout.trim().split("\n").reverse().find((l) => l.trim().startsWith("{"));
  let variations: string[] = [];
  try { variations = JSON.parse(jsonLine || "{}").paths || []; } catch { /* no paths */ }
  if (variations.length === 0) {
    res.status(500).json({ error: "no thumbnails generated" });
    return;
  }
  // Bake the chosen thumbnail into the clip as the opening card (stripping any prior card).
  if (existsSync(clip.output_path)) {
    const bake = await bakeThumbnailCard(clip.output_path, variations[0], clip.thumbnail_config?.card_seconds || 0);
    if (!bake.ok) {
      res.status(500).json({ error: `thumbnail generated but bake into clip failed: ${bake.error}` });
      return;
    }
  }
  const merged = { ...tc, preview_path: variations[0], variations, card_seconds: 1.5 };
  await runCli(["clips", "edit", String(clip.id), "--thumbnail-config", JSON.stringify(merged)]);
  res.json({ ok: true, preview_path: variations[0], variations });
});

app.post("/api/clips/:id/thumbnail/select", async (req, res) => {
  const clip = await clipsHistory.findById(req.params.id);
  if (!clip) { res.status(404).json({ error: "clip not found" }); return; }
  const tc = clip.thumbnail_config || {};
  const pick = String(req.body?.path || "");
  if (!pick || !(tc.variations || []).includes(pick) || !existsSync(pick)) {
    res.status(400).json({ error: "unknown variation" });
    return;
  }
  if (existsSync(clip.output_path)) {
    const bake = await bakeThumbnailCard(clip.output_path, pick, tc.card_seconds || 0);
    if (!bake.ok) {
      res.status(500).json({ error: `bake into clip failed: ${bake.error}` });
      return;
    }
  }
  await runCli(["clips", "edit", String(clip.id), "--thumbnail-config", JSON.stringify({ ...tc, preview_path: pick, card_seconds: 1.5 })]);
  res.json({ ok: true, preview_path: pick });
});

// Candidate headline texts + face frames for the two-step thumbnail picker.
app.get("/api/clips/:id/thumbnail/options", async (req, res) => {
  const clip = await clipsHistory.findById(req.params.id);
  if (!clip) { res.status(404).json({ error: "clip not found" }); return; }
  const tc = clip.thumbnail_config || {};
  const clamp = (v: any, d: number) => Math.min(Math.max(parseInt(String(v)) || d, 1), 8);
  const outDir = join(paths.output, "thumbnails", String(clip.id), "frames");
  const r = await runCli([
    "thumbnail-options",
    "--output", outDir,
    "--video", clip.source_video,
    "--start", String(clip.start_second),
    "--end", String(clip.end_second),
    "--texts", String(clamp(req.query.texts, 6)),
    "--frames", String(clamp(req.query.frames, 6)),
    "--", tc.text || clip.title,
  ]);
  if (r.code !== 0) { res.status(400).json({ error: stripAnsi(r.stderr || r.stdout) || "options failed" }); return; }
  const jsonLine = r.stdout.trim().split("\n").reverse().find((l) => l.trim().startsWith("{"));
  try {
    const parsed = JSON.parse(jsonLine || "{}");
    if (!parsed.frames?.length && (parsed.texts?.length || 0) > 0) {
      parsed.warning = "No suitable frames found in this clip — try another moment or upload a frame.";
    }
    res.json(parsed);
  } catch { res.status(500).json({ error: "bad options output" }); }
});

// Render one final thumbnail from a chosen frame + headline (empty lines = AI writes the text).
app.post("/api/clips/:id/thumbnail/render", async (req, res) => {
  const clip = await clipsHistory.findById(req.params.id);
  if (!clip) { res.status(404).json({ error: "clip not found" }); return; }
  const tc = clip.thumbnail_config || {};
  const { line1, line2, frame_path, frame_info } = req.body || {};
  if (!frame_path) { res.status(400).json({ error: "select a frame first" }); return; }
  // Only allow frames podcli itself produced (candidate frames) or the user uploaded —
  // never an arbitrary server path passed through to the renderer.
  const resolvedFrame = resolveFrameInRoots(frame_path, [join(paths.output, "thumbnails", String(clip.id)), uploadDir]);
  if (!resolvedFrame) { res.status(400).json({ error: "invalid frame" }); return; }
  const outDir = join(paths.output, "thumbnails", String(clip.id));
  await mkdir(outDir, { recursive: true });
  const out = join(outDir, `thumb_${uuidv4().slice(0, 8)}.png`);
  const args = ["thumbnail-render", "--frame", resolvedFrame, "--output", out];
  if (line1) args.push(`--line1=${line1}`);
  if (line2) args.push(`--line2=${line2}`);
  if (frame_info) args.push("--frame-info", JSON.stringify(frame_info));
  args.push("--", tc.text || clip.title);
  const r = await runCli(args);
  if (r.code !== 0) { res.status(400).json({ error: stripAnsi(r.stderr || r.stdout) || "render failed" }); return; }
  const jsonLine = r.stdout.trim().split("\n").reverse().find((l) => l.trim().startsWith("{"));
  let outPath = "";
  try { outPath = JSON.parse(jsonLine || "{}").path || ""; } catch { /* no path */ }
  if (!outPath || !existsSync(outPath)) { res.status(500).json({ error: "no thumbnail produced" }); return; }
  if (clip.output_path && existsSync(clip.output_path)) {
    const bake = await bakeThumbnailCard(clip.output_path, outPath, tc.card_seconds || 0);
    if (!bake.ok) { res.status(500).json({ error: `rendered but bake into clip failed: ${bake.error}` }); return; }
  }
  const merged = { ...tc, line1: line1 || undefined, line2: line2 || undefined, preview_path: outPath, card_seconds: 1.5 };
  const edit = await runCli(["clips", "edit", String(clip.id), "--thumbnail-config", JSON.stringify(merged)]);
  if (edit.code !== 0) { res.status(500).json({ error: stripAnsi(edit.stderr || edit.stdout) || "thumbnail metadata update failed" }); return; }
  res.json({ ok: true, preview_path: outPath });
});

// --- Thumbnail studio (standalone thumbnails, no clip required) ---

const thumbStudioDir = join(paths.output, "thumbnails", "studio");

// realpath both sides so a symlink planted inside an allowed root can't point
// the renderer at a file outside it.
function resolveFrameInRoots(framePath: unknown, roots: string[]): string | null {
  let real: string;
  try {
    real = realpathSync(path.resolve(String(framePath)));
  } catch {
    return null;
  }
  const realRoot = (p: string) => {
    try { return realpathSync(p); } catch { return resolve(p); }
  };
  const ok = roots.map(realRoot).some((root) => real === root || real.startsWith(root + path.sep));
  return ok ? real : null;
}

app.post("/api/thumbnail-studio/options", async (req, res) => {
  const { title, video_path, start, end, texts, frames } = req.body || {};
  if (!title || !String(title).trim()) { res.status(400).json({ error: "title is required" }); return; }
  const clamp = (v: any, d: number) => Math.min(Math.max(parseInt(String(v)) || d, 1), 8);
  const args = [
    "thumbnail-options",
    "--output", join(thumbStudioDir, "frames"),
    "--texts", String(clamp(texts, 6)),
    "--frames", String(clamp(frames, 6)),
  ];
  if (video_path) {
    // Frames come only from files the user explicitly uploaded/selected this
    // session — never an arbitrary server path passed to the extractor.
    let video: string;
    try { video = realpathSync(path.resolve(String(video_path))); } catch { res.status(400).json({ error: "video not found" }); return; }
    if (!allowedSourcePaths.has(video)) { res.status(400).json({ error: "unknown video, upload or select it first" }); return; }
    args.push("--video", video);
    if (start != null && start !== "") args.push("--start", String(Math.max(0, Number(start) || 0)));
    if (end != null && end !== "") args.push("--end", String(Math.max(0, Number(end) || 0)));
  }
  args.push("--", String(title));
  const r = await runCli(args);
  if (r.code !== 0) { res.status(400).json({ error: stripAnsi(r.stderr || r.stdout) || "options failed" }); return; }
  const jsonLine = r.stdout.trim().split("\n").reverse().find((l) => l.trim().startsWith("{"));
  try {
    const parsed = JSON.parse(jsonLine || "{}");
    if (!parsed.frames?.length && (parsed.texts?.length || 0) > 0) {
      parsed.warning = "No suitable frames found — try another moment or upload a frame.";
    }
    res.json(parsed);
  } catch { res.status(500).json({ error: "bad options output" }); }
});

app.post("/api/thumbnail-studio/render", async (req, res) => {
  const { title, line1, line2, frame_path, frame_info } = req.body || {};
  if (!title || !String(title).trim()) { res.status(400).json({ error: "title is required" }); return; }
  if (!frame_path) { res.status(400).json({ error: "select a frame first" }); return; }
  const resolvedFrame = resolveFrameInRoots(frame_path, [thumbStudioDir, uploadDir]);
  if (!resolvedFrame) { res.status(400).json({ error: "invalid frame" }); return; }
  await mkdir(thumbStudioDir, { recursive: true });
  const out = join(thumbStudioDir, `thumb_${uuidv4().slice(0, 8)}.png`);
  const args = ["thumbnail-render", "--frame", resolvedFrame, "--output", out];
  if (line1) args.push(`--line1=${line1}`);
  if (line2) args.push(`--line2=${line2}`);
  if (frame_info) args.push("--frame-info", JSON.stringify(frame_info));
  args.push("--", String(title));
  const r = await runCli(args);
  if (r.code !== 0) { res.status(400).json({ error: stripAnsi(r.stderr || r.stdout) || "render failed" }); return; }
  const jsonLine = r.stdout.trim().split("\n").reverse().find((l) => l.trim().startsWith("{"));
  let outPath = "";
  try { outPath = JSON.parse(jsonLine || "{}").path || ""; } catch { /* no path */ }
  if (!outPath || !existsSync(outPath)) { res.status(500).json({ error: "no thumbnail produced" }); return; }
  res.json({ ok: true, path: outPath });
});

// --- Secrets/settings stored in the global .env (e.g. HF_TOKEN) ---

app.get("/api/settings", async (_req, res) => {
  try {
    const result = await executor.execute<{ settings?: unknown[] }>("manage_env", { action: "list" });
    res.json(result.data ?? { settings: [] });
  } catch (err: unknown) {
    res.status(500).json({ error: errMsg(err) });
  }
});

app.post("/api/settings", async (req, res) => {
  const key = typeof req.body?.key === "string" ? req.body.key : "";
  const value = typeof req.body?.value === "string" ? req.body.value : "";
  if (!key) {
    res.status(400).json({ error: "key is required" });
    return;
  }
  try {
    const action = value.trim() ? "set" : "unset";
    const result = await executor.execute("manage_env", { action, key, value });
    res.json(result.data ?? { ok: true });
  } catch (err: unknown) {
    res.status(500).json({ error: errMsg(err) });
  }
});

app.get("/api/ai-cli-status", async (_req, res) => {
  try {
    const result = await executor.execute<{
      configured?: Record<string, string | null>;
      candidates?: Array<{ engine: string; path: string }>;
      available?: boolean;
    }>("ai_cli_status", {});
    res.json(result.data ?? { available: false, candidates: [], configured: {} });
  } catch (err: unknown) {
    res.status(500).json({ error: errMsg(err) });
  }
});

app.get("/api/youtube/config", (_req, res) => {
  try {
    const all = JSON.parse(readFileSync(paths.integrations, "utf-8"));
    const yt = all.youtube || {};
    res.json({ client_id: yt.client_id || "", has_secret: !!yt.client_secret });
  } catch {
    res.json({ client_id: "", has_secret: false });
  }
});

app.put("/api/youtube/config", (req, res) => {
  try {
    let all: Record<string, any> = {};
    try { all = JSON.parse(readFileSync(paths.integrations, "utf-8")); } catch { /* new */ }
    const yt = { ...(all.youtube || {}) };
    const { client_id, client_secret } = req.body || {};
    if (client_id !== undefined) yt.client_id = client_id;
    if (client_secret) yt.client_secret = client_secret;
    all.youtube = yt;
    writeFileAtomicSync(paths.integrations, JSON.stringify(all, null, 2) + "\n");
    // Holds the OAuth client secret — keep it owner-only (chmod covers the
    // case where the file already existed with looser perms).
    try { chmodSync(paths.integrations, 0o600); } catch { /* best effort */ }
    res.json({ ok: true });
  } catch (e: any) {
    res.status(500).json({ error: e.message });
  }
});

app.get("/api/youtube/status", async (_req, res) => {
  try {
    const clips = await clipsHistory.load();
    res.json({
      authorized: DEMO || existsSync(join(paths.home, "youtube-token.json")),
      linked: clips.filter((c) => c.youtube_video_id).length,
      with_metrics: clips.filter((c) => c.metrics && (c.metrics.views != null || c.metrics.retention != null)).length,
      total: clips.length,
    });
  } catch {
    res.json({ authorized: false, linked: 0, with_metrics: 0, total: 0 });
  }
});

app.post("/api/youtube/sync", async (req, res) => {
  const csvPath = req.body?.csv_path;
  const r = await runCli(["youtube", "sync", ...(csvPath ? ["--csv", String(csvPath)] : [])]);
  if (r.code !== 0) {
    res.status(400).json({ error: stripAnsi(r.stderr || r.stdout) || "sync failed" });
    return;
  }
  res.json({ ok: true, message: stripAnsi(r.stdout) });
});

app.post("/api/youtube/learn", async (_req, res) => {
  const r = await runCli(["youtube", "learn"]);
  if (r.code !== 0) {
    res.status(400).json({ error: stripAnsi(r.stderr || r.stdout) || "analysis failed" });
    return;
  }
  res.json({ ok: true, message: stripAnsi(r.stdout) });
});

// Proposed clip↔video links for the authorized channel (live OAuth path).
app.get("/api/youtube/links", async (_req, res) => {
  const r = await runCli(["youtube", "link", "--json"]);
  let payload: any = {};
  try { payload = JSON.parse(r.stdout.trim().split("\n").pop() || "{}"); } catch { /* non-JSON */ }
  if (r.code !== 0 || payload.error) {
    res.status(400).json({ error: payload.error || stripAnsi(r.stderr || r.stdout) || "could not load proposals" });
    return;
  }
  res.json({ proposals: payload.proposals || [] });
});

app.post("/api/youtube/link", async (req, res) => {
  const { clip_id, video_id } = req.body || {};
  if (!clip_id || !video_id) {
    res.status(400).json({ error: "clip_id and video_id are required" });
    return;
  }
  const r = await runCli(["youtube", "link", String(clip_id), String(video_id), "--json"]);
  let payload: any = {};
  try { payload = JSON.parse(r.stdout.trim().split("\n").pop() || "{}"); } catch { /* non-JSON */ }
  if (r.code !== 0 || !payload.ok) {
    res.status(400).json({ error: payload.error || "link failed (clip not found?)" });
    return;
  }
  res.json({ ok: true });
});

app.get("/api/analytics", async (_req, res) => {
  const clips = await clipsHistory.load();
  const withM = clips.filter((c) => c.metrics && (c.metrics.views != null || c.metrics.retention != null));
  const avg = (arr: number[]) => (arr.length ? arr.reduce((a, b) => a + b, 0) / arr.length : 0);
  const agg = (keyFn: (c: any) => string) => {
    const groups = new Map<string, any[]>();
    for (const c of withM) {
      const k = keyFn(c) || "—";
      (groups.get(k) ?? groups.set(k, []).get(k)!).push(c);
    }
    return Array.from(groups.entries()).map(([key, cs]) => ({
      key,
      count: cs.length,
      avgViews: Math.round(avg(cs.map((c) => c.metrics?.views || 0))),
      avgRetention: +avg(cs.map((c) => c.metrics?.retention || 0)).toFixed(1),
      avgCtr: +avg(cs.map((c) => c.metrics?.ctr || 0)).toFixed(1),
    })).sort((a, b) => b.avgViews - a.avgViews);
  };
  const lengthBucket = (d: number) => (d < 25 ? "<25s" : d < 35 ? "25–35s" : d < 45 ? "35–45s" : "45s+");
  res.json({
    published: withM.length,
    total: clips.length,
    byContentType: agg((c) => c.content_type),
    byCaptionStyle: agg((c) => c.caption_style),
    byLength: agg((c) => lengthBucket(c.duration || 0)),
    top: withM
      .slice()
      .sort((a, b) => (b.metrics?.views || 0) - (a.metrics?.views || 0))
      .slice(0, 12)
      .map((c) => ({ id: c.id, title: c.title, content_type: c.content_type, caption_style: c.caption_style, duration: c.duration, metrics: c.metrics })),
  });
});

app.get("/api/clips/:id/source", async (req, res) => {
  const clip = await clipsHistory.findById(req.params.id);
  if (!clip || !existsSync(clip.source_video)) {
    res.status(404).json({ error: "source not found" });
    return;
  }
  const srcMime: Record<string, string> = { ".mp4": "video/mp4", ".webm": "video/webm", ".mov": "video/quicktime", ".mkv": "video/x-matroska", ".m4v": "video/mp4" };
  streamVideo(req, res, clip.source_video, srcMime[extname(clip.source_video).toLowerCase()] || "video/mp4");
});

app.get("/api/clips/:id/reframe", async (req, res) => {
  const clip = await clipsHistory.findById(req.params.id);
  if (!clip) { res.status(404).json({ error: "clip not found" }); return; }
  res.json((await clipsHistory.loadReframe(clip.id)) || {});
});

app.get("/api/clips/:id/cuts", async (req, res) => {
  const clip = await clipsHistory.findById(req.params.id);
  if (!clip || !existsSync(clip.source_video)) { res.status(404).json({ error: "source not found" }); return; }
  const start = clip.start_second;
  const dur = Math.max(0.1, clip.end_second - clip.start_second);
  // pts_time from ffmpeg scene detection is relative to -ss, not the source.
  const proc = spawn(paths.ffmpegPath, [
    "-ss", String(start), "-i", clip.source_video, "-t", String(dur),
    "-vf", "select='gt(scene,0.3)',showinfo", "-an", "-f", "null", "-",
  ]);
  let stderr = "";
  proc.stderr.on("data", (d) => (stderr += d));
  proc.on("close", () => {
    const cuts = [...stderr.matchAll(/pts_time:([\d.]+)/g)]
      .map((m) => +(start + parseFloat(m[1])).toFixed(3))
      .filter((t) => t > start + 0.2 && t < start + dur - 0.2);
    res.json({ cuts: Array.from(new Set(cuts)) });
  });
  proc.on("error", () => res.json({ cuts: [] }));
});

app.post("/api/clips/:id/rerender", async (req, res) => {
  const clip = await clipsHistory.findById(req.params.id);
  if (!clip) {
    res.status(404).json({ error: "clip not found" });
    return;
  }
  // Editor sends source-absolute keyframes + trim; derive the render's clip-relative
  // crop keyframes from it, and persist the editor state so reopening shows it.
  const reframe = req.body?.reframe as { keyframes?: { tAbs: number; x_pct: number }[]; inSec?: number; outSec?: number } | undefined;
  const startSecond = reframe && typeof reframe.inSec === "number" ? reframe.inSec
    : typeof req.body?.start_second === "number" ? req.body.start_second : clip.start_second;
  const endSecond = reframe && typeof reframe.outSec === "number" ? reframe.outSec
    : typeof req.body?.end_second === "number" ? req.body.end_second : clip.end_second;
  const keyframes = reframe?.keyframes
    ? reframe.keyframes
        .filter((k) => k.tAbs >= startSecond - 0.001 && k.tAbs <= endSecond + 0.001)
        .map((k) => ({ t: +Math.max(0, k.tAbs - startSecond).toFixed(3), x_pct: k.x_pct }))
    : req.body?.crop_keyframes;
  if (!Array.isArray(keyframes) || keyframes.length === 0) {
    res.status(400).json({ error: "keyframes required" });
    return;
  }
  if (reframe) await clipsHistory.saveReframe(clip.id, reframe);
  // Replay the original render recipe (logo/outro/captions/fillers) with the new
  // manual crop, so brand elements survive the reframe.
  const recipe = (await clipsHistory.loadRecipe(clip.id)) || {};
  let allWords = (recipe.transcript_words as any[]) || (await clipsHistory.loadWords(clip.id)) || [];
  if (!allWords.length) {
    // Fallback for clips rendered before recipes existed: recover words from the cached source transcript.
    try {
      const t = await cache.get(clip.source_video);
      if (t?.words?.length) allWords = t.words as any[];
    } catch { /* no cached transcript */ }
  }
  // If trimmed wider/narrower, keep only words inside the new bounds.
  const words = allWords.filter((w: any) => typeof w?.start !== "number" || (w.start >= startSecond && w.start < endSecond));
  try {
    const result = await executor.execute<ClipResult>("create_clip", {
      video_path: clip.source_video,
      start_second: startSecond,
      end_second: endSecond,
      caption_style: req.body?.caption_style || (recipe.caption_style as string) || clip.caption_style,
      crop_strategy: "manual",
      crop_keyframes: keyframes,
      transcript_words: words,
      logo_path: (recipe.logo_path as string) ?? clip.logo_path ?? null,
      outro_path: (recipe.outro_path as string) ?? clip.outro_path ?? null,
      // Honor an explicit null in the recipe (intro removed), not the stale clip value.
      intro_path: "intro_path" in recipe ? (recipe.intro_path ?? null) : (clip.intro_path ?? null),
      clean_fillers: recipe.clean_fillers !== undefined ? recipe.clean_fillers : true,
      ...(recipe.keep_segments ? { keep_segments: recipe.keep_segments } : {}),
      title: clip.title,
      output_dir: dirname(clip.output_path),
    });
    if (!result.data) throw new Error("no render output");
    const outPath = result.data.output_path || clip.output_path;
    // Re-bake the chosen thumbnail card onto the fresh clip (no strip needed —
    // create_clip always renders a cardless clip).
    const tnail = clip.thumbnail_config?.preview_path;
    let thumbnailBaked = false;
    if (tnail && existsSync(tnail) && existsSync(outPath)) {
      const bake = await bakeThumbnailCard(outPath, tnail);
      thumbnailBaked = bake.ok;
      if (!bake.ok) {
        res.status(500).json({ error: `reframe rendered but thumbnail bake failed: ${bake.error}` });
        return;
      }
    }
    await clipsHistory.update(clip.id, {
      start_second: startSecond,
      end_second: endSecond,
      crop_strategy: "manual",
      duration: result.data.duration ?? clip.duration,
      file_size_mb: result.data.file_size_mb ?? clip.file_size_mb,
      output_path: outPath,
    });
    res.json({ ok: true, output_path: outPath, file_size_mb: result.data.file_size_mb, thumbnail_baked: thumbnailBaked });
  } catch (e: any) {
    res.status(500).json({ error: e.message });
  }
});

app.post("/api/clips/:id/davinci", async (req, res) => {
  const clip = await clipsHistory.findById(req.params.id);
  if (!clip) {
    res.status(404).json({ error: "clip not found" });
    return;
  }
  if (!existsSync(clip.output_path)) {
    res.status(400).json({ error: "rendered file missing" });
    return;
  }
  const cli = join(paths.backendDir, "services", "integrations", "davinci_resolve", "cli.py");
  const r = await runPy([cli, "--source", clip.output_path, `--title=${clip.title}`]);
  if (r.code !== 0) {
    res.status(400).json({ error: stripAnsi(r.stderr || r.stdout) || "export failed" });
    return;
  }
  const wrote = (r.stdout.match(/wrote:\s*(.+)/) || [])[1]?.trim();
  res.json({ ok: true, path: wrote });
});

app.get("/api/history/check", async (req, res) => {
  try {
    const {
      source,
      start,
      end,
      style = "hormozi",
      crop = "speaker",
    } = req.query;
    if (!source || !start || !end) {
      res.json({ duplicate: null });
      return;
    }
    const dup = await clipsHistory.findDuplicate(
      source as string,
      parseFloat(start as string),
      parseFloat(end as string),
      style as string,
      crop as string,
    );
    res.json({ duplicate: dup });
  } catch (err: any) {
    res.json({ duplicate: null });
  }
});

// --- Transcript Corrections ---

const correctionsPath = paths.corrections;

app.get("/api/corrections", (_req, res) => {
  try {
    if (existsSync(correctionsPath)) {
      res.json(JSON.parse(readFileSync(correctionsPath, "utf-8")));
    } else {
      res.json({});
    }
  } catch {
    res.json({});
  }
});

app.put("/api/corrections", express.json(), (req, res) => {
  try {
    const corrections = req.body || {};
    writeFileAtomicSync(correctionsPath, JSON.stringify(corrections, null, 2));
    res.json({ ok: true, corrections });
  } catch (err: any) {
    res.status(500).json({ error: err.message });
  }
});

app.post("/api/corrections/add", express.json(), (req, res) => {
  const { wrong, correct } = req.body;
  if (!wrong || !correct) {
    res.status(400).json({ error: "'wrong' and 'correct' are required" });
    return;
  }
  try {
    let corrections: Record<string, string> = {};
    if (existsSync(correctionsPath)) {
      corrections = JSON.parse(readFileSync(correctionsPath, "utf-8"));
    }
    corrections[wrong] = correct;
    writeFileAtomicSync(correctionsPath, JSON.stringify(corrections, null, 2));
    res.json({ ok: true, corrections });
  } catch (err: any) {
    res.status(500).json({ error: err.message });
  }
});

app.delete("/api/corrections/:wrong", (req, res) => {
  try {
    let corrections: Record<string, string> = {};
    if (existsSync(correctionsPath)) {
      corrections = JSON.parse(readFileSync(correctionsPath, "utf-8"));
    }
    delete corrections[req.params.wrong];
    writeFileAtomicSync(correctionsPath, JSON.stringify(corrections, null, 2));
    res.json({ ok: true, corrections });
  } catch (err: any) {
    res.status(500).json({ error: err.message });
  }
});

// --- Knowledge Base ---
app.get("/api/knowledge", async (_req, res) => {
  try {
    const files = await knowledgeBase.listFiles();
    res.json(files);
  } catch (err: any) {
    res.json([]);
  }
});

app.get("/api/knowledge/dir", (_req, res) => {
  res.json({ path: paths.knowledge });
});

// Must stay ahead of the :filename routes, which would otherwise claim "init".
app.post("/api/knowledge/init", async (_req, res) => {
  try {
    res.json(await knowledgeBase.initFromTemplates());
  } catch (err: unknown) {
    res.status(500).json({ error: errMsg(err) });
  }
});

// Knowledge file upload (drag & drop .md files) — must be before :filename routes
const knowledgeUpload = multer({
  storage: multer.diskStorage({
    destination: async (_req, _file, cb) => {
      await mkdir(paths.knowledge, { recursive: true });
      cb(null, paths.knowledge);
    },
    filename: (_req, file, cb) => cb(null, basename(file.originalname)),
  }),
  fileFilter: (_req, file, cb) => {
    const name = basename(file.originalname);
    if (name.endsWith(".md") || name.endsWith(".txt")) {
      cb(null, true);
    } else {
      cb(new Error("Only .md and .txt files are allowed"));
    }
  },
});

app.post(
  "/api/knowledge/upload",
  knowledgeUpload.array("files", 50),
  (req, res) => {
    const files = req.files as Express.Multer.File[];
    if (!files || files.length === 0) {
      res.status(400).json({ error: "No files uploaded" });
      return;
    }
    res.json({ uploaded: files.map((f) => f.originalname) });
  },
);

app.get("/api/knowledge/:filename", async (req, res) => {
  if (!safePath(paths.knowledge, req.params.filename)) {
    res.status(400).json({ error: "Invalid filename" });
    return;
  }
  try {
    const content = await knowledgeBase.readFile(req.params.filename);
    res.json({ filename: req.params.filename, content });
  } catch (err: any) {
    res.status(404).json({ error: err.message });
  }
});

app.post("/api/knowledge/:filename", async (req, res) => {
  if (!safePath(paths.knowledge, req.params.filename)) {
    res.status(400).json({ error: "Invalid filename" });
    return;
  }
  try {
    await knowledgeBase.writeFile(req.params.filename, req.body.content);
    res.json({ ok: true, filename: req.params.filename });
  } catch (err: any) {
    res.status(400).json({ error: err.message });
  }
});

app.delete("/api/knowledge/:filename", async (req, res) => {
  if (!safePath(paths.knowledge, req.params.filename)) {
    res.status(400).json({ error: "Invalid filename" });
    return;
  }
  try {
    await knowledgeBase.deleteFile(req.params.filename);
    res.json({ ok: true });
  } catch (err: any) {
    res.status(500).json({ error: err.message });
  }
});

// --- Onboarding readiness ---
app.get("/api/onboarding", async (_req, res) => {
  const knowledge = await knowledgeBase
    .status()
    .catch(() => ({ templates: [], present: [], filled: [], missing: [] }));
  const assets = await assetManager.list().catch(() => []);
  const clips = await clipsHistory.load().catch(() => []);

  let aiCli = false;
  try {
    const result = await executor.execute<{ available?: boolean }>("ai_cli_status", {});
    aiCli = !!result.data?.available;
  } catch {
    // A failed probe reads the same as no CLI installed.
  }

  res.json({
    knowledge: {
      total: knowledge.templates.length,
      present: knowledge.present.length,
      filled: knowledge.filled,
      missing: knowledge.missing,
    },
    assets: {
      count: assets.length,
      branding: assets.some((a) => a.type === "logo" || a.type === "outro"),
    },
    aiCli: { available: aiCli },
    clips: { count: clips.length },
    dismissed: !!uiState.settings.onboardingDismissed,
  });
});

// --- Prompt Builder (shared by /api/generate-prompt and /api/mcp-hints) ---

function getStateContext() {
  const hasVideo = !!uiState.videoPath;
  const hasTranscript = (uiState.transcript?.words?.length ?? 0) > 0;
  const hasRawTranscript = !!uiState.rawTranscriptText?.trim();
  const hasSuggestions = uiState.suggestions.length > 0;
  const selectedCount =
    uiState.suggestions.length - uiState.deselectedIndices.length;
  return {
    hasVideo,
    hasTranscript,
    hasRawTranscript,
    hasSuggestions,
    selectedCount,
    phase: uiState.phase,
  };
}

function buildPromptForAction(
  action: "suggest" | "export" | "restyle",
): string {
  const ctx = getStateContext();
  const parts: string[] = [];

  const settingsSummary = [
    uiState.videoPath ? `Video: ${uiState.videoPath.split(/[/\\]/).pop()}` : null,
    `Style: ${uiState.settings.captionStyle}`,
    `Crop: ${uiState.settings.cropStrategy}`,
    uiState.settings.logoPath ? `Logo: set` : null,
    uiState.settings.outroPath ? `Outro: set` : null,
  ]
    .filter(Boolean)
    .join(", ");

  if (action === "suggest") {
    if (!ctx.hasTranscript && !ctx.hasRawTranscript) {
      // No transcript yet — prompt includes transcribe step
      if (uiState.videoPath) {
        parts.push(
          `Transcribe the podcast at ${uiState.videoPath} using transcribe_podcast.`,
        );
      } else {
        parts.push(
          "Set a video path first, then transcribe it using transcribe_podcast.",
        );
      }
      parts.push(
        "Then find the 5-8 best viral-worthy moments and call suggest_clips.",
      );
    } else {
      parts.push(
        "Use get_ui_state with include_transcript=true to read the full transcript.",
      );
      parts.push(
        "Find the 5-8 best viral-worthy moments — hot takes, strong opinions, funny moments, actionable advice, and emotional stories.",
      );
      parts.push("Then call suggest_clips with your suggestions.");
    }
    parts.push(`Current settings: ${settingsSummary}`);
  } else if (action === "export") {
    parts.push("Use get_ui_state to read the selected clips.");
    parts.push(
      `Export all ${ctx.selectedCount} selected clip${ctx.selectedCount !== 1 ? "s" : ""} using batch_create_clips.`,
    );
    parts.push(`Current settings: ${settingsSummary}`);
  } else if (action === "restyle") {
    parts.push("Use get_ui_state to read the current clips.");
    parts.push("Re-export all clips with the updated style settings.");
    parts.push(`Current settings: ${settingsSummary}`);
  }

  return parts.join("\n");
}

// --- MCP Prompt Hints ---

/**
 * GET /api/mcp-hints — Returns contextual MCP prompt suggestions based on current state.
 * Prompts are practical clip-generation actions, not generic help.
 * Phrased generically (not Claude-specific) since any MCP client can use them.
 */
app.get("/api/mcp-hints", (_req, res) => {
  const ctx = getStateContext();

  interface Hint {
    prompt: string;
    description: string;
    category: "analyze" | "create" | "refine" | "export";
  }

  const hints: Hint[] = [];

  // The UI handles video upload and transcript input directly.
  // MCP hints only appear once there's something to work with.

  if ((ctx.hasRawTranscript || ctx.hasTranscript) && !ctx.hasSuggestions) {
    // Transcript is ready — suggest clip-finding prompts
    hints.push(
      {
        prompt: "Find the 5 best viral moments from this podcast",
        description: "Clips optimized for TikTok/Shorts",
        category: "analyze",
      },
      {
        prompt: "Find moments with hot takes and strong opinions",
        description: "Controversial, high-engagement clips",
        category: "analyze",
      },
      {
        prompt: "Find funny moments and quotable one-liners",
        description: "Entertainment-focused clips",
        category: "analyze",
      },
      {
        prompt: "Find actionable advice and key insights",
        description: "Value-driven educational clips",
        category: "analyze",
      },
    );
  } else if (
    ctx.phase === "review" &&
    ctx.hasSuggestions &&
    ctx.selectedCount > 0
  ) {
    // Clips suggested, ready for action
    hints.push(
      {
        prompt: "Export all selected clips",
        description: `Render ${ctx.selectedCount} clip${ctx.selectedCount !== 1 ? "s" : ""} as vertical shorts`,
        category: "export",
      },
      {
        prompt: "Export clip #1",
        description: "Render just the first clip",
        category: "export",
      },
      {
        prompt: "Change all clips to hormozi style",
        description: "Bold uppercase, yellow highlight",
        category: "refine",
      },
      {
        prompt: "Extend clip #1 by 10 seconds",
        description: "Adjust timing before export",
        category: "refine",
      },
      {
        prompt: "Find 5 more moments",
        description: "Additional clips from the transcript",
        category: "analyze",
      },
    );
  } else if (ctx.phase === "done") {
    hints.push(
      {
        prompt: "Find more viral moments from the transcript",
        description: "Get another batch of clips",
        category: "analyze",
      },
      {
        prompt: "Re-export all clips with karaoke style",
        description: "Try a different caption look",
        category: "refine",
      },
      {
        prompt: "Save these settings as a preset called 'myshow'",
        description: "Reuse this config next time",
        category: "refine",
      },
    );
  }

  res.json({
    hints,
    phase: ctx.phase,
    hasVideo: ctx.hasVideo,
    hasTranscript: ctx.hasTranscript,
    hasSuggestions: ctx.hasSuggestions,
    selectedCount: ctx.selectedCount,
  });
});

/**
 * POST /api/generate-prompt — Build an MCP prompt for the given action using authoritative server state.
 * Body: { action: "suggest" | "export" | "restyle" }
 * Returns: { prompt, action, context }
 */
app.post("/api/generate-prompt", (req, res) => {
  const action = req.body.action || "suggest";
  if (!["suggest", "export", "restyle"].includes(action)) {
    res
      .status(400)
      .json({ error: "action must be 'suggest', 'export', or 'restyle'" });
    return;
  }
  const prompt = buildPromptForAction(action);
  const ctx = getStateContext();
  res.json({ prompt, action, context: ctx });
});

// --- AI-powered clip suggestion (delegates to Python backend) ---

app.post("/api/claude-suggest", async (req, res) => {
  const { top_n = 5, min_duration, max_duration } = req.body;

  // Need transcript in state
  if (!uiState.transcript && !uiState.rawTranscriptText) {
    res
      .status(400)
      .json({ error: "No transcript loaded. Transcribe or import one first." });
    return;
  }

  const segs = uiState.transcript?.segments;
  if (!segs || !Array.isArray(segs) || segs.length === 0) {
    res.status(400).json({ error: "No transcript segments available." });
    return;
  }

  try {
    // Feed already-known moments to the AI so it doesn't re-suggest the same
    // ranges: clips already rendered for this video plus current suggestions.
    const rendered = uiState.videoPath
      ? await clipsHistory.getBySource(uiState.videoPath).catch(() => [])
      : [];
    const existing_clips = [
      ...rendered.map((c) => ({ start_second: c.start_second, end_second: c.end_second, title: c.title })),
      ...uiState.suggestions.map((s) => ({ start_second: s.start_second, end_second: s.end_second, title: s.title })),
    ];

    // The backend derives laughter/reaction anchors from the audio, so it needs
    // the source video, not just the segments.
    const params: Record<string, unknown> = { segments: segs, top_n, existing_clips };
    const suggestVideo = uiState.filePath || uiState.videoPath;
    if (suggestVideo) params.video_path = suggestVideo;
    if (min_duration) params.min_duration = min_duration;
    if (max_duration) params.max_duration = max_duration;
    const result = await executor.execute<{ clips?: SuggestedClip[] }>(
      "suggest_clips",
      params,
      (event) =>
        broadcastSSE("job-update", {
          progress: event.percent,
          message: event.message,
        }),
    );

    const clips = result.data?.clips ?? [];

    // Auto-push to UI state as suggestions
    if (clips.length > 0) {
      uiState.suggestions = clips.map((c, i) => ({
        clip_id: `claude-${i}`,
        title: c.title,
        start_second: c.start_second,
        end_second: c.end_second,
        duration: c.duration ?? c.end_second - c.start_second,
        segments: c.segments,
        reasoning: c.reasoning ?? "",
        preview_text: c.preview_text ?? "",
        content_type: c.content_type,
        score: c.score,
        suggested_caption_style: c.suggested_caption_style || "hormozi",
      }));
      uiState.deselectedIndices = [];
      uiState.phase = "review";
      broadcastSSE("state-sync", uiState);
    } else {
      uiState.phase = "idle";
      broadcastSSE("state-sync", uiState);
    }

    res.json({ clips, source: "python" });
  } catch (err: unknown) {
    const msg = errMsg(err);
    uiState.phase = "idle";
    broadcastSSE("state-sync", uiState);
    res
      .status(500)
      .json({ error: `Suggestion failed: ${msg.substring(0, 200)}` });
  }
});

// --- Find user-pasted moments (paste a description/quotes, AI locates them) ---

app.post("/api/find-moment", async (req, res) => {
  const text = typeof req.body?.text === "string" ? req.body.text.trim() : "";
  if (!text) {
    res.status(400).json({ error: "Paste a moment or description to search for." });
    return;
  }
  const segs = uiState.transcript?.segments;
  if (!segs || !Array.isArray(segs) || segs.length === 0) {
    res
      .status(400)
      .json({ error: "No transcript loaded. Transcribe or import one first." });
    return;
  }

  try {
    const existing = uiState.suggestions.map((s) => ({
      start_second: s.start_second,
      end_second: s.end_second,
      title: s.title,
    }));
    const result = await executor.execute<{ clips?: SuggestedClip[] }>(
      "find_moment",
      { text, segments: segs, existing_clips: existing, max_results: 8 },
      (event) =>
        broadcastSSE("job-update", { progress: event.percent, message: event.message }),
    );

    const found = result.data?.clips ?? [];
    // Append to existing suggestions, skipping anything at a range we already have.
    const seen = new Set(
      uiState.suggestions.map(
        (s) => `${Math.round(s.start_second * 10)}-${Math.round(s.end_second * 10)}`,
      ),
    );
    const added: SuggestedClip[] = [];
    for (const c of found) {
      const key = `${Math.round(c.start_second * 10)}-${Math.round(c.end_second * 10)}`;
      if (seen.has(key)) continue;
      seen.add(key);
      added.push({
        clip_id: `manual-${Date.now()}-${added.length}`,
        title: c.title,
        start_second: c.start_second,
        end_second: c.end_second,
        duration: c.duration ?? c.end_second - c.start_second,
        segments: c.segments,
        reasoning: c.reasoning ?? "",
        preview_text: c.preview_text ?? "",
        content_type: c.content_type,
        score: c.score,
        suggested_caption_style: c.suggested_caption_style || "hormozi",
      });
    }

    if (added.length > 0) {
      uiState.suggestions = [...uiState.suggestions, ...added];
      uiState.phase = "review";
      uiState.lastUpdated = Date.now();
      persistState();
      broadcastSSE("state-sync", uiState);
    }

    res.json({ clips: added, found: found.length, added: added.length });
  } catch (err: unknown) {
    const msg = errMsg(err);
    res.status(500).json({ error: `Moment search failed: ${msg.substring(0, 200)}` });
  }
});

// --- Per-clip content generation (titles, descriptions, tags) ---

app.post("/api/generate-content", async (req, res) => {
  const { clip, transcript_segments } = req.body;

  if (!clip) {
    res.status(400).json({ error: "clip is required" });
    return;
  }

  // Use transcript segments from request or fall back to UI state
  const segs = transcript_segments || uiState.transcript?.segments || [];

  try {
    const result = await executor.execute(
      "generate_content",
      { clip, transcript_segments: segs },
      (event) => {
        if (event.partial) {
          broadcastSSE("content-partial", { stream_id: clip?.id ? String(clip.id) : null, partial: event.partial });
          return;
        }
        broadcastSSE("job-update", {
          progress: event.percent,
          message: event.message,
        });
      },
    );

    const data: any = result.data || {};
    // Persist onto the clip's history entry so the generated metadata survives a
    // reload — generation is expensive and was previously discarded after display.
    const clipId = clip?.id ? await clipsHistory.resolveId(String(clip.id)) : null;
    if (clipId) {
      const patch: Partial<ClipHistoryEntry> = {};
      if (Array.isArray(data.titles) && data.titles.length) patch.generated_titles = data.titles;
      if (data.description) patch.description = data.description;
      if (data.tags) patch.tags = data.tags;
      if (data.hashtags) patch.hashtags = data.hashtags;
      if (Object.keys(patch).length) await clipsHistory.update(clipId, patch);
    }

    res.json(data);
  } catch (err: any) {
    res.status(500).json({
      error: `Content generation failed: ${err.message?.substring(0, 200)}`,
    });
  }
});

// --- Content studio (transcript-first generation, no clip required) ---

const CONTENT_MAX_SEGMENTS = 30;

// Long transcripts get sampled evenly so titles reflect the whole episode,
// not just the opening minutes (the generator reads at most 30 segments).
function packTranscriptText(text: string): Array<{ start: number; text: string }> {
  const clean = text.replace(/\r/g, "").trim();
  const segChars = 1000;
  const stride = Math.max(segChars, Math.floor(clean.length / CONTENT_MAX_SEGMENTS));
  const segs: Array<{ start: number; text: string }> = [];
  for (let off = 0; off < clean.length && segs.length < CONTENT_MAX_SEGMENTS; off += stride) {
    const chunk = clean.slice(off, off + segChars).trim();
    if (chunk) segs.push({ start: segs.length, text: chunk });
  }
  return segs;
}

function condenseSegments(segments: Array<{ start?: number; text?: string }>): Array<{ start: number; text: string }> {
  const clean = segments
    .map((s) => ({ start: Number(s.start) || 0, text: String(s.text || "").trim() }))
    .filter((s) => s.text);
  if (clean.length <= CONTENT_MAX_SEGMENTS) return clean;
  const per = Math.ceil(clean.length / CONTENT_MAX_SEGMENTS);
  const out: Array<{ start: number; text: string }> = [];
  for (let i = 0; i < clean.length; i += per) {
    const group = clean.slice(i, i + per);
    out.push({ start: group[0].start, text: group.map((s) => s.text).join(" ").slice(0, 1200) });
  }
  return out;
}

app.post("/api/content-studio/generate", async (req, res) => {
  const { title, transcript_text, mode, stream_id } = req.body || {};
  let segs: Array<{ start: number; text: string }>;
  if (typeof transcript_text === "string" && transcript_text.trim()) {
    segs = packTranscriptText(transcript_text);
  } else if (uiState.transcript?.segments?.length) {
    segs = condenseSegments(uiState.transcript.segments);
  } else {
    res.status(400).json({ error: "paste a transcript or load an episode first" });
    return;
  }
  const episode = mode === "episode";
  const lastStart = segs.length ? segs[segs.length - 1].start : 0;
  const clip = {
    title: String(title || "").trim() || "Untitled episode",
    start_second: 0,
    end_second: lastStart + 60,
    content_type: episode ? "full episode" : "highlight",
  };
  try {
    const result = await executor.execute(
      "generate_content",
      { clip, transcript_segments: segs, mode: episode ? "episode" : "shorts" },
      (event) => {
        if (event.partial) {
          broadcastSSE("content-partial", { stream_id: stream_id || null, partial: event.partial });
          return;
        }
        broadcastSSE("job-update", {
          progress: event.percent,
          message: event.message,
        });
      },
    );
    res.json(result.data || {});
  } catch (err: any) {
    res.status(500).json({
      error: `Content generation failed: ${err.message?.substring(0, 200)}`,
    });
  }
});

/**
 * POST /api/content-studio/custom — free-form or per-section content request.
 */
app.post("/api/content-studio/custom", async (req, res) => {
  const { instruction, transcript_text, mode } = req.body || {};
  if (!instruction || !String(instruction).trim()) {
    res.status(400).json({ error: "instruction is required" });
    return;
  }
  let segs: Array<{ start: number; text: string }>;
  if (typeof transcript_text === "string" && transcript_text.trim()) {
    segs = packTranscriptText(transcript_text);
  } else if (uiState.transcript?.segments?.length) {
    segs = condenseSegments(uiState.transcript.segments);
  } else {
    res.status(400).json({ error: "paste a transcript or load an episode first" });
    return;
  }
  try {
    const result = await executor.execute(
      "generate_custom",
      {
        instruction: String(instruction),
        transcript_segments: segs,
        mode: mode === "episode" ? "episode" : "shorts",
      },
      (event) => {
        broadcastSSE("job-update", { progress: event.percent, message: event.message });
      },
    );
    res.json(result.data || {});
  } catch (err: any) {
    res.status(500).json({
      error: `Custom generation failed: ${err.message?.substring(0, 200)}`,
    });
  }
});

// --- MCP ↔ UI Bridge Endpoints ---

/**
 * GET /api/events — Global SSE channel for real-time MCP→UI events
 */
app.get("/api/events", (_req, res) => {
  res.writeHead(200, {
    "Content-Type": "text/event-stream",
    "Cache-Control": "no-cache",
    Connection: "keep-alive",
  });

  // Send current state on connect
  res.write(`event: state\ndata: ${JSON.stringify(uiState)}\n\n`);

  sseClients.push(res);

  // Heartbeat every 15s
  const heartbeat = setInterval(() => {
    try {
      res.write(`: heartbeat\n\n`);
    } catch {
      clearInterval(heartbeat);
    }
  }, 15_000);

  _req.on("close", () => {
    clearInterval(heartbeat);
    const idx = sseClients.indexOf(res);
    if (idx !== -1) sseClients.splice(idx, 1);
  });
});

/**
 * GET /api/ui-state — MCP reads current UI state
 */
app.get("/api/ui-state", (_req, res) => {
  const selected = uiState.suggestions.filter(
    (_: unknown, i: number) => !uiState.deselectedIndices.includes(i),
  );
  res.json({
    videoPath: uiState.videoPath,
    filePath: uiState.filePath,
    activeExportJobId: uiState.activeExportJobId,
    phase: uiState.phase,
    settings: uiState.settings,
    selectedClips: selected,
    suggestions: uiState.suggestions,
    deselectedIndices: uiState.deselectedIndices,
    totalSuggestions: uiState.suggestions.length,
    deselectedCount: uiState.deselectedIndices.length,
    transcriptWordCount: Array.isArray(uiState.transcript?.words)
      ? (uiState.transcript?.words ?? []).length
      : 0,
    transcript: uiState.transcript,
    rawTranscriptText: uiState.rawTranscriptText,
    lastUpdated: uiState.lastUpdated,
  });
});

/**
 * POST /api/ui-state — UI syncs state changes to server
 */
app.post("/api/ui-state", (req, res) => {
  const body = req.body;
  // Track which fields changed for targeted SSE broadcasts
  const source = body._source || "mcp"; // UI sends _source:'ui'

  if (body.videoPath !== undefined) uiState.videoPath = body.videoPath;
  if (body.filePath !== undefined) uiState.filePath = body.filePath;
  if (body.transcript !== undefined) uiState.transcript = body.transcript;
  if (body.rawTranscriptText !== undefined)
    uiState.rawTranscriptText = body.rawTranscriptText;
  if (body.suggestions !== undefined) {
    if (body._source === "ui" && Array.isArray(body.suggestions)) {
      uiState.suggestions = body.suggestions.map((incoming: SuggestedClip) => {
        if (incoming.segments?.length) return incoming;
        const segments = findSuggestionSegments(
          uiState.suggestions,
          incoming.start_second,
          incoming.end_second,
        );
        if (!segments?.length) return incoming;
        const existing = uiState.suggestions.find(
          (s) =>
            Math.abs(s.start_second - incoming.start_second) < 0.5 &&
            Math.abs(s.end_second - incoming.end_second) < 0.5,
        );
        return { ...incoming, segments, duration: existing?.duration ?? incoming.duration };
      });
    } else {
      uiState.suggestions = body.suggestions;
    }
  }
  if (body.deselectedIndices !== undefined)
    uiState.deselectedIndices = body.deselectedIndices;
  if (body.phase !== undefined) uiState.phase = body.phase;
  if (body.results !== undefined) uiState.results = body.results;
  if (body.energyData !== undefined) uiState.energyData = body.energyData;
  if (body.settings) {
    if (body.settings.captionStyle !== undefined)
      uiState.settings.captionStyle = body.settings.captionStyle;
    if (body.settings.cropStrategy !== undefined)
      uiState.settings.cropStrategy = body.settings.cropStrategy;
    if (body.settings.format !== undefined)
      uiState.settings.format = body.settings.format;
    if (body.settings.logoPath !== undefined)
      uiState.settings.logoPath = body.settings.logoPath;
    if (body.settings.outroPath !== undefined)
      uiState.settings.outroPath = body.settings.outroPath;
    if (body.settings.introPath !== undefined)
      uiState.settings.introPath = body.settings.introPath;
    if (body.settings.cleanFillers !== undefined)
      uiState.settings.cleanFillers = body.settings.cleanFillers !== false;
    if (body.settings.onboardingDismissed !== undefined)
      uiState.settings.onboardingDismissed = !!body.settings.onboardingDismissed;
  }
  uiState.lastUpdated = Date.now();
  persistState();

  // Broadcast to UI when changes come from MCP (not from UI itself).
  // Send the stored (server-enriched) values, not the raw request body, so
  // clients see keep_segments etc.; energyData rides along so unrelated syncs
  // don't wipe computed energy client-side.
  if (source !== "ui") {
    broadcastSSE("state-sync", {
      ...(body.videoPath !== undefined && { videoPath: uiState.videoPath }),
      ...(body.filePath !== undefined && { filePath: uiState.filePath }),
      ...(body.suggestions !== undefined && { suggestions: uiState.suggestions }),
      ...(body.deselectedIndices !== undefined && {
        deselectedIndices: uiState.deselectedIndices,
      }),
      ...(body.phase !== undefined && { phase: uiState.phase }),
      ...(body.transcript !== undefined && { transcript: uiState.transcript }),
      ...(body.settings && { settings: uiState.settings }),
      energyData: uiState.energyData,
    });
  }

  res.json({ ok: true });
});

// Mirrors clipKey() in the studio client: energy scores are keyed by clip
// identity, not by position, so they survive a suggestion being spliced out.
function dropEnergy(clip: SuggestedClip): void {
  const key =
    clip.clip_id ||
    `${clip.start_second.toFixed(2)}:${clip.end_second.toFixed(2)}`;
  delete uiState.energyData[key];
}

/**
 * POST /api/suggestions/modify — mutate one suggestion in-place on the server.
 * Replaces the read-modify-replace cycle MCP tools used, which lost concurrent
 * edits between the GET and the wholesale POST.
 * Body: { action: "update" | "delete" | "toggle", index? | clip_id?, updates?, selected? }
 */
app.post("/api/suggestions/modify", (req, res) => {
  const { action = "update", clip_id, updates, selected } = req.body || {};
  let index = typeof req.body?.index === "number" ? req.body.index : -1;
  if (index === -1 && clip_id) {
    index = uiState.suggestions.findIndex((s) => s.clip_id === clip_id);
  }
  if (index < 0 || index >= uiState.suggestions.length) {
    res.status(404).json({ error: "Clip not found", total: uiState.suggestions.length });
    return;
  }

  let clip: SuggestedClip;
  if (action === "delete") {
    [clip] = uiState.suggestions.splice(index, 1);
    uiState.deselectedIndices = uiState.deselectedIndices
      .filter((i) => i !== index)
      .map((i) => (i > index ? i - 1 : i));
    dropEnergy(clip);
  } else if (action === "toggle") {
    if (typeof selected !== "boolean") {
      res.status(400).json({ error: "selected must be a boolean for action 'toggle'" });
      return;
    }
    if (selected) {
      uiState.deselectedIndices = uiState.deselectedIndices.filter((i) => i !== index);
    } else if (!uiState.deselectedIndices.includes(index)) {
      uiState.deselectedIndices = [...uiState.deselectedIndices, index];
    }
    clip = uiState.suggestions[index];
  } else if (action === "update") {
    const upd = updates || {};
    clip = uiState.suggestions[index];
    const nextStart = typeof upd.start_second === "number" ? upd.start_second : clip.start_second;
    const nextEnd = typeof upd.end_second === "number" ? upd.end_second : clip.end_second;
    const rangeError = validateSuggestionRange(nextStart, nextEnd);
    if (rangeError) {
      res.status(400).json({ error: rangeError });
      return;
    }
    // The energy score was measured over the old range.
    if (nextStart !== clip.start_second || nextEnd !== clip.end_second) dropEnergy(clip);
    if (typeof upd.title === "string") clip.title = upd.title;
    clip.start_second = nextStart;
    clip.end_second = nextEnd;
    if (typeof upd.reasoning === "string") clip.reasoning = upd.reasoning;
    if (typeof upd.preview_text === "string") clip.preview_text = upd.preview_text;
    if (typeof upd.suggested_caption_style === "string") {
      clip.suggested_caption_style = upd.suggested_caption_style;
    }
    clip.duration = Math.round((clip.end_second - clip.start_second) * 10) / 10;
    const fmtTime = (s: number) =>
      `${Math.floor(s / 60)}:${Math.floor(s % 60).toString().padStart(2, "0")}`;
    clip.timestamp_display = `${fmtTime(clip.start_second)} → ${fmtTime(clip.end_second)}`;
  } else {
    res.status(400).json({ error: `Unknown action: ${action}` });
    return;
  }

  uiState.lastUpdated = Date.now();
  persistState();
  broadcastSSE("state-sync", {
    suggestions: uiState.suggestions,
    deselectedIndices: uiState.deselectedIndices,
    energyData: uiState.energyData,
  });
  res.json({
    ok: true,
    index,
    clip,
    total: uiState.suggestions.length,
    selectedCount: uiState.suggestions.length - uiState.deselectedIndices.length,
  });
});

/**
 * POST /api/mcp/export — MCP triggers export using current UI state
 */
app.post("/api/mcp/export", async (req, res) => {
  // Use UI state if no explicit params provided
  const videoPath =
    req.body.video_path || uiState.filePath || uiState.videoPath;
  const clips =
    req.body.clips ||
    uiState.suggestions.filter(
      (_: unknown, i: number) => !uiState.deselectedIndices.includes(i),
    );
  const transcriptWords =
    req.body.transcript_words ||
    (Array.isArray(uiState.transcript?.words)
      ? (uiState.transcript?.words ?? [])
      : []);
  const logoPath = req.body.logo_path || uiState.settings.logoPath || null;
  const outroPath = req.body.outro_path || uiState.settings.outroPath || null;
  const introPath = req.body.intro_path || uiState.settings.introPath || null;
  const captionStyle =
    req.body.caption_style || uiState.settings.captionStyle || "branded";
  const cropStrategy =
    req.body.crop_strategy || uiState.settings.cropStrategy || "speaker";
  const format =
    req.body.format || uiState.settings.format || "vertical";
  const allowAssFallback = req.body.allow_ass_fallback === true;
  // An agent that says nothing about fillers gets the studio's toggle, so an
  // export it triggers matches what the user set up there.
  const cleanFillers =
    req.body.clean_fillers !== undefined
      ? req.body.clean_fillers !== false
      : uiState.settings.cleanFillers !== false;
  const keepCaptionOverlay = req.body.keep_caption_overlay === true;

  if (!videoPath || !existsSync(videoPath)) {
    res.status(400).json({ error: "Video file not found" });
    return;
  }
  if (!clips.length) {
    res.status(400).json({ error: "No clips to export" });
    return;
  }
  for (let i = 0; i < clips.length; i++) {
    const c = clips[i];
    const rangeError = validateClipRange(c.start_second, c.end_second, c.format || format);
    if (rangeError) {
      res.status(400).json({ error: `Clip ${i + 1}: ${rangeError}` });
      return;
    }
  }

  await fileManager.ensureDirectories();

  // Apply style settings to clips that don't have their own
  const styledClips = clips.map((c: any) =>
    enrichClipWithSegments({
      start_second: c.start_second,
      end_second: c.end_second,
      title: c.title || "clip",
      caption_style: c.caption_style || captionStyle,
      crop_strategy: c.crop_strategy || cropStrategy,
      format: c.format || format,
      allow_ass_fallback: c.allow_ass_fallback === true || allowAssFallback,
      keep_segments:
        (Array.isArray(c.segments) && c.segments.length > 0 && c.segments) ||
        (Array.isArray(c.keep_segments) && c.keep_segments.length > 0 && c.keep_segments) ||
        (Array.isArray(c.keep_segment) && c.keep_segment.length > 0 && c.keep_segment) ||
        undefined,
    }),
  );

  const jobId = uuidv4();
  const job: JobState = {
    id: jobId,
    type: "batch_clips",
    status: "running",
    progress: 0,
    message: "Starting MCP export...",
    createdAt: Date.now(),
  };
  jobs.set(jobId, job);

  const historyRecorder = createBatchHistoryRecorder({
    jobId,
    sourceVideo: videoPath,
    transcriptWords,
    defaultCaptionStyle: captionStyle,
    defaultCropStrategy: cropStrategy,
    defaultFormat: format,
    label: "MCP export",
    clipSpecs: styledClips,
    logoPath,
    outroPath,
    introPath,
    cleanFillers,
  });

  // Broadcast to UI so it can track progress
  broadcastSSE("export-started", { jobId, clipCount: styledClips.length });
  setExportState("exporting", jobId);

  res.json({ job_id: jobId, status: "running", clipCount: styledClips.length });

  executor
    .execute<BatchClipsResult>(
      "batch_clips",
      {
        video_path: videoPath,
        clips: styledClips,
        transcript_words: transcriptWords,
        output_dir: paths.output,
        logo_path: logoPath,
        outro_path: outroPath,
        intro_path: introPath,
        clean_fillers: cleanFillers,
        keep_caption_overlay: keepCaptionOverlay,
        face_map: uiState.transcript?.face_map,
      },
      (event) => {
        const progress = advanceProgress(job, event.percent);
        job.message = event.message;
        historyRecorder.recordProgress(event);
        const clipResult = event.clip_result
          ? tagSubmittedClip(event.clip_result, styledClips)
          : undefined;
        if (event.stage === "clip_complete" && clipResult) {
          (job.clip_results ??= []).push(clipResult);
        }
        broadcastSSE("job-update", {
          jobId,
          progress,
          message: event.message,
          stage: event.stage,
          clip_result: clipResult,
        });
      },
    )
    .then(async (result) => {
      const data = tagSubmittedClips(result.data, styledClips);
      job.status = "done";
      job.progress = 100;
      job.message = "Export complete!";
      job.result = data;
      // Record clips to history
      try {
        await historyRecorder.recordRemaining(result.data?.results);
      } catch (err) {
        log.warn("Failed to record batch export clips to history", {
          err: errMsg(err),
        });
      }
      setExportState("done", null);
      broadcastSSE("job-complete", { jobId, result: data });
    })
    .catch((err) => {
      job.status = "error";
      job.error = err.message;
      job.message = `Error: ${err.message}`;
      setExportState("review", null);
      broadcastSSE("job-error", { jobId, error: err.message });
    });
});

// --- Start ---
async function main() {
  await fileManager.ensureDirectories();
  await knowledgeBase.ensureDir();
  await mkdir(uploadDir, { recursive: true });

  // Cleanup old temp files on startup (>48h)
  try {
    const cleaned = await fileManager.cleanupOldTasks(48);
    if (cleaned > 0) log.info(`Cleaned up ${cleaned} old temp files`);
  } catch (err) {
    log.warn("Startup temp-file cleanup failed", { err: errMsg(err) });
  }

  try {
    const status = await executor.execute<{
      legacy_cache_pending?: boolean;
      legacy_presets_pending?: boolean;
    }>("manage_config", {
      action: "status",
    });
    if (status.data?.legacy_cache_pending || status.data?.legacy_presets_pending) {
      await executor.execute("manage_config", { action: "migrate" });
      log.info("Migrated legacy transcription cache to data/cache");
    }
  } catch (err) {
    log.warn("Legacy cache migration skipped", { err: errMsg(err) });
  }

  // SPA fallback: client-side routes (/, /episode, /clip/:id) resolve to the
  // built index.html. Registered last so it never shadows /api or static assets.
  // root: the install path can contain a dot segment (a global npm install under
  // ~/.nvm, say), and sendFile's dotfile rule would 404 the whole app.
  app.get(/^(?!\/api\/).*/, (_req, res) => {
    res.sendFile("index.html", { root: publicDir });
  });

  // Bind to loopback by default — the studio serves local files (clips, assets,
  // source video) with no auth. Set PODCLI_HOST=0.0.0.0 to expose it on the LAN.
  const HOST = process.env.PODCLI_HOST || "127.0.0.1";
  const server = app.listen(PORT, HOST, () => {
    log.info(`podcli running at http://localhost:${PORT}`);
  });
  server.on("error", (err: NodeJS.ErrnoException) => {
    if (err.code === "EADDRINUSE") {
      log.error(`Port ${PORT} is already in use — is the studio already running? Set PODCLI_PORT to use another.`);
    } else {
      log.error("Web server failed to start", { err: err.message });
    }
    process.exit(1);
  });
}

main().catch((err) => {
  log.error("Fatal error during startup", {
    err: err instanceof Error ? err.stack : String(err),
  });
  process.exit(1);
});
