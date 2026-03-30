#!/usr/bin/env node
/**
 * Quick test render — generates a captioned clip to ~/Downloads/
 * Uses the same caching as production.
 *
 * Usage:
 *   node remotion/test-render.mjs                  # branded (default)
 *   node remotion/test-render.mjs hormozi           # test specific style
 *   node remotion/test-render.mjs karaoke
 *   node remotion/test-render.mjs subtle
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

function hashSrcDir() {
  const srcDir = path.join(__dirname, "src");
  const hash = crypto.createHash("md5");
  function walk(dir) {
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
      const full = path.join(dir, entry.name);
      if (entry.isDirectory()) walk(full);
      else if (entry.isFile()) hash.update(fs.readFileSync(full));
    }
  }
  walk(srcDir);
  return hash.digest("hex");
}

async function getCachedBundle() {
  const currentHash = hashSrcDir();
  if (fs.existsSync(BUNDLE_HASH_FILE)) {
    const cachedHash = fs.readFileSync(BUNDLE_HASH_FILE, "utf-8").trim();
    if (cachedHash === currentHash && fs.existsSync(path.join(CACHE_DIR, "index.html"))) {
      return CACHE_DIR;
    }
  }
  console.log("Bundling (first run or src changed)...");
  fs.mkdirSync(CACHE_DIR, { recursive: true });
  const loc = await bundle({ entryPoint: ENTRY_POINT, outDir: CACHE_DIR });
  fs.writeFileSync(BUNDLE_HASH_FILE, currentHash);
  return loc;
}

const styleName = process.argv[2] || "branded";

const words = [
  { word: "and", start: 0.0, end: 0.3 },
  { word: "make", start: 0.3, end: 0.6 },
  { word: "film", start: 0.6, end: 0.9 },
  { word: "the", start: 0.9, end: 1.1 },
  { word: "world's", start: 1.1, end: 1.5 },
  { word: "first", start: 1.5, end: 1.9 },
  { word: "bricks", start: 1.9, end: 2.3 },
  { word: "being", start: 2.5, end: 2.9 },
  { word: "made.", start: 2.9, end: 3.4 },
  { word: "It's", start: 3.6, end: 3.9 },
  { word: "absolutely", start: 3.9, end: 4.5 },
  { word: "incredible", start: 4.5, end: 5.0 },
];

const fps = 30;
const durationInFrames = Math.ceil(5.5 * fps);
const outputPath = path.join(process.env.HOME, "Downloads", `remotion-test-${styleName}.mp4`);

// Try to load logo from podcli asset registry
let logoPath = "";
try {
  const registry = JSON.parse(fs.readFileSync(path.join(PROJECT_ROOT, ".podcli", "assets", "registry.json"), "utf-8"));
  const logo = registry.assets?.find((a) => a.type === "logo");
  if (logo?.path && fs.existsSync(logo.path)) logoPath = logo.path;
} catch {}

let logoSrc;

const inputProps = {
  videoSrc: "",
  words,
  styleName,
  durationInFrames,
  fps,
};

async function main() {
  const t0 = Date.now();
  const bundleLocation = await getCachedBundle();
  const bundleMs = Date.now() - t0;
  console.log(`Bundle: ${bundleMs}ms (${bundleMs < 100 ? "cached" : "fresh"})`);

  // Copy logo into bundle dir
  if (fs.existsSync(logoPath)) {
    fs.copyFileSync(logoPath, path.join(bundleLocation, "logo.png"));
    inputProps.logoSrc = "logo.png";
  }

  const composition = await selectComposition({
    serveUrl: bundleLocation,
    id: "CaptionedClip",
    inputProps,
  });

  console.log(`Rendering ${styleName} → ${outputPath}`);
  await renderMedia({
    composition: { ...composition, durationInFrames, fps, width: 2160, height: 3840 },
    serveUrl: bundleLocation,
    codec: "h264",
    outputLocation: outputPath,
    inputProps,
    onProgress: ({ progress }) => {
      process.stderr.write(`  ${Math.round(progress * 100)}%\r`);
    },
  });

  console.log(`\nDone: ${outputPath}`);
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
