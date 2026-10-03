import { describe, it, expect, beforeEach } from "vitest";
import { mkdtempSync, writeFileSync, rmSync, mkdirSync } from "fs";
import { tmpdir } from "os";
import { join } from "path";
import type { TranscriptResult } from "../models/index.js";

const tmp = mkdtempSync(join(tmpdir(), "podcli-cache-test-"));
process.env.PODCLI_HOME = tmp;
process.env.PODCLI_DATA = tmp;

const { TranscriptCache, hasSpeakerLabels, needsDiarizationRetry } = await import(
  "./transcript-cache.js"
);

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

  it("reads packed markdown with the engine suffix", async () => {
    const file = makeFakeVideo("assemblyai.mp4", "same media");
    const hash = await cache.getFileHashForEngine(file, "assemblyai");
    mkdirSync(join(tmp, "packed"), { recursive: true });
    writeFileSync(join(tmp, "packed", `${hash}.md`), "# Packed AssemblyAI");
    expect(await cache.getPackedMarkdown(file, "assemblyai")).toBe("# Packed AssemblyAI");
    expect(await cache.getPackedMarkdown(file)).toBeNull();
  });

  it("keys the packed view by model and language too, not engine alone", async () => {
    const file = makeFakeVideo("packed-multi-key.mp4", "packed multi key");
    mkdirSync(join(tmp, "packed"), { recursive: true });
    const base = await cache.getFileHashForEngine(file, { engine: "whispercpp" });
    const small = await cache.getFileHashForEngine(file, {
      engine: "whispercpp",
      model: "small",
      language: "ka",
    });
    writeFileSync(join(tmp, "packed", `${base}.md`), "# base");
    writeFileSync(join(tmp, "packed", `${small}.md`), "# small-ka");
    expect(await cache.getPackedMarkdown(file, { engine: "whispercpp" })).toBe("# base");
    expect(
      await cache.getPackedMarkdown(file, { engine: "whispercpp", model: "small", language: "ka" }),
    ).toBe("# small-ka");
    // A combo that was never written must miss, not fall back to either.
    expect(
      await cache.getPackedMarkdown(file, { engine: "whispercpp", model: "medium", language: "fr" }),
    ).toBeNull();
  });

  it("falls back to the most recently modified packed view when the caller doesn't know model/language", async () => {
    const file = makeFakeVideo("packed-unknown-key.mp4", "packed unknown key");
    mkdirSync(join(tmp, "packed"), { recursive: true });
    const first = await cache.getFileHashForEngine(file, { engine: "whispercpp", model: "small" });
    writeFileSync(join(tmp, "packed", `${first}.md`), "# first");
    await new Promise((r) => setTimeout(r, 5));
    const second = await cache.getFileHashForEngine(file, {
      engine: "whispercpp",
      model: "medium",
      language: "ka",
    });
    writeFileSync(join(tmp, "packed", `${second}.md`), "# second");
    // Caller only knows the engine, not which model/language actually ran.
    // Deterministic rule: most recently written entry for this hash+engine.
    expect(await cache.getPackedMarkdown(file, { engine: "whispercpp" })).toBe("# second");
  });

  it("get returns null when the cache file is corrupt", async () => {
    const file = makeFakeVideo("corrupt-source.mp4", "any content");
    const hash = await cache.getFileHash(file);
    writeFileSync(join(tmp, "cache", "transcripts", `${hash}.json`), "this is not json");
    expect(await cache.get(file)).toBeNull();
  });

  it("keys the raw cache by engine, model and language, not engine alone", async () => {
    const file = makeFakeVideo("multi-key.mp4", "same media, different requests");
    await cache.set(file, { ...fakeTranscript, transcript: "base-auto" }, {
      engine: "whispercpp",
    });
    await cache.set(file, { ...fakeTranscript, transcript: "small-ka" }, {
      engine: "whispercpp",
      model: "small",
      language: "ka",
    });
    expect((await cache.get(file, { engine: "whispercpp" }))?.transcript).toBe("base-auto");
    expect(
      (await cache.get(file, { engine: "whispercpp", model: "small", language: "ka" }))
        ?.transcript,
    ).toBe("small-ka");
    // A request for a third, never-written combo must miss, not fall back to
    // either of the above.
    expect(
      await cache.get(file, { engine: "whispercpp", model: "medium", language: "fr" }),
    ).toBeNull();
  });

  it("treats base model and auto language as no suffix, for backward compatibility", async () => {
    const file = makeFakeVideo("default-key.mp4", "legacy cache shape");
    // A plain string engine (the pre-existing call shape) must land on the
    // same key as the equivalent object form with default model/language.
    await cache.set(file, { ...fakeTranscript, transcript: "legacy" }, "whispercpp");
    expect(
      (await cache.get(file, { engine: "whispercpp", model: "base", language: "auto" }))
        ?.transcript,
    ).toBe("legacy");
  });

  it("sanitizes a language tag to [a-z0-9-], rejecting path separators and odd casing", async () => {
    const file = makeFakeVideo("lang-sanitize.mp4", "language sanitize check");
    await cache.set(file, { ...fakeTranscript, transcript: "danger" }, {
      language: "../../etc",
    });
    // The sanitized form keys the lookup: "../../etc" strips to "etc".
    expect(
      (await cache.get(file, { language: "etc" }))?.transcript,
    ).toBe("danger");
    const { readdirSync } = await import("fs");
    const files = readdirSync(join(tmp, "cache", "transcripts"));
    expect(files.every((f) => !f.includes("..") && !f.includes("/"))).toBe(true);
  });

  it("treats mixed-case language tags the same as their lowercase form", async () => {
    const file = makeFakeVideo("lang-case.mp4", "case check");
    await cache.set(file, { ...fakeTranscript, transcript: "georgian" }, { language: "KA" });
    expect((await cache.get(file, { language: "ka" }))?.transcript).toBe("georgian");
  });

  it("writes atomically: no temp file left behind, and no partial reads", async () => {
    const file = makeFakeVideo("atomic.mp4", "atomic write check");
    await cache.set(file, fakeTranscript);
    const { readdirSync } = await import("fs");
    const files = readdirSync(join(tmp, "cache", "transcripts"));
    expect(files.some((f) => f.endsWith(".tmp"))).toBe(false);
  });
});


