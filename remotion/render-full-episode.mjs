#!/usr/bin/env node

/**
 * Burn Remotion captions into an arbitrarily long source video.
 *
 * A full-length transparent ProRes overlay can consume tens of gigabytes. This
 * renderer instead creates one short overlay at a time, composites it, deletes
 * it, then losslessly concatenates the compressed video chunks and remuxes the
 * source audio.
 */

import { renderMedia, selectComposition } from "@remotion/renderer";
import { getCachedBundle } from "./bundle-cache.mjs";
import { hdCanvas, overlayFilter } from "./render.mjs";
import { spawnSync } from "node:child_process";
import crypto from "node:crypto";
import fs from "node:fs";
import http from "node:http";
import os from "node:os";
import path from "node:path";

const parseArgs = () => {
  const out = {};
  for (let i = 2; i < process.argv.length; i += 2) {
    const key = process.argv[i]?.replace(/^--/, "");
    const value = process.argv[i + 1];
    if (key && value) out[key] = value;
  }
  return out;
};

// spawnSync blocks the event loop, so the signal handlers below cannot run
// while a child is alive. Without a bound, one stalled ffmpeg leaves the job
// "running" forever with no way for the user to cancel it.
const STEP_TIMEOUT_MS = 30 * 60 * 1000;

const run = (command, args) => {
  const result = spawnSync(command, args, {
    encoding: "utf8",
    stdio: ["ignore", "pipe", "pipe"],
    timeout: STEP_TIMEOUT_MS,
  });
  const name = path.basename(command);
  if (result.error) {
    const reason = result.error.code === "ETIMEDOUT"
      ? `timed out after ${STEP_TIMEOUT_MS / 60000} minutes`
      : result.error.message;
    throw new Error(`${name} failed: ${reason}`);
  }
  if (result.status !== 0) {
    const detail = (result.stderr || result.stdout || "unknown error").trim().slice(-3000);
    throw new Error(`${name} failed (${result.status}): ${detail}`);
  }
  return (result.stdout || "").trim();
};

const progress = (percent, message) => {
  process.stdout.write(`PODCLI_PROGRESS=${JSON.stringify({ percent, message })}\n`);
};

const quoteConcatPath = (filePath) => {
  const normalized = filePath.replaceAll("\\", "/");
  return `'${normalized.replaceAll("'", "'\\''")}'`;
};

const args = parseArgs();
for (const required of ["video", "words", "output", "ffmpeg", "ffprobe"]) {
  if (!args[required]) throw new Error(`Missing --${required}`);
}

const video = path.resolve(args.video);
const wordsPath = path.resolve(args.words);
const output = path.resolve(args.output);
const partialOutput = `${output}.partial.mp4`;
const logo = args.logo ? path.resolve(args.logo) : null;
const styleName = args.style || "branded";
const captionPosition = args["caption-position"] || "auto";
// Mirrors the 60-160 clamp in backend/services/caption_renderer.py so the ASS
// and Remotion caption paths render the same size for a given request.
const rawFontScale = Number(args["caption-font-scale"]);
const captionFontScale = Number.isFinite(rawFontScale)
  ? Math.min(160, Math.max(60, rawFontScale))
  : 100;
const logoPosition = args["logo-position"] || "top-left";
const fps = Number(args.fps || 30);
const chunkSeconds = Number(args["chunk-seconds"] || 15);

if (fs.existsSync(output)) throw new Error(`Refusing to overwrite existing output: ${output}`);
if (!fs.existsSync(video)) throw new Error(`Video not found: ${video}`);
if (!fs.existsSync(wordsPath)) throw new Error(`Words JSON not found: ${wordsPath}`);
if (logo && !fs.existsSync(logo)) throw new Error(`Logo not found: ${logo}`);
if (!(fps > 0) || !(chunkSeconds > 0)) throw new Error("fps and chunk-seconds must be positive");

const wordsData = JSON.parse(fs.readFileSync(wordsPath, "utf8"));
const words = Array.isArray(wordsData) ? wordsData : wordsData.words || [];
const faceY = Array.isArray(wordsData) ? null : wordsData.faceY ?? null;
const dimensions = run(args.ffprobe, [
  "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
  "-of", "csv=s=x:p=0", video,
]);
const [width, height] = dimensions.split("x").map(Number);
const duration = Number(run(args.ffprobe, [
  "-v", "error", "-show_entries", "format=duration",
  "-of", "default=noprint_wrappers=1:nokey=1", video,
]));
if (!(width > 0 && height > 0 && duration > 0)) throw new Error("Could not probe video");

const durationInFrames = Math.ceil(duration * fps);
const framesPerChunk = Math.max(1, Math.round(chunkSeconds * fps));
const outputDir = path.dirname(output);
fs.mkdirSync(outputDir, { recursive: true });
const workDir = fs.mkdtempSync(path.join(outputDir, ".podcli-full-caption-work-"));
let server;

