import fs from "fs";
import os from "os";
import path from "path";
import { fileURLToPath } from "url";
import { afterEach, describe, expect, it, vi } from "vitest";
import { bundle } from "@remotion/bundler";

vi.mock("@remotion/bundler", () => ({ bundle: vi.fn() }));

const __dirname = path.dirname(fileURLToPath(import.meta.url));

const savedCacheDir = process.env.PODCLI_CACHE_DIR;
const temporaryDirectories = [];

function deferred() {
  let resolve;
  const promise = new Promise((resolvePromise) => {
    resolve = resolvePromise;
  });
  return { promise, resolve };
}

afterEach(() => {
  vi.mocked(bundle).mockReset();
  vi.resetModules();
  if (savedCacheDir === undefined) delete process.env.PODCLI_CACHE_DIR;
  else process.env.PODCLI_CACHE_DIR = savedCacheDir;
  for (const directory of temporaryDirectories.splice(0)) {
    fs.rmSync(directory, { recursive: true, force: true });
  }
});

describe("bundle cache hash", () => {
  it("rebuilds when a file is added under remotion/public", async () => {
    const cacheRoot = fs.mkdtempSync(path.join(os.tmpdir(), "podcli-bundle-cache-"));
    temporaryDirectories.push(cacheRoot);
    process.env.PODCLI_CACHE_DIR = cacheRoot;

    vi.mocked(bundle).mockImplementation(async ({ outDir }) => {
      fs.mkdirSync(outDir, { recursive: true });
      fs.writeFileSync(path.join(outDir, "index.html"), "");
      return outDir;
    });

    const { getCachedBundle } = await import("./bundle-cache.mjs");
    await getCachedBundle();
    expect(bundle).toHaveBeenCalledTimes(1);

    await getCachedBundle();
    expect(bundle).toHaveBeenCalledTimes(1);

    const publicDir = path.join(__dirname, "public");
    fs.mkdirSync(publicDir, { recursive: true });
    const marker = path.join(publicDir, `.bundle-cache-test-${process.pid}`);
    fs.writeFileSync(marker, "new texture");
    try {
      await getCachedBundle();
      expect(bundle).toHaveBeenCalledTimes(2);
    } finally {
      fs.rmSync(marker, { force: true });
    }
  });
});

describe("bundle cache lock", () => {
  it("does not let a displaced owner release its successor's lock", async () => {
    const cacheRoot = fs.mkdtempSync(path.join(os.tmpdir(), "podcli-bundle-cache-"));
    temporaryDirectories.push(cacheRoot);
    process.env.PODCLI_CACHE_DIR = cacheRoot;

    const pendingBundles = [];
    vi.mocked(bundle).mockImplementation(async ({ outDir }) => {
      const completion = deferred();
      pendingBundles.push(completion);
      await completion.promise;
      fs.mkdirSync(outDir, { recursive: true });
      fs.writeFileSync(path.join(outDir, "index.html"), "");
      return outDir;
    });

    const { CACHE_DIR, getCachedBundle } = await import("./bundle-cache.mjs");
    const lockDir = `${CACHE_DIR}.lock`;
    const first = getCachedBundle();
    await vi.waitFor(() => expect(pendingBundles).toHaveLength(1));

    const staleTime = new Date(Date.now() - 10 * 60 * 1000);
    for (const entry of fs.readdirSync(lockDir)) {
      fs.utimesSync(path.join(lockDir, entry), staleTime, staleTime);
    }
    fs.utimesSync(lockDir, staleTime, staleTime);

    const second = getCachedBundle();
    await vi.waitFor(() => expect(pendingBundles).toHaveLength(2));
    const replacementTokens = fs.readdirSync(lockDir);
    expect(replacementTokens).toHaveLength(1);

    pendingBundles[0].resolve();
    await expect(first).rejects.toThrow("Bundle cache lock ownership lost");
    expect(fs.readdirSync(lockDir)).toEqual(replacementTokens);

    pendingBundles[1].resolve();
    await second;
    expect(fs.existsSync(lockDir)).toBe(false);
  });
});
