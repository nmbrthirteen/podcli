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
import { createReadStream, existsSync, statSync } from "fs";
import { mkdir, readdir, unlink } from "fs/promises";
import { join, dirname, basename, extname } from "path";
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
    crop_strategy = "center",
    transcript_words = [],
    title = "clip",
    logo_path = null,
  } = req.body;

  if (!video_path || !existsSync(video_path)) {
    res.status(400).json({ error: "Video file not found" });
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
    });
});

/**
 * POST /api/batch-clips — Create multiple clips
 */
app.post("/api/batch-clips", async (req, res) => {
  const { video_path, clips, transcript_words = [], logo_path = null } = req.body;

  if (!video_path || !existsSync(video_path)) {
    res.status(400).json({ error: "Video file not found" });
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
      { video_path, clips, transcript_words, output_dir: paths.output, logo_path },
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
                crop_strategy: r.crop_strategy || "center",
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
    const { source, start, end, style = "hormozi", crop = "center" } = req.query;
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

// --- Start ---
async function main() {
  await fileManager.ensureDirectories();
  await knowledgeBase.ensureDir();
  await mkdir(uploadDir, { recursive: true });

  app.listen(PORT, () => {
    console.log(`\n  🎬 podcli running at:`);
    console.log(`  ➜  http://localhost:${PORT}\n`);
  });
}

main().catch(console.error);