const cleanup = () => {
  try { server?.close(); } catch {}
  try { fs.rmSync(workDir, { recursive: true, force: true }); } catch {}
  try { fs.rmSync(partialOutput, { force: true }); } catch {}
};
process.on("SIGINT", () => { cleanup(); process.exit(130); });
process.on("SIGTERM", () => { cleanup(); process.exit(143); });

try {
  server = http.createServer((request, response) => {
    if (request.url !== "/logo.png" || !logo) {
      response.writeHead(404);
      response.end();
      return;
    }
    const stat = fs.statSync(logo);
    response.writeHead(200, {
      "Content-Type": "image/png",
      "Content-Length": stat.size,
      "Access-Control-Allow-Origin": "*",
    });
    fs.createReadStream(logo).pipe(response);
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const logoSrc = logo ? `http://127.0.0.1:${server.address().port}/logo.png` : undefined;

  progress(2, "Preparing caption renderer");
  const bundle = await getCachedBundle({ onBundle: () => progress(3, "Preparing caption renderer") });
  const canvas = hdCanvas(width, height);
  const inputProps = {
    videoSrc: "",
    words,
    styleName,
    logoSrc,
    faceY,
    durationInFrames,
    fps,
    captionPosition,
    captionFontScale,
    logoPosition,
    singleLine: true,
    canvasWidth: canvas.width,
    canvasHeight: canvas.height,
  };
  const composition = await selectComposition({
    serveUrl: bundle,
    id: "CaptionedClip",
    inputProps,
    timeoutInMilliseconds: 120000,
  });
  const renderComposition = { ...composition, durationInFrames, fps, width: canvas.width, height: canvas.height };
  const chunks = [];
  const chunkCount = Math.ceil(durationInFrames / framesPerChunk);
  const requestedConcurrency = Number.parseInt(process.env.PODCLI_REMOTION_CONCURRENCY || "", 10);
  const concurrency = Number.isFinite(requestedConcurrency)
    ? Math.max(1, Math.min(os.cpus().length, requestedConcurrency))
    : Math.max(2, Math.min(os.cpus().length, 8));

  for (let index = 0; index < chunkCount; index++) {
    const startFrame = index * framesPerChunk;
    const endFrame = Math.min(durationInFrames - 1, startFrame + framesPerChunk - 1);
    const startSeconds = startFrame / fps;
    const sectionDuration = (endFrame - startFrame + 1) / fps;
    const id = String(index + 1).padStart(4, "0");
    const overlay = path.join(workDir, `overlay-${id}.mov`);
    const chunk = path.join(workDir, `video-${id}.mp4`);
    let lastPercent = -1;

    await renderMedia({
      composition: renderComposition,
      serveUrl: bundle,
      codec: "prores",
      proResProfile: "4444",
      pixelFormat: "yuva444p10le",
      imageFormat: "png",
      outputLocation: overlay,
      inputProps,
      frameRange: [startFrame, endFrame],
      concurrency,
      timeoutInMilliseconds: 120000,
      onProgress: ({ progress: chunkProgress }) => {
        const percent = Math.floor(chunkProgress * 100);
        if (percent >= lastPercent + 10) {
          lastPercent = percent;
          const overall = 5 + ((index + chunkProgress) / chunkCount) * 80;
          progress(overall, `Rendering captions ${index + 1}/${chunkCount}`);
        }
      },
    });

    progress(5 + ((index + 1) / chunkCount) * 80, `Compositing section ${index + 1}/${chunkCount}`);
    run(args.ffmpeg, [
      "-y", "-hide_banner", "-loglevel", "error",
      "-ss", startSeconds.toFixed(6), "-t", sectionDuration.toFixed(6), "-i", video,
      "-i", overlay,
      "-filter_complex", `${overlayFilter()},format=yuv420p[v]`,
      "-map", "[v]", "-an",
      "-c:v", "libx264", "-crf", "18", "-preset", "fast",
      "-r", String(fps), "-g", String(fps * 2),
      "-movflags", "+faststart", chunk,
    ]);
    fs.rmSync(overlay, { force: true });
    chunks.push(chunk);
  }

  progress(90, "Joining captioned sections");
  const listPath = path.join(workDir, "chunks.txt");
  fs.writeFileSync(listPath, chunks.map((chunk) => `file ${quoteConcatPath(chunk)}`).join("\n") + "\n");
  const videoOnly = path.join(workDir, `video-only-${crypto.randomUUID()}.mp4`);
  run(args.ffmpeg, [
    "-y", "-hide_banner", "-loglevel", "error", "-f", "concat", "-safe", "0",
    "-i", listPath, "-c", "copy", "-movflags", "+faststart", videoOnly,
  ]);

  progress(96, "Adding original audio");
  run(args.ffmpeg, [
    "-y", "-hide_banner", "-loglevel", "error", "-i", videoOnly, "-i", video,
    "-map", "0:v:0", "-map", "1:a:0?", "-c", "copy", "-shortest",
    "-movflags", "+faststart", partialOutput,
  ]);
  fs.renameSync(partialOutput, output);
  progress(100, "Full episode ready");
} finally {
  cleanup();
}
