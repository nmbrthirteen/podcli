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
import { createReadStream, existsSync, statSync, readFileSync, writeFileSync } from "fs";
import { mkdir, readdir, unlink } from "fs/promises";
import { join, dirname, basename, extname } from "path";
import { execSync } from "child_process";
import { fileURLToPath } from "url";
import { v4 as uuidv4 } from "uuid";

import { PythonExecutor } from "../services/python-executor.js";
import { TranscriptCache } from "../services/transcript-cache.js";
import { FileManager } from "../services/file-manager.js";
import { AssetManager } from "../services/asset-manager.js";
import { ClipsHistory } from "../services/clips-history.js";
import { KnowledgeBase } from "../services/knowledge-base.js";
import { paths } from "../config/paths.js";

const __dirname = dirname(fileURLToPath(import.meta.url));
const app = express();
const PORT = parseInt(process.env.PORT || "3847");

// --- Services ---
const executor = new PythonExecutor();
const cache = new TranscriptCache();
const fileManager = new FileManager();
const assetManager = new AssetManager();
const clipsHistory = new ClipsHistory();
const knowledgeBase = new KnowledgeBase();

// --- State ---
// Track active jobs so the UI can poll progress
interface JobState {
  id: string;
  type: "transcribe" | "create_clip" | "batch_clips";
  status: "pending" | "running" | "done" | "error";
  progress: number;
  message: string;
  result?: Record<string, unknown>;
  error?: string;
  createdAt: number;
}

const jobs = new Map<string, JobState>();
// Store the latest transcript per uploaded file for the session
const sessionTranscripts = new Map<string, Record<string, unknown>>();

// --- MCP ↔ UI Bridge State ---
interface UIState {
  videoPath: string;
  filePath: string;
  transcript: Record<string, unknown> | null;
  rawTranscriptText: string;
  suggestions: Array<Record<string, unknown>>;
  deselectedIndices: number[];
  settings: {
    captionStyle: string;
    cropStrategy: string;
    logoPath: string;
    outroPath: string;
  };
  phase: string;
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
        transcript: saved.transcript || null,
        rawTranscriptText: saved.rawTranscriptText || "",
        suggestions: saved.suggestions || [],
        deselectedIndices: saved.deselectedIndices || [],
        settings: {
          captionStyle: saved.settings?.captionStyle || "branded",
          cropStrategy: saved.settings?.cropStrategy || "face",
          logoPath: saved.settings?.logoPath || "",
          outroPath: saved.settings?.outroPath || "",
        },
        // Never restore mid-export phases
        phase: ["exporting", "parsing", "suggesting"].includes(saved.phase) ? "idle" : (saved.phase || "idle"),
        lastUpdated: saved.lastUpdated || 0,
      };
    }
  } catch {}
  return {
    videoPath: "",
    filePath: "",
    transcript: null,
    rawTranscriptText: "",
    suggestions: [],
    deselectedIndices: [],
    settings: { captionStyle: "branded", cropStrategy: "face", logoPath: "", outroPath: "" },
    phase: "idle",
    lastUpdated: 0,
  };
}

const uiState: UIState = loadPersistedState();

// Debounced save to disk
let saveTimer: ReturnType<typeof setTimeout> | null = null;
function persistState() {
  if (saveTimer) clearTimeout(saveTimer);
  saveTimer = setTimeout(() => {
    try {
      writeFileSync(paths.uiState, JSON.stringify(uiState, null, 2));
    } catch {}
  }, 500);
}

// SSE clients for the global event bus
import type { Response } from "express";
const sseClients: Response[] = [];

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

// --- Middleware ---
app.use(express.json({ limit: "50mb" }));

// Serve static frontend
app.use(express.static(join(__dirname, "public")));

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
    const allowed = [".mp4", ".mov", ".mkv", ".webm", ".mp3", ".wav", ".m4a", ".png", ".jpg", ".jpeg", ".svg"];
    const ext = extname(file.originalname).toLowerCase();
    if (allowed.includes(ext)) {
      cb(null, true);
    } else {
      cb(new Error(`Unsupported format: ${ext}. Use MP4, MOV, MKV, WebM, MP3, WAV, M4A.`));
    }
  },
});

// --- API Routes ---

/**
 * POST /api/upload — Upload a podcast file
 */
