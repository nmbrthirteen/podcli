#!/usr/bin/env node
/**
 * Render an audiogram: captions and a moving waveform over the show's artwork,
 * for an episode that has no picture of its own.
 *
 * The video is rendered silent and the window's audio muxed on afterwards, the
 * same way render-bookend.mjs does it. Remotion could take the audio itself,
 * but that means decoding the whole source inside the render for a window a
 * single ffmpeg copy can cut out.
 *
 * Usage:
 *   node remotion/render-audiogram.mjs \
 *     --props /path/to/props.json \
 *     --audio /path/to/episode.mp3 \
 *     --start 12.5 --end 45.0 \
 *     --output /path/to/clip.mp4 \
 *     [--fps 30] [--width 1080] [--height 1920]
 *
 * The props file carries words, style, levels, colours, cover and title. It is
 * a file rather than a flag because the level data is one row per frame and
 * would not survive an argv limit.
 */

import { renderMedia, selectComposition } from "@remotion/renderer";

// Same reason as remotion/render.mjs: the 30s default is measured against
// the font delayRender competing with the whole bundle.
const DELAY_RENDER_TIMEOUT_MS = 120_000;
import { getCachedBundle } from "./bundle-cache.mjs";
import path from "path";
import fs from "fs";
import os from "os";
import crypto from "crypto";
import { spawnSync } from "child_process";

function parseArgs() {
  const args = process.argv.slice(2);
  const opts = {};
  for (let i = 0; i < args.length; i++) {
    if (args[i].startsWith("--") && i + 1 < args.length) {
      opts[args[i].replace(/^--/, "")] = args[i + 1];
      i++;
    }
  }
  return opts;
}

async function main() {
  const opts = parseArgs();
  if (!opts.props || !opts.output || !opts.audio) {
    console.error(
      "Usage: render-audiogram.mjs --props <json> --audio <file> --start <s> --end <s> --output <path>",
    );
    process.exit(1);
  }

  const props = JSON.parse(fs.readFileSync(opts.props, "utf-8"));
  const fps = parseInt(opts.fps || "30", 10);
  const width = parseInt(opts.width || "1080", 10);
  const height = parseInt(opts.height || "1920", 10);
  const start = parseFloat(opts.start || "0");
  const end = parseFloat(opts.end || "0");
  const seconds = Math.max(0.1, end - start);

  // The levels decide the length: they were computed for this window at this
  // frame rate, so trusting them keeps the bars in step with the audio rather
  // than drifting a frame at a time.
  const durationInFrames = props.levels?.length
    ? props.levels.length
    : Math.round(seconds * fps);

  const inputProps = {
    words: props.words || [],
    levels: props.levels || [],
    audiogramBg: props.bg || "#0B0B0F",
    audiogramAccent: props.accent || "#FFE000",
    coverSrc: props.coverSrc,
    audiogramTitle: props.title,
    styleName: props.styleName || "hormozi",
    singleLine: props.singleLine === true,
  };

  const bundleLocation = await getCachedBundle({
    onBundle: () => console.log("  Remotion: bundling (first run, or src/config changed)..."),
  });
  const composition = await selectComposition({
    timeoutInMilliseconds: DELAY_RENDER_TIMEOUT_MS,
    serveUrl: bundleLocation,
    id: "Audiogram",
    inputProps,
  });

  const seed = `${path.resolve(opts.output)}:${process.pid}`;
  const id = crypto.createHash("md5").update(seed).digest("hex").slice(0, 12);
  const silentVideo = path.join(os.tmpdir(), `audiogram_${id}.mp4`);

  console.log(
    `Audiogram: ${durationInFrames}f @ ${fps}fps, ${width}x${height}, ` +
      `${inputProps.levels.length ? inputProps.levels[0].length : 0} bars`,
  );

  await renderMedia({
    timeoutInMilliseconds: DELAY_RENDER_TIMEOUT_MS,
    composition: { ...composition, durationInFrames, fps, width, height },
    serveUrl: bundleLocation,
    codec: "h264",
    outputLocation: silentVideo,
    inputProps,
    crf: 18,
    concurrency: Math.max(2, Math.min(os.cpus().length, 8)),
  });

  const ffmpeg = process.env.PODCLI_FFMPEG || "ffmpeg";
  const mux = spawnSync(
    ffmpeg,
    [
      "-y",
      "-i", silentVideo,
      "-ss", String(start),
      "-t", String(seconds),
      "-i", opts.audio,
      "-map", "0:v:0",
      "-map", "1:a:0",
      "-c:v", "copy",
      "-c:a", "aac",
      "-b:a", "192k",
      "-ar", "44100",
      "-ac", "2",
      "-shortest",
      "-movflags", "+faststart",
      opts.output,
    ],
    { encoding: "utf-8" },
  );
  try {
    fs.unlinkSync(silentVideo);
  } catch {
    // A leftover temp file is not worth failing a finished render over.
  }
  if (mux.status !== 0 || !fs.existsSync(opts.output)) {
    console.error(`Audio mux failed:\n${(mux.stderr || "").slice(-800)}`);
    process.exit(1);
  }

  console.log(`  ✓ ${opts.output}`);
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
