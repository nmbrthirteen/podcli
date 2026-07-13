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
const LOCK_HEARTBEAT_MS = Math.floor(LOCK_STALE_MS / 3);
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

function removeStaleLock() {
  try {
    const entries = fs.readdirSync(LOCK_DIR, { withFileTypes: true });
    if (entries.length === 0) {
      if (Date.now() - fs.statSync(LOCK_DIR).mtimeMs > LOCK_STALE_MS) {
        fs.rmdirSync(LOCK_DIR);
        return true;
      }
      return false;
    }

    for (const entry of entries) {
      if (!entry.isFile()) continue;
      const tokenPath = path.join(LOCK_DIR, entry.name);
      if (Date.now() - fs.statSync(tokenPath).mtimeMs > LOCK_STALE_MS) {
        fs.rmSync(tokenPath, { force: true });
      }
    }
    fs.rmdirSync(LOCK_DIR);
    return true;
  } catch {
    return false;
  }
}

async function acquireLock() {
  const token = `${process.pid}-${crypto.randomUUID()}`;
  for (;;) {
    try {
      fs.mkdirSync(LOCK_DIR);
      const tokenPath = path.join(LOCK_DIR, token);
      try {
        fs.writeFileSync(tokenPath, token, { flag: "wx" });
      } catch (err) {
        try {
          fs.rmdirSync(LOCK_DIR);
        } catch {}
        throw err;
      }
      const lock = { token, tokenPath, heartbeat: undefined };
      lock.heartbeat = setInterval(() => {
        try {
          const now = new Date();
          fs.utimesSync(tokenPath, now, now);
        } catch {
          clearInterval(lock.heartbeat);
        }
      }, LOCK_HEARTBEAT_MS);
      lock.heartbeat.unref();
      return lock;
    } catch (err) {
      if (err.code !== "EEXIST") throw err;
    }
    if (removeStaleLock()) continue;
    await sleep(500);
  }
}

function releaseLock(lock) {
  clearInterval(lock.heartbeat);
  try {
    fs.rmSync(lock.tokenPath, { force: true });
  } catch {}
  try {
    fs.rmdirSync(LOCK_DIR);
  } catch {}
}

function ownsLock(lock) {
  return fs.existsSync(lock.tokenPath);
}

export async function getCachedBundle({ onBundle } = {}) {
  const want = currentHash();
  if (cacheIsValid(want)) return CACHE_DIR;

  fs.mkdirSync(CACHE_ROOT, { recursive: true });
  const lock = await acquireLock();
  try {
    if (cacheIsValid(want)) return CACHE_DIR;

    onBundle?.();
    // Bundle into a scratch dir and swap it in whole, so a crash mid-bundle
    // never leaves CACHE_DIR half-written.
    const staging = `${CACHE_DIR}.tmp-${lock.token}`;
    fs.rmSync(staging, { recursive: true, force: true });
    try {
      await bundle({
        entryPoint: ENTRY_POINT,
        outDir: staging,
        webpackOverride,
      });
      if (!ownsLock(lock)) {
        throw new Error("Bundle cache lock ownership lost");
      }
      fs.writeFileSync(path.join(staging, ".hash"), want);
      fs.rmSync(CACHE_DIR, { recursive: true, force: true });
      fs.renameSync(staging, CACHE_DIR);
    } finally {
      fs.rmSync(staging, { recursive: true, force: true });
    }
    return CACHE_DIR;
  } finally {
    releaseLock(lock);
  }
}