app.post("/api/upload", upload.single("file"), (req, res) => {
  if (!req.file) {
    res.status(400).json({ error: "No file uploaded" });
    return;
  }
  res.json({
    file_path: req.file.path,
    filename: req.file.originalname,
    size_mb: Math.round((req.file.size / (1024 * 1024)) * 100) / 100,
  });
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
  res.json({
    file_path,
    filename: basename(file_path),
    size_mb: Math.round((stat.size / (1024 * 1024)) * 100) / 100,
  });
});

/**
 * GET /api/browse-file — Open native OS file dialog and return the selected path
 */
app.get("/api/browse-file", (_req, res) => {
  try {
    let filePath: string;
    if (process.platform === "darwin") {
      const script = `osascript -e 'POSIX path of (choose file of type {"mp4","mov","mkv","webm","mp3","wav","m4a"})'`;
      filePath = execSync(script, { encoding: "utf-8", timeout: 120_000 }).trim();
    } else {
      // Linux fallback
      filePath = execSync(
        `zenity --file-selection --file-filter="Media files|*.mp4 *.mov *.mkv *.webm *.mp3 *.wav *.m4a"`,
        { encoding: "utf-8", timeout: 120_000 }
      ).trim();
    }

    if (!filePath || !existsSync(filePath)) {
      res.json({ error: "cancelled" });
      return;
    }

    const stat = statSync(filePath);
    res.json({
      file_path: filePath,
      filename: basename(filePath),
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
      error: "transcript must include a 'words' array with { word, start, end } objects",
    });
    return;
  }

  // Build a full transcript result from the imported data
  const result: Record<string, unknown> = {
    transcript: transcript.text || transcript.words.map((w: any) => w.word).join(" "),
    words: transcript.words,
    segments: transcript.segments || [],
    duration: transcript.duration || (transcript.words.length > 0
      ? transcript.words[transcript.words.length - 1].end
      : 0),
    language: transcript.language || "en",
    speakers: transcript.speakers || null,
    speaker_segments: transcript.speaker_segments || null,
    imported: true,
  };

  sessionTranscripts.set(file_path, result);

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
      sessionTranscripts.set(file_path, result.data as Record<string, unknown>);
    }

    res.json({
      status: "done",
      imported: true,
      data: result.data,
    });
  } catch (err: any) {
    res.status(500).json({ error: err.message || "Failed to parse transcript" });
  }
});

/**
 * POST /api/transcribe — Start transcription job
 */