describe("hasSpeakerLabels", () => {
  it("accepts a transcript with speaker segments", () => {
    expect(hasSpeakerLabels({ speaker_segments: [{ speaker: "S0", start: 0, end: 1 }] })).toBe(true);
  });

  it("accepts a transcript whose words carry speakers", () => {
    expect(hasSpeakerLabels({ words: [{ word: "hi", speaker: "SPEAKER_00" }] })).toBe(true);
  });

  it("rejects a count with no labels behind it", () => {
    // The packer emits "S?" on every line for this shape, so serving it back
    // to a request for speakers would make re-transcribing a no-op.
    expect(
      hasSpeakerLabels({ speakers: { num_speakers: 2 }, speaker_segments: [], words: [] }),
    ).toBe(false);
  });

  it("rejects the shape a whisper.cpp run leaves behind", () => {
    expect(
      hasSpeakerLabels({
        speakers: { num_speakers: 0 },
        speaker_segments: [],
        words: [{ word: "hi", speaker: null }],
      }),
    ).toBe(false);
  });

  it("rejects missing and empty input", () => {
    expect(hasSpeakerLabels(null)).toBe(false);
    expect(hasSpeakerLabels({})).toBe(false);
  });
});

describe("needsDiarizationRetry", () => {
  const unlabelled = { diarization_attempted: false, words: [] };
  const attemptedButNoneFound = { diarization_attempted: true, words: [] };

  it("is false when diarization wasn't requested", () => {
    expect(needsDiarizationRetry(unlabelled, false, true)).toBe(false);
  });

  it("is false when the resolved engine can never diarize, no matter the flag", () => {
    // whisper.cpp/omnilingual will never gain labels, so retrying forever
    // would be the bug this flag exists to prevent.
    expect(needsDiarizationRetry(unlabelled, true, false)).toBe(false);
  });

  it("is true when requested, possible, and never attempted", () => {
    expect(needsDiarizationRetry(unlabelled, true, true)).toBe(true);
  });

  it("is false once diarization was attempted, even with no labels to show for it", () => {
    expect(needsDiarizationRetry(attemptedButNoneFound, true, true)).toBe(false);
  });

  it("is false for missing cache input", () => {
    expect(needsDiarizationRetry(null, true, true)).toBe(false);
  });
});
