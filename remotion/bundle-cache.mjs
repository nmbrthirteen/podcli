import { bundle } from "@remotion/bundler";
import path from "path";
import fs from "fs";
import crypto from "crypto";
import { createRequire } from "module";
import { fileURLToPath } from "url";
import { webpackOverride } from "./webpack-override.mjs";

const require = createRequire(import.meta.url);

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const PROJECT_ROOT = path.resolve(__dirname, "..");
const CACHE_ROOT = process.env.PODCLI_CACHE_DIR
  ? path.resolve(process.env.PODCLI_CACHE_DIR)
  : path.join(PROJECT_ROOT, "data", "cache");

export const CACHE_DIR = path.join(CACHE_ROOT, "remotion-bundle");
const HASH_FILE = path.join(CACHE_DIR, ".hash");
const LOCK_DIR = `${CACHE_DIR}.lock`;
const LOCK_STALE_MS = 5 * 60 * 1000;
const ENTRY_POINT = path.join(__dirname, "src", "index.ts");

/**
 * A bundle is a product of the compositions, the webpack config, and the
 * remotion/bundler versions that produced it, so all of them feed the cache
 * key. Keying on a subset silently serves a bundle built from inputs that no
 * longer exist.
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
  hash.update(`remotion@${require("remotion/package.json").version}`);
  hash.update(`@remotion/bundler@${require("@remotion/bundler/package.json").version}`);
  return hash.digest("hex");
}

function cacheIsValid(want) {
  try {
    return (
      fs.existsSync(path.join(CACHE_DIR, "index.html")) &&
      fs.readFileSync(HASH_FILE, "utf-8").trim() === want
    );
  } catch {
    return false;
  }
}

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function acquireLock() {
  for (;;) {
    try {
      fs.mkdirSync(LOCK_DIR);
      return;
    } catch (err) {
      if (err.code !== "EEXIST") throw err;
    }
    try {
      if (Date.now() - fs.statSync(LOCK_DIR).mtimeMs > LOCK_STALE_MS) {
        fs.rmdirSync(LOCK_DIR);
        continue;
      }
    } catch {}
    await sleep(500);
  }
}

function releaseLock() {
  try {
    fs.rmdirSync(LOCK_DIR);
  } catch {}
}

export async function getCachedBundle({ onBundle } = {}) {
  const want = currentHash();
  if (cacheIsValid(want)) return CACHE_DIR;

  fs.mkdirSync(CACHE_ROOT, { recursive: true });
  await acquireLock();
  try {
    if (cacheIsValid(want)) return CACHE_DIR;

    onBundle?.();
    // Bundle into a scratch dir and swap it in whole, so a crash mid-bundle
    // never leaves CACHE_DIR half-written.
    const staging = `${CACHE_DIR}.tmp-${process.pid}`;
    fs.rmSync(staging, { recursive: true, force: true });
    try {
      await bundle({
        entryPoint: ENTRY_POINT,
        outDir: staging,
        webpackOverride,
      });
      fs.writeFileSync(path.join(staging, ".hash"), want);
      fs.rmSync(CACHE_DIR, { recursive: true, force: true });
      fs.renameSync(staging, CACHE_DIR);
    } finally {
      fs.rmSync(staging, { recursive: true, force: true });
    }
    return CACHE_DIR;
  } finally {
    releaseLock();
  }
}
