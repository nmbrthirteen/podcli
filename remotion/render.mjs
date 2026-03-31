#!/usr/bin/env node
/**
 * Remotion render script — called by Python backend.
 *
 * Caches the bundle to .podcli/cache/remotion-bundle/ so subsequent renders
 * skip the ~15-20s bundling step.
 *
 * Usage:
 *   node remotion/render.mjs \
 *     --video /path/to/cropped.mp4 \
 *     --words /path/to/words.json \
 *     --style branded \
 *     --output /path/to/captioned.mp4 \
 *     [--logo /path/to/logo.png] \
 *     [--fps 30]
 *
 *   node remotion/render.mjs --prebundle   # Bundle only, no render
 */

import { bundle } from "@remotion/bundler";
import { renderMedia, selectComposition } from "@remotion/renderer";
import path from "path";
import fs from "fs";
import os from "os";
import crypto from "crypto";
import { fileURLToPath } from "url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const PROJECT_ROOT = path.resolve(__dirname, "..");
const CACHE_DIR = path.join(PROJECT_ROOT, ".podcli", "cache", "remotion-bundle");
const BUNDLE_HASH_FILE = path.join(CACHE_DIR, ".hash");
const ENTRY_POINT = path.join(__dirname, "src", "index.ts");

function parseArgs() {
  const args = process.argv.slice(2);
  const opts = {};
  for (let i = 0; i < args.length; i++) {
    if (args[i] === "--prebundle") {
      opts.prebundle = true;
      continue;
    }
    if (args[i].startsWith("--") && i + 1 < args.length) {
      opts[args[i].replace(/^--/, "")] = args[i + 1];
      i++;
    }
  }
  return opts;
}

/**
 * Hash the remotion/src/ directory to detect changes.
 */
function hashSrcDir() {
  const srcDir = path.join(__dirname, "src");
  const hash = crypto.createHash("md5");

  function walk(dir) {
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
      const full = path.join(dir, entry.name);
      if (entry.isDirectory()) {
        walk(full);
      } else if (entry.isFile()) {
        hash.update(fs.readFileSync(full));
      }
    }
  }

  walk(srcDir);
  return hash.digest("hex");
}

/**
 * Get or create a cached bundle. Only re-bundles when src/ changes.
 */
async function getCachedBundle() {
  const currentHash = hashSrcDir();

  // Check if cached bundle is still valid
  if (fs.existsSync(BUNDLE_HASH_FILE)) {
    const cachedHash = fs.readFileSync(BUNDLE_HASH_FILE, "utf-8").trim();
    const bundleIndex = path.join(CACHE_DIR, "index.html");
    if (cachedHash === currentHash && fs.existsSync(bundleIndex)) {
      return CACHE_DIR;
    }
  }

  // Bundle fresh
  console.log("  Remotion: bundling (first run or src changed)...");
  fs.mkdirSync(CACHE_DIR, { recursive: true });

  const bundleLocation = await bundle({
    entryPoint: ENTRY_POINT,
    outDir: CACHE_DIR,
  });

  // Save hash
  fs.writeFileSync(BUNDLE_HASH_FILE, currentHash);
  return bundleLocation;
}

