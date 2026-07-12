import { bundle } from "@remotion/bundler";
import path from "path";
import fs from "fs";
import crypto from "crypto";
import { fileURLToPath } from "url";
import { webpackOverride } from "./webpack-override.mjs";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const PROJECT_ROOT = path.resolve(__dirname, "..");
const CACHE_ROOT = process.env.PODCLI_CACHE_DIR
  ? path.resolve(process.env.PODCLI_CACHE_DIR)
  : path.join(PROJECT_ROOT, "data", "cache");

export const CACHE_DIR = path.join(CACHE_ROOT, "remotion-bundle");
const HASH_FILE = path.join(CACHE_DIR, ".hash");
const ENTRY_POINT = path.join(__dirname, "src", "index.ts");

/**
 * A bundle is a product of the compositions and the webpack config, so both
 * feed the cache key. Keying on either alone silently serves a bundle built
 * from inputs that no longer exist.
 */
function currentHash() {
  const hash = crypto.createHash("md5");

  function walk(dir) {
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
      const full = path.join(dir, entry.name);
      if (entry.isDirectory()) walk(full);
      else if (entry.isFile()) hash.update(fs.readFileSync(full));
    }
  }

  walk(path.join(__dirname, "src"));
  hash.update(fs.readFileSync(path.join(__dirname, "webpack-override.mjs")));
  return hash.digest("hex");
}

export async function getCachedBundle({ onBundle } = {}) {
  const want = currentHash();
  if (fs.existsSync(HASH_FILE) && fs.existsSync(path.join(CACHE_DIR, "index.html"))) {
    if (fs.readFileSync(HASH_FILE, "utf-8").trim() === want) return CACHE_DIR;
  }

  onBundle?.();
  fs.mkdirSync(CACHE_DIR, { recursive: true });
  const location = await bundle({
    entryPoint: ENTRY_POINT,
    outDir: CACHE_DIR,
    webpackOverride,
  });
  fs.writeFileSync(HASH_FILE, want);
  return location;
}
