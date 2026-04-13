import { describe, it, expect, beforeEach } from "vitest";
import { mkdtempSync, writeFileSync, rmSync, mkdirSync } from "fs";
import { tmpdir } from "os";
import { join } from "path";
import type { TranscriptResult } from "../models/index.js";

const tmp = mkdtempSync(join(tmpdir(), "podcli-cache-test-"));
process.env.PODCLI_HOME = tmp;
process.env.PODCLI_DATA = tmp;

const { TranscriptCache } = await import("./transcript-cache.js");

function makeFakeVideo(name: string, content: string): string {
  const p = join(tmp, name);
  writeFileSync(p, content);
  return p;
}

const fakeTranscript: TranscriptResult = {
  transcript: "hello world",
  segments: [],
  words: [
    { word: "hello", start: 0, end: 0.5, confidence: 0.99 },
    { word: "world", start: 0.5, end: 1.0, confidence: 0.99 },
  ],
  duration: 1.0,
  language: "en",
  speakers: { num_speakers: 0, speakers: {} },
  speaker_segments: [],
};

describe("TranscriptCache", () => {
  let cache: InstanceType<typeof TranscriptCache>;

  beforeEach(() => {
    rmSync(join(tmp, "cache"), { recursive: true, force: true });
    mkdirSync(join(tmp, "cache", "transcripts"), { recursive: true });
    cache = new TranscriptCache();
  });

  it("returns null for an uncached file", async () => {
    const file = makeFakeVideo("uncached.mp4", "fresh content");
    expect(await cache.get(file)).toBeNull();
  });

  it("set then get round-trips the transcript", async () => {
    const file = makeFakeVideo("cached.mp4", "stable content here");
    await cache.set(file, fakeTranscript);
    const loaded = await cache.get(file);
    expect(loaded).not.toBeNull();
    expect(loaded?.transcript).toBe("hello world");
    expect(loaded?.words).toHaveLength(2);
  });

  it("hashes the file content — different files get different cache keys", async () => {
    const a = makeFakeVideo("a.mp4", "content A");
    const b = makeFakeVideo("b.mp4", "content B");
    await cache.set(a, { ...fakeTranscript, transcript: "A" });
    await cache.set(b, { ...fakeTranscript, transcript: "B" });
    expect((await cache.get(a))?.transcript).toBe("A");
    expect((await cache.get(b))?.transcript).toBe("B");
  });

  it("getFileHash is stable for identical content", async () => {
    const a = makeFakeVideo("first.mp4", "identical bytes");
    const b = makeFakeVideo("second.mp4", "identical bytes");
    const hashA = await cache.getFileHash(a);
    const hashB = await cache.getFileHash(b);
    expect(hashA).toBe(hashB);
  });

  it("get returns null when the cache file is corrupt", async () => {
    const file = makeFakeVideo("corrupt-source.mp4", "any content");
    const hash = await cache.getFileHash(file);
    writeFileSync(join(tmp, "cache", "transcripts", `${hash}.json`), "this is not json");
    expect(await cache.get(file)).toBeNull();
  });
});
