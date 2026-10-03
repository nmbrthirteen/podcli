import { describe, it, expect, beforeAll, afterAll } from "vitest";
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "fs";
import { tmpdir } from "os";
import { join } from "path";

// record_decisions is the MCP way to answer the per-episode open questions
// (clip count, caption style, language, ...) once instead of every run.
// These tests exercise the registered tool end to end against a real
// EpisodeState file, not a mock, so a refactor that breaks the wiring
// between the tool and the service shows up here.

const tmp = mkdtempSync(join(tmpdir(), "podcli-record-decisions-"));
const savedHome = process.env.PODCLI_HOME;
const savedData = process.env.PODCLI_DATA;
const savedPort = process.env.PODCLI_PORT;

let createServer: typeof import("./server.js").createServer;
let recordDecisionsInputSchema: typeof import("./server.js").recordDecisionsInputSchema;
let videoPath: string;

beforeAll(async () => {
  process.env.PODCLI_HOME = join(tmp, "home");
  process.env.PODCLI_DATA = join(tmp, "data");
  // Force readUIState to fall back to the on-disk ui-state.json instead of
  // reaching a real studio server that happens to be running on the default
  // port during local development.
  process.env.PODCLI_PORT = "18423";
  mkdirSync(process.env.PODCLI_HOME, { recursive: true });
  ({ createServer, recordDecisionsInputSchema } = await import("./server.js"));
  videoPath = join(tmp, "episode.mp4");
  writeFileSync(videoPath, "fake video bytes");
});

afterAll(() => {
  if (savedHome === undefined) delete process.env.PODCLI_HOME;
  else process.env.PODCLI_HOME = savedHome;
  if (savedPort === undefined) delete process.env.PODCLI_PORT;
  else process.env.PODCLI_PORT = savedPort;
  if (savedData === undefined) delete process.env.PODCLI_DATA;
  else process.env.PODCLI_DATA = savedData;
  rmSync(tmp, { recursive: true, force: true });
});

function getHandler(name: string) {
  const server = createServer() as unknown as {
    _registeredTools: Record<string, { handler: (args: unknown, extra: unknown) => Promise<{ content: { text: string }[] }> }>;
  };
  return server._registeredTools[name].handler;
}

describe("record_decisions", () => {
  it("records a decision and reports what's still open", async () => {
    const handler = getHandler("record_decisions");
    const result = await handler({ video_path: videoPath, clip_count: 5 }, {});
    const text = result.content[0].text;
    expect(text).toContain("clipCount");
    expect(text).toContain("Still open");
  });

  it("reports every decision answered once all fields are recorded", async () => {
    const handler = getHandler("record_decisions");
    await handler(
      {
        video_path: videoPath,
        clip_count: 5,
        clip_duration_min: 20,
        clip_duration_max: 45,
        caption_style: "hormozi",
        captions_enabled: true,
        language: "en",
        thumbnails_wanted: true,
        delivery_target: "youtube_shorts",
      },
      {},
    );
    const result = await handler({ video_path: videoPath, notes: "final note" }, {});
    expect(result.content[0].text).toContain("All episode decisions answered.");
  });

  it("rejects a call with no fields to record", async () => {
    const handler = getHandler("record_decisions");
    const result = await handler({ video_path: videoPath }, {});
    expect(result.content[0].text).toContain("No decisions provided");
  });

  it("keys decisions on the pre-silence-removal video, not its derivative", async () => {
    // A silence-removal pass rewrites the working video to a new path with a
    // different size, which would otherwise orphan decisions already recorded
    // against the original (path+size+mtime no longer matches).
    const originalPath = join(tmp, "original.mp4");
    const derivativePath = join(tmp, "original.silence-removed.mp4");
    writeFileSync(originalPath, "original bytes");
    writeFileSync(derivativePath, "a shorter derivative");

    writeFileSync(
      join(process.env.PODCLI_HOME!, "ui-state.json"),
      JSON.stringify({
        videoPath: derivativePath,
        silenceOriginal: { videoPath: originalPath, transcript: null },
      }),
    );

    const handler = getHandler("record_decisions");
    await handler({ video_path: derivativePath, clip_count: 7 }, {});

    const { EpisodeState } = await import("./services/episode-state.js");
    const state = new EpisodeState();
    expect((await state.get(originalPath))?.clipCount).toBe(7);
    expect(await state.get(derivativePath)).toBeNull();
  });
});

describe("recordDecisionsInputSchema", () => {
  // An unrecognized key like captions (instead of captions_enabled) must be
  // a schema error naming the valid fields, not silently stripped and
  // recorded as nothing, which is what z.object's default behavior does.
  it("rejects an unknown field and names the valid fields in the error", () => {
    const result = recordDecisionsInputSchema.safeParse({ video_path: videoPath, captions: true });
    expect(result.success).toBe(false);
    if (!result.success) {
      const message = result.error.issues[0].message;
      expect(message).toContain("captions");
      expect(message).toContain("captions_enabled");
      expect(message).toContain("clip_count");
    }
  });

  it("accepts a call with only recognized fields", () => {
    const result = recordDecisionsInputSchema.safeParse({ video_path: videoPath, clip_count: 5 });
    expect(result.success).toBe(true);
  });
});

describe("record_decisions duration range", () => {
  it("stores only the side given, never a 0 for the missing one", async () => {
    const { EpisodeState } = await import("./services/episode-state.js");
    const fresh = join(tmp, "fresh-episode.mp4");
    writeFileSync(fresh, "another fake video");
    const handler = getHandler("record_decisions");
    await handler({ video_path: fresh, clip_duration_min: 30 }, {});
    expect((await new EpisodeState().get(fresh))?.clipDurationRange).toEqual({ min: 30 });
    await handler({ video_path: fresh, clip_duration_max: 60 }, {});
    expect((await new EpisodeState().get(fresh))?.clipDurationRange).toEqual({ min: 30, max: 60 });
  });
});
