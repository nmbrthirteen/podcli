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

let createServer: typeof import("./server.js").createServer;
let videoPath: string;

beforeAll(async () => {
  process.env.PODCLI_HOME = join(tmp, "home");
  process.env.PODCLI_DATA = join(tmp, "data");
  mkdirSync(process.env.PODCLI_HOME, { recursive: true });
  ({ createServer } = await import("./server.js"));
  videoPath = join(tmp, "episode.mp4");
  writeFileSync(videoPath, "fake video bytes");
});

afterAll(() => {
  if (savedHome === undefined) delete process.env.PODCLI_HOME;
  else process.env.PODCLI_HOME = savedHome;
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
});