app.post("/api/transcribe", async (req, res) => {
  const { file_path, model_size = "base", language, enable_diarization = false, num_speakers } = req.body;

  if (!file_path || !existsSync(file_path)) {
    res.status(400).json({ error: "File not found" });
    return;
  }

  // Check cache first
  const cached = await cache.get(file_path);
  if (cached) {
    const jobId = uuidv4();
    sessionTranscripts.set(file_path, cached as unknown as Record<string, unknown>);
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
      { file_path, model_size, language, enable_diarization, num_speakers },
      (event) => {
        job.progress = event.percent;
        job.message = event.message;
      }
    )
    .then(async (result) => {
      job.status = "done";
      job.progress = 100;
      job.message = "Transcription complete";
      job.result = result.data;
      sessionTranscripts.set(file_path, result.data as Record<string, unknown>);
      // Cache it
      try {
        await cache.set(file_path, result.data as any);
      } catch {}
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
    crop_strategy = "face",
    transcript_words = [],
    title = "clip",
    logo_path = null,
    outro_path = null,
  } = req.body;

  if (!video_path || !existsSync(video_path)) {
    res.status(400).json({ error: "Video file not found" });
    return;
  }

  // Validate clip params before spawning Python
  if (typeof start_second !== "number" || typeof end_second !== "number") {
    res.status(400).json({ error: "start_second and end_second must be numbers" });
    return;
  }
  if (end_second <= start_second) {
    res.status(400).json({ error: "end_second must be greater than start_second" });
    return;
  }
  const duration = end_second - start_second;
  if (duration > 180) {
    res.status(400).json({ error: `Clip too long (${Math.round(duration)}s). Max 180 seconds.` });
    return;
  }
  if (logo_path && !existsSync(logo_path)) {
    res.status(400).json({ error: `Logo file not found: ${logo_path}` });
    return;
  }
  if (outro_path && !existsSync(outro_path)) {
    res.status(400).json({ error: `Outro file not found: ${outro_path}` });
    return;
  }
  const validStyles = ["hormozi", "karaoke", "subtle", "branded"];
  if (!validStyles.includes(caption_style)) {
    res.status(400).json({ error: `Invalid caption style. Use: ${validStyles.join(", ")}` });
    return;
  }
  const validCrops = ["center", "face"];
  if (!validCrops.includes(crop_strategy)) {
    res.status(400).json({ error: `Invalid crop strategy. Use: ${validCrops.join(", ")}` });
    return;
  }

  await fileManager.ensureDirectories();

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
    .execute(
      "create_clip",
      {
        video_path,
        start_second,
        end_second,
        caption_style,
        crop_strategy,
        transcript_words,
        title,
        output_dir: paths.output,
        logo_path,
        outro_path,
      },
      (event) => {
        job.progress = event.percent;
        job.message = event.message;
      }
    )
    .then(async (result) => {
      job.status = "done";
      job.progress = 100;
      job.message = "Clip created!";
      job.result = result.data;
      broadcastSSE("job-complete", { jobId, result: result.data });
      // Record to history
      try {
        const d = result.data as any;
        await clipsHistory.record({
          source_video: video_path,
          start_second, end_second,
          caption_style, crop_strategy,
          logo_path: logo_path || undefined,
          title, output_path: d.output_path || "",
          file_size_mb: d.file_size_mb || 0,
          duration: d.duration || 0,
        });
      } catch {}
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
  const { video_path, clips, transcript_words = [], logo_path = null, outro_path = null } = req.body;

  if (!video_path || !existsSync(video_path)) {
    res.status(400).json({ error: "Video file not found" });
    return;
  }
  if (!clips || !Array.isArray(clips) || clips.length === 0) {
    res.status(400).json({ error: "No clips provided" });
    return;
  }
  // Validate each clip's timing
  for (let i = 0; i < clips.length; i++) {
    const c = clips[i];
    const dur = (c.end_second || 0) - (c.start_second || 0);
    if (dur <= 0) {
      res.status(400).json({ error: `Clip ${i + 1}: end must be after start` });
      return;
    }
    if (dur > 180) {
      res.status(400).json({ error: `Clip ${i + 1}: too long (${Math.round(dur)}s). Max 180s.` });
      return;
    }
  }
  if (logo_path && !existsSync(logo_path)) {
    res.status(400).json({ error: `Logo file not found: ${logo_path}` });
    return;
  }
  if (outro_path && !existsSync(outro_path)) {
    res.status(400).json({ error: `Outro file not found: ${outro_path}` });
    return;
  }

  await fileManager.ensureDirectories();

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

  res.json({ job_id: jobId, status: "running" });

  executor
    .execute(
      "batch_clips",
      { video_path, clips, transcript_words, output_dir: paths.output, logo_path, outro_path },
      (event) => {
        job.progress = event.percent;
        job.message = event.message;
      }
    )
    .then(async (result) => {
      job.status = "done";
      job.progress = 100;
      job.message = "Batch complete!";
      job.result = result.data;
      broadcastSSE("job-complete", { jobId, result: result.data });
      // Record successful clips to history
      try {
        const d = result.data as any;
        if (d?.results) {
          for (const r of d.results) {
            if (r.status === "success" && r.output_path) {
              await clipsHistory.record({
                source_video: video_path,
                start_second: r.start_second || 0,
                end_second: r.end_second || 0,
                caption_style: r.caption_style || "hormozi",
                crop_strategy: r.crop_strategy || "face",
                title: r.title || "clip",
                output_path: r.output_path,
                file_size_mb: r.file_size_mb || 0,
                duration: r.duration || 0,
              });
            }
          }
        }
      } catch {}
    })
    .catch((err) => {
      job.status = "error";
      job.error = err.message;
      job.message = `Error: ${err.message}`;
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
      })}\n\n`
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
      .sort((a, b) => new Date(b.created).getTime() - new Date(a.created).getTime());
    res.json(clips);
  } catch {
    res.json([]);
  }
});

/**
 * GET /api/download/:filename — Download a finished clip
 */
app.get("/api/download/:filename", (req, res) => {
  const filePath = join(paths.output, req.params.filename);
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
  const filePath = join(paths.output, req.params.filename);
  if (!existsSync(filePath)) {
    res.status(404).json({ error: "File not found" });
    return;
  }

  const stat = statSync(filePath);
  const fileSize = stat.size;
  const range = req.headers.range;

  if (range) {
    const parts = range.replace(/bytes=/, "").split("-");
    const start = parseInt(parts[0], 10);
    const end = parts[1] ? parseInt(parts[1], 10) : fileSize - 1;
    const chunkSize = end - start + 1;

    const stream = createReadStream(filePath, { start, end });
    res.writeHead(206, {
      "Content-Range": `bytes ${start}-${end}/${fileSize}`,
      "Accept-Ranges": "bytes",
      "Content-Length": chunkSize,
      "Content-Type": "video/mp4",
    });
    stream.pipe(res);
  } else {
    res.writeHead(200, {
      "Content-Length": fileSize,
      "Content-Type": "video/mp4",
    });
    createReadStream(filePath).pipe(res);
  }
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

  const stat = statSync(filePath);
  const fileSize = stat.size;
  const range = req.headers.range;
  const ext = extname(filePath).toLowerCase();
  const mimeTypes: Record<string, string> = {
    ".mp4": "video/mp4",
    ".webm": "video/webm",
    ".mov": "video/quicktime",
    ".avi": "video/x-msvideo",
    ".mkv": "video/x-matroska",
    ".m4v": "video/mp4",
  };
  const contentType = mimeTypes[ext] || "video/mp4";

  if (range) {
    const parts = range.replace(/bytes=/, "").split("-");
    const start = parseInt(parts[0], 10);
    const end = parts[1] ? parseInt(parts[1], 10) : fileSize - 1;
    const chunkSize = end - start + 1;
    const stream = createReadStream(filePath, { start, end });
    res.writeHead(206, {
      "Content-Range": `bytes ${start}-${end}/${fileSize}`,
      "Accept-Ranges": "bytes",
      "Content-Length": chunkSize,
      "Content-Type": contentType,
    });
    stream.pipe(res);
  } else {
    res.writeHead(200, {
      "Content-Length": fileSize,
      "Content-Type": contentType,
    });
    createReadStream(filePath).pipe(res);
  }
});

// --- Integration info ---
app.get("/api/integration-info", (_req, res) => {
  const projectRoot = join(__dirname, "..", "..");
  const distPath = join(projectRoot, "dist", "index.js");
  const serverOk = existsSync(distPath);

  res.json({
    dist_path: distPath,
    project_root: projectRoot,
    server_ok: serverOk,
    tools_count: 4,
  });
});

// --- Transcript export (SRT/VTT) ---
app.get("/api/export-transcript", (_req, res) => {
  const format = (_req.query.format as string) || "srt";
  const transcript = uiState.transcript as any;

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
    res.setHeader("Content-Disposition", "attachment; filename=transcript.json");
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
  if (!video_path) return res.json({ error: "video_path required" });
  try {
    const result = await executor.execute("analyze_energy", { video_path, segments: segments || [] });
    res.json(result.data || {});
  } catch (err: any) {
    res.json({ error: err.message });
  }
});

// --- Encoder info ---
app.get("/api/encoder-info", async (_req, res) => {
  try {
    const result = await executor.execute("detect_encoder", {});
    res.json(result.data || {});
  } catch (err: any) {
    res.json({ error: err.message });
  }
});

// --- Presets ---
app.get("/api/presets", async (_req, res) => {
  try {
    const result = await executor.execute("presets", { action: "list" });
    res.json(result.data || { presets: [] });
  } catch (err: any) {
    res.json({ error: err.message });
  }
});

app.post("/api/presets", async (req, res) => {
  const { action, name, config } = req.body;
  try {
    const result = await executor.execute("presets", { action, name, config });
    res.json(result.data || {});
  } catch (err: any) {
    res.json({ error: err.message });
  }
});

// --- Assets ---
app.get("/api/assets", async (req, res) => {
  try {
    const items = await assetManager.list(req.query.type as string | undefined);
    res.json(items);
  } catch (err: any) {
    res.json({ error: err.message });
  }
});

app.post("/api/assets/register", async (req, res) => {
  const { name, path: filePath, type = "other" } = req.body;
  try {
    const asset = await assetManager.register(name, filePath, type);
    res.json(asset);
  } catch (err: any) {
    res.status(400).json({ error: err.message });
  }
});

app.post("/api/assets/unregister", async (req, res) => {
  try {
    await assetManager.unregister(req.body.name);
    res.json({ ok: true });
  } catch (err: any) {
    res.json({ error: err.message });
  }
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

app.get("/api/history/check", async (req, res) => {
  try {
    const { source, start, end, style = "hormozi", crop = "face" } = req.query;
    if (!source || !start || !end) {
      res.json({ duplicate: null });
      return;
    }
    const dup = await clipsHistory.findDuplicate(
      source as string,
      parseFloat(start as string),
      parseFloat(end as string),
      style as string,
      crop as string
    );
    res.json({ duplicate: dup });
  } catch (err: any) {
    res.json({ duplicate: null });
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

// Knowledge file upload (drag & drop .md files) — must be before :filename routes
const knowledgeUpload = multer({
  storage: multer.diskStorage({
    destination: async (_req, _file, cb) => {
      await mkdir(paths.knowledge, { recursive: true });
      cb(null, paths.knowledge);
    },
    filename: (_req, file, cb) => cb(null, file.originalname),
  }),
  fileFilter: (_req, file, cb) => {
    if (file.originalname.endsWith(".md") || file.originalname.endsWith(".txt")) {
      cb(null, true);
    } else {
      cb(new Error("Only .md and .txt files are allowed"));
    }
  },
});

app.post("/api/knowledge/upload", knowledgeUpload.array("files", 50), (req, res) => {
  const files = req.files as Express.Multer.File[];
  if (!files || files.length === 0) {
    res.status(400).json({ error: "No files uploaded" });
    return;
  }
  res.json({ uploaded: files.map((f) => f.originalname) });
});

app.get("/api/knowledge/:filename", async (req, res) => {
  try {
    const content = await knowledgeBase.readFile(req.params.filename);
    res.json({ filename: req.params.filename, content });
  } catch (err: any) {
    res.status(404).json({ error: err.message });
  }
});

app.post("/api/knowledge/:filename", async (req, res) => {
  try {
    await knowledgeBase.writeFile(req.params.filename, req.body.content);
    res.json({ ok: true, filename: req.params.filename });
  } catch (err: any) {
    res.status(400).json({ error: err.message });
  }
});

app.delete("/api/knowledge/:filename", async (req, res) => {
  try {
    await knowledgeBase.deleteFile(req.params.filename);
    res.json({ ok: true });
  } catch (err: any) {
    res.json({ error: err.message });
  }
});

// --- Prompt Builder (shared by /api/generate-prompt and /api/mcp-hints) ---

function getStateContext() {
  const hasVideo = !!uiState.videoPath;
  const hasTranscript = Array.isArray((uiState.transcript as any)?.words) && (uiState.transcript as any).words.length > 0;
  const hasRawTranscript = !!uiState.rawTranscriptText?.trim();
  const hasSuggestions = uiState.suggestions.length > 0;
  const selectedCount = uiState.suggestions.length - uiState.deselectedIndices.length;
  return { hasVideo, hasTranscript, hasRawTranscript, hasSuggestions, selectedCount, phase: uiState.phase };
}

function buildPromptForAction(action: "suggest" | "export" | "restyle"): string {
  const ctx = getStateContext();
  const parts: string[] = [];

  const settingsSummary = [
    uiState.videoPath ? `Video: ${uiState.videoPath.split("/").pop()}` : null,
    `Style: ${uiState.settings.captionStyle}`,
    `Crop: ${uiState.settings.cropStrategy}`,
    uiState.settings.logoPath ? `Logo: set` : null,
    uiState.settings.outroPath ? `Outro: set` : null,
  ].filter(Boolean).join(", ");

  if (action === "suggest") {
    if (!ctx.hasTranscript && !ctx.hasRawTranscript) {
      // No transcript yet — prompt includes transcribe step
      if (uiState.videoPath) {
        parts.push(`Transcribe the podcast at ${uiState.videoPath} using transcribe_podcast.`);
      } else {
        parts.push("Set a video path first, then transcribe it using transcribe_podcast.");
      }
      parts.push("Then find the 5-8 best viral-worthy moments and call suggest_clips.");
    } else {
      parts.push("Use get_ui_state with include_transcript=true to read the full transcript.");
      parts.push("Find the 5-8 best viral-worthy moments — hot takes, strong opinions, funny moments, actionable advice, and emotional stories.");
      parts.push("Then call suggest_clips with your suggestions.");
    }
    parts.push(`Current settings: ${settingsSummary}`);
  } else if (action === "export") {
    parts.push("Use get_ui_state to read the selected clips.");
    parts.push(`Export all ${ctx.selectedCount} selected clip${ctx.selectedCount !== 1 ? "s" : ""} using batch_create_clips.`);
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
      { prompt: "Find the 5 best viral moments from this podcast", description: "Clips optimized for TikTok/Shorts", category: "analyze" },
      { prompt: "Find moments with hot takes and strong opinions", description: "Controversial, high-engagement clips", category: "analyze" },
      { prompt: "Find funny moments and quotable one-liners", description: "Entertainment-focused clips", category: "analyze" },
      { prompt: "Find actionable advice and key insights", description: "Value-driven educational clips", category: "analyze" },
    );
  } else if (ctx.phase === "review" && ctx.hasSuggestions && ctx.selectedCount > 0) {
    // Clips suggested, ready for action
    hints.push(
      { prompt: "Export all selected clips", description: `Render ${ctx.selectedCount} clip${ctx.selectedCount !== 1 ? "s" : ""} as vertical shorts`, category: "export" },
      { prompt: "Export clip #1", description: "Render just the first clip", category: "export" },
      { prompt: "Change all clips to hormozi style", description: "Bold uppercase, yellow highlight", category: "refine" },
      { prompt: "Extend clip #1 by 10 seconds", description: "Adjust timing before export", category: "refine" },
      { prompt: "Find 5 more moments", description: "Additional clips from the transcript", category: "analyze" },
    );
  } else if (ctx.phase === "done") {
    hints.push(
      { prompt: "Find more viral moments from the transcript", description: "Get another batch of clips", category: "analyze" },
      { prompt: "Re-export all clips with karaoke style", description: "Try a different caption look", category: "refine" },
      { prompt: "Save these settings as a preset called 'myshow'", description: "Reuse this config next time", category: "refine" },
    );
  }

  res.json({ hints, phase: ctx.phase, hasVideo: ctx.hasVideo, hasTranscript: ctx.hasTranscript, hasSuggestions: ctx.hasSuggestions, selectedCount: ctx.selectedCount });
});

/**
 * POST /api/generate-prompt — Build an MCP prompt for the given action using authoritative server state.
 * Body: { action: "suggest" | "export" | "restyle" }
 * Returns: { prompt, action, context }
 */
app.post("/api/generate-prompt", (req, res) => {
  const action = req.body.action || "suggest";
  if (!["suggest", "export", "restyle"].includes(action)) {
    res.status(400).json({ error: "action must be 'suggest', 'export', or 'restyle'" });
    return;
  }
  const prompt = buildPromptForAction(action);
  const ctx = getStateContext();
  res.json({ prompt, action, context: ctx });
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
    (_: unknown, i: number) => !uiState.deselectedIndices.includes(i)
  );
  res.json({
    videoPath: uiState.videoPath,
    filePath: uiState.filePath,
    phase: uiState.phase,
    settings: uiState.settings,
    selectedClips: selected,
    suggestions: uiState.suggestions,
    deselectedIndices: uiState.deselectedIndices,
    totalSuggestions: uiState.suggestions.length,
    deselectedCount: uiState.deselectedIndices.length,
    transcriptWordCount: Array.isArray((uiState.transcript as any)?.words)
      ? (uiState.transcript as any).words.length
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
  if (body.rawTranscriptText !== undefined) uiState.rawTranscriptText = body.rawTranscriptText;
  if (body.suggestions !== undefined) uiState.suggestions = body.suggestions;
  if (body.deselectedIndices !== undefined) uiState.deselectedIndices = body.deselectedIndices;
  if (body.phase !== undefined) uiState.phase = body.phase;
  if (body.settings) {
    if (body.settings.captionStyle !== undefined) uiState.settings.captionStyle = body.settings.captionStyle;
    if (body.settings.cropStrategy !== undefined) uiState.settings.cropStrategy = body.settings.cropStrategy;
    if (body.settings.logoPath !== undefined) uiState.settings.logoPath = body.settings.logoPath;
    if (body.settings.outroPath !== undefined) uiState.settings.outroPath = body.settings.outroPath;
  }
  uiState.lastUpdated = Date.now();
  persistState();

  // Broadcast to UI when changes come from MCP (not from UI itself)
  if (source !== "ui") {
    broadcastSSE("state-sync", {
      ...(body.videoPath !== undefined && { videoPath: body.videoPath }),
      ...(body.filePath !== undefined && { filePath: body.filePath }),
      ...(body.suggestions !== undefined && { suggestions: body.suggestions }),
      ...(body.deselectedIndices !== undefined && { deselectedIndices: body.deselectedIndices }),
      ...(body.phase !== undefined && { phase: body.phase }),
      ...(body.transcript !== undefined && { transcript: body.transcript }),
      ...(body.settings && { settings: body.settings }),
    });
  }

  res.json({ ok: true });
});

/**
 * POST /api/mcp/export — MCP triggers export using current UI state
 */
app.post("/api/mcp/export", async (req, res) => {
  // Use UI state if no explicit params provided
  const videoPath = req.body.video_path || uiState.filePath || uiState.videoPath;
  const clips = req.body.clips || uiState.suggestions.filter(
    (_: unknown, i: number) => !uiState.deselectedIndices.includes(i)
  );
  const transcriptWords = req.body.transcript_words ||
    (Array.isArray((uiState.transcript as any)?.words) ? (uiState.transcript as any).words : []);
  const logoPath = req.body.logo_path || uiState.settings.logoPath || null;
  const outroPath = req.body.outro_path || (uiState.settings as any).outroPath || null;
  const captionStyle = req.body.caption_style || uiState.settings.captionStyle || "branded";
  const cropStrategy = req.body.crop_strategy || uiState.settings.cropStrategy || "face";

  if (!videoPath || !existsSync(videoPath)) {
    res.status(400).json({ error: "Video file not found" });
    return;
  }
  if (!clips.length) {
    res.status(400).json({ error: "No clips to export" });
    return;
  }

  await fileManager.ensureDirectories();

  // Apply style settings to clips that don't have their own
  const styledClips = clips.map((c: any) => ({
    start_second: c.start_second,
    end_second: c.end_second,
    title: (c.title || "clip").slice(0, 40),
    caption_style: c.caption_style || captionStyle,
    crop_strategy: c.crop_strategy || cropStrategy,
  }));

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

  // Broadcast to UI so it can track progress
  broadcastSSE("export-started", { jobId, clipCount: styledClips.length });
  uiState.phase = "exporting";
  uiState.lastUpdated = Date.now();
  persistState();

  res.json({ job_id: jobId, status: "running", clipCount: styledClips.length });

  executor
    .execute(
      "batch_clips",
      { video_path: videoPath, clips: styledClips, transcript_words: transcriptWords, output_dir: paths.output, logo_path: logoPath, outro_path: outroPath },
      (event) => {
        job.progress = event.percent;
        job.message = event.message;
        broadcastSSE("job-update", { jobId, progress: event.percent, message: event.message });
      }
    )
    .then(async (result) => {
      job.status = "done";
      job.progress = 100;
      job.message = "Export complete!";
      job.result = result.data;
      // Record clips to history
      try {
        const d = result.data as any;
        if (d?.results) {
          for (const r of d.results) {
            if (r.status === "success" && r.output_path) {
              await clipsHistory.record({
                source_video: videoPath,
                start_second: r.start_second || 0,
                end_second: r.end_second || 0,
                caption_style: r.caption_style || captionStyle,
                crop_strategy: r.crop_strategy || cropStrategy,
                title: r.title || "clip",
                output_path: r.output_path,
                file_size_mb: r.file_size_mb || 0,
                duration: r.duration || 0,
              });
            }
          }
        }
      } catch {}
      broadcastSSE("job-complete", { jobId, result: result.data });
    })
    .catch((err) => {
      job.status = "error";
      job.error = err.message;
      job.message = `Error: ${err.message}`;
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
    if (cleaned > 0) console.log(`  Cleaned up ${cleaned} old temp files`);
  } catch {}

  app.listen(PORT, () => {
    console.log(`\n  🎬 podcli running at:`);
    console.log(`  ➜  http://localhost:${PORT}\n`);
  });
}

main().catch(console.error);
