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

  const words = JSON.parse(fs.readFileSync(opts.words, "utf-8"));
  const styleName = opts.style || "branded";
  const fps = parseInt(opts.fps || "30", 10);

  // Calculate duration from video words
  const lastWord = words[words.length - 1];
  const durationSec = lastWord ? lastWord.end + 0.5 : 30;
  const durationInFrames = Math.ceil(durationSec * fps);

  // Get cached bundle first (needed to copy assets into it)
  const t0 = Date.now();
  const bundleLocation = await getCachedBundle();
  const bundleMs = Date.now() - t0;
  if (bundleMs > 1000) {
    console.log(`  bundled in ${(bundleMs / 1000).toFixed(1)}s`);
  }

  // Symlink video into bundle's public dir so Remotion's server can serve it
  const publicDir = path.join(bundleLocation, "public");
  fs.mkdirSync(publicDir, { recursive: true });

  const videoExt = path.extname(opts.video);
  const videoDest = path.join(publicDir, `clip${videoExt}`);
  try { fs.unlinkSync(videoDest); } catch {}
  fs.symlinkSync(path.resolve(opts.video), videoDest);
  const videoSrc = `clip${videoExt}`;

  // Copy logo into bundle's public dir
  let logoSrc;
  if (opts.logo && fs.existsSync(opts.logo)) {
    const logoExt = path.extname(opts.logo);
    fs.copyFileSync(path.resolve(opts.logo), path.join(publicDir, `logo${logoExt}`));
    logoSrc = `logo${logoExt}`;
  }

  const inputProps = {
    videoSrc,
    words,
    styleName,
    logoSrc,
    durationInFrames,
    fps,
  };

  console.log(
    `Remotion: ${words.length} words, ${styleName}, ${durationInFrames}f @ ${fps}fps`
  );

  // Select composition
  const composition = await selectComposition({
    serveUrl: bundleLocation,
    id: "CaptionedClip",
    inputProps,
  });

  // Render
  await renderMedia({
    composition: {
      ...composition,
      durationInFrames,
      fps,
      width: 2160,
      height: 3840,
    },
    serveUrl: bundleLocation,
    codec: "h264",
    outputLocation: opts.output,
    inputProps,
    onProgress: ({ progress }) => {
      process.stderr.write(
        `  rendering: ${Math.round(progress * 100)}%\r`
      );
    },
  });

  console.log(`\nDone: ${opts.output}`);
}

main().catch((err) => {
  console.error("Remotion render error:", err.message);
  process.exit(1);
});