async function main() {
  const opts = parseArgs();

  // Prebundle mode — just bundle and exit
  if (opts.prebundle) {
    const t0 = Date.now();
    const loc = await getCachedBundle();
    console.log(`Remotion bundle ready at ${loc} (${Date.now() - t0}ms)`);
    return;
  }

  if (!opts.video || !opts.words || !opts.output) {
    console.error(
      "Usage: node render.mjs --video <path> --words <path> --style <name> --output <path>"
    );
    process.exit(1);
  }

  const wordsData = JSON.parse(fs.readFileSync(opts.words, "utf-8"));
  // Support both old format (array) and new format ({words, faceY})
  const words = Array.isArray(wordsData) ? wordsData : wordsData.words || [];
  const faceY = Array.isArray(wordsData) ? null : wordsData.faceY ?? null;
  const styleName = opts.style || "branded";
  const fps = parseInt(opts.fps || "30", 10);

  // Get cached bundle first (needed to copy assets into it)
  const t0 = Date.now();
  const bundleLocation = await getCachedBundle();
  const bundleMs = Date.now() - t0;
  if (bundleMs > 1000) {
    console.log(`  bundled in ${(bundleMs / 1000).toFixed(1)}s`);
  }

  // Serve video and logo via a tiny local HTTP server
  // (Remotion's bundled server can't serve dynamically added files)
  const http = await import("http");
  const assetServer = http.createServer((req, res) => {
    let filePath = null;
    if (req.url === "/clip.mp4") filePath = path.resolve(opts.video);
    else if (req.url === "/logo.png" && opts.logo) filePath = path.resolve(opts.logo);

    if (filePath && fs.existsSync(filePath)) {
      const stat = fs.statSync(filePath);
      const ext = path.extname(filePath).slice(1);
      const mime = { mp4: "video/mp4", png: "image/png", jpg: "image/jpeg", webp: "image/webp" }[ext] || "application/octet-stream";
      res.writeHead(200, { "Content-Type": mime, "Content-Length": stat.size, "Access-Control-Allow-Origin": "*" });
      fs.createReadStream(filePath).pipe(res);
    } else {
      res.writeHead(404);
      res.end();
    }
  });
  await new Promise((resolve) => assetServer.listen(0, "127.0.0.1", resolve));
  const assetPort = assetServer.address().port;

  const videoSrc = `http://127.0.0.1:${assetPort}/clip.mp4`;
  const logoSrc = opts.logo ? `http://127.0.0.1:${assetPort}/logo.png` : undefined;

  // Probe video dimensions and duration
  let renderW = 1080;
  let renderH = 1920;
  let videoDuration = null;
  try {
    const { execSync } = await import("child_process");
    const probe = execSync(
      `ffprobe -v error -select_streams v:0 -show_entries stream=width,height -of csv=s=x:p=0 "${path.resolve(opts.video)}"`,
      { encoding: "utf-8", timeout: 5000 }
    ).trim();
    const [w, h] = probe.split("x").map(Number);
    if (w > 0 && h > 0) {
      renderW = w;
      renderH = h;
    }
    const durStr = execSync(
      `ffprobe -v error -show_entries format=duration -of default=noprint_wrappers=1:nokey=1 "${path.resolve(opts.video)}"`,
      { encoding: "utf-8", timeout: 5000 }
    ).trim();
    videoDuration = parseFloat(durStr);
  } catch {}

  // Calculate duration from video or word timing
  const lastWord = words[words.length - 1];
  const durationSec = videoDuration || (lastWord ? lastWord.end + 0.5 : 30);
  const durationInFrames = Math.ceil(durationSec * fps);

  const inputProps = {
    videoSrc,
    words,
    styleName,
    logoSrc,
    faceY,
    durationInFrames,
    fps,
  };

  console.log(
    `Remotion: ${words.length} words, ${styleName}, ${renderW}x${renderH}, ${durationInFrames}f`
  );

  // Select composition
  const composition = await selectComposition({
    serveUrl: bundleLocation,
    id: "CaptionedClip",
    inputProps,
  });

  // Render captions as transparent WebM overlay (no video = 10x faster)
  const cpus = os.cpus().length;
  const concurrency = Math.max(2, Math.min(cpus, 8));
  // Store overlay in /tmp to survive temp dir cleanup
  const overlayId = path.basename(opts.output, ".mp4");
  const captionOverlay = path.join(os.tmpdir(), `remotion_overlay_${overlayId}.mov`);

  let lastPct = -1;
  await renderMedia({
    composition: {
      ...composition,
      durationInFrames,
      fps,
      width: renderW,
      height: renderH,
    },
    serveUrl: bundleLocation,
    codec: "prores",
    proResProfile: "4444",
    pixelFormat: "yuva444p10le",
    imageFormat: "png",
    outputLocation: captionOverlay,
    inputProps,
    concurrency,
    onProgress: ({ progress }) => {
      const pct = Math.round(progress * 100);
      if (pct > lastPct + 9) {
        lastPct = pct;
        process.stderr.write(`  captions: ${pct}%\n`);
      }
    },
  });

  assetServer.close();

  // Composite: overlay transparent captions (ProRes 4444 with alpha) onto video
  const { execSync } = await import("child_process");
  process.stderr.write("  compositing...\n");
  execSync(
    `ffmpeg -y -hide_banner -loglevel warning -i "${path.resolve(opts.video)}" -i "${captionOverlay}" ` +
    `-filter_complex "[0:v][1:v]overlay=0:0:shortest=1" ` +
    `-c:v libx264 -crf 18 -preset fast -map 0:a -c:a copy "${opts.output}"`,
    { stdio: ["pipe", "pipe", "pipe"], timeout: 300000 }
  );

  // Clean up overlay
  try { fs.unlinkSync(captionOverlay); } catch {}
  console.log(`Done: ${opts.output}`);
}

main().catch((err) => {
  console.error("Remotion render error:", err.message);
  process.exit(1);
});
