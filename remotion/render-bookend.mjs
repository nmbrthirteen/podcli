#!/usr/bin/env node
/**
 * Render an intro or outro "bookend" card via Remotion, with a silent audio
 * track so it concatenates cleanly with the main clip.
 *
 * Usage:
 *   node remotion/render-bookend.mjs \
 *     --mode outro \
 *     --title "Follow for more" \
 *     --handle "@yourbrand" \
 *     --platforms tiktok,instagram,youtube,x \
 *     --seconds 3 \
 *     --output /path/to/outro.mp4 \
 *     [--bg "#0B0B0F"] [--accent "#FFE000"] [--fps 30] [--width 1080] [--height 1920]
 */

import { bundle } from "@remotion/bundler";
import { renderMedia, selectComposition } from "@remotion/renderer";
import path from "path";
import fs from "fs";
import os from "os";
import crypto from "crypto";
import { execSync } from "child_process";
import { fileURLToPath } from "url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const PROJECT_ROOT = path.resolve(__dirname, "..");
const CACHE_DIR = path.join(PROJECT_ROOT, ".podcli", "cache", "remotion-bundle");
const ENTRY_POINT = path.join(__dirname, "src", "index.ts");

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

async function getBundle() {
  // Reuse podcli's cached bundle if present; otherwise build fresh.
  if (fs.existsSync(path.join(CACHE_DIR, "index.html"))) return CACHE_DIR;
  return await bundle({ entryPoint: ENTRY_POINT, outDir: CACHE_DIR });
}

async function main() {
  const opts = parseArgs();
  if (!opts.mode || !opts.output) {
    console.error("Usage: render-bookend.mjs --mode intro|outro --title <t> --output <path> [--handle --platforms --seconds --bg --accent]");
    process.exit(1);
  }

  const fps = parseInt(opts.fps || "30", 10);
  const width = parseInt(opts.width || "1080", 10);
  const height = parseInt(opts.height || "1920", 10);
  const seconds = parseFloat(opts.seconds || "3");
  const durationInFrames = Math.round(seconds * fps);
  const platforms = (opts.platforms || "tiktok,instagram,youtube,x")
    .split(",").map((s) => s.trim()).filter(Boolean);

  const inputProps = {
    bookendMode: opts.mode,
    bookendTitle: opts.title || (opts.mode === "outro" ? "Follow for more" : ""),
    bookendHandle: opts.handle || undefined,
    bookendPlatforms: platforms,
    bookendBg: opts.bg || "#0B0B0F",
    bookendAccent: opts.accent || "#FFE000",
  };

  const bundleLocation = await getBundle();
  const composition = await selectComposition({ serveUrl: bundleLocation, id: "Bookend", inputProps });

  const seed = `${path.resolve(opts.output)}:${process.pid}`;
  const id = crypto.createHash("md5").update(seed).digest("hex").slice(0, 12);
  const silentVideo = path.join(os.tmpdir(), `bookend_${id}.mp4`);

  console.log(`Bookend: ${opts.mode}, "${inputProps.bookendTitle}", ${platforms.join("+")}, ${durationInFrames}f`);

  await renderMedia({
    composition: { ...composition, durationInFrames, fps, width, height },
    serveUrl: bundleLocation,
    codec: "h264",
    outputLocation: silentVideo,
    inputProps,
    crf: 16,
    concurrency: Math.max(2, Math.min(os.cpus().length, 8)),
  });

  // Add a silent stereo audio track so concat/crossfade with the main clip
  // (which has audio) doesn't fail on a missing audio stream.
  execSync(
    `ffmpeg -y -i "${silentVideo}" -f lavfi -i anullsrc=channel_layout=stereo:sample_rate=44100 ` +
    `-c:v copy -c:a aac -b:a 192k -ar 44100 -shortest "${path.resolve(opts.output)}"`,
    { stdio: "ignore", timeout: 60000 }
  );
  try { fs.unlinkSync(silentVideo); } catch {}

  console.log(`OK ${opts.output}`);
}

main().catch((e) => { console.error("Bookend render failed:", e?.message || e); process.exit(1); });
