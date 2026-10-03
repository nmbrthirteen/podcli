import { describe, it, expect, beforeAll, afterAll } from "vitest";
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "fs";
import { tmpdir } from "os";
import { join } from "path";

// create_clip/batch_create_clips fill transcript_words from session state
// when the caller omits it (e.g. clip_number-only calls). handleCreateClip
// and handleBatchClips only run the "transcript belongs to a different
// video" guard when transcript_words arrives null, so filling it from state
// in server.ts has to run that guard itself. Otherwise a render against a
// video that was swapped in after the transcript was generated goes through
// unchecked. These tests never need the studio server: the check runs
// before server.ts even tries to reach it.

const tmp = mkdtempSync(join(tmpdir(), "podcli-offline-identity-"));
const savedHome = process.env.PODCLI_HOME;
const savedData = process.env.PODCLI_DATA;
const savedPort = process.env.PODCLI_PORT;

let createServer: typeof import("./server.js").createServer;
let videoPath: string;
let uiStatePath: string;

beforeAll(async () => {
  process.env.PODCLI_HOME = join(tmp, "home");
  process.env.PODCLI_DATA = join(tmp, "data");
  process.env.PODCLI_PORT = "18425"; // force readUIState to fall back to disk
  mkdirSync(process.env.PODCLI_HOME, { recursive: true });
  uiStatePath = join(process.env.PODCLI_HOME, "ui-state.json");

  ({ createServer } = await import("./server.js"));
  videoPath = join(tmp, "episode.mp4");
  writeFileSync(videoPath, "fake video bytes");
});

afterAll(() => {
  if (savedHome === undefined) delete process.env.PODCLI_HOME;
  else process.env.PODCLI_HOME = savedHome;
  if (savedData === undefined) delete process.env.PODCLI_DATA;
  else process.env.PODCLI_DATA = savedData;
  if (savedPort === undefined) delete process.env.PODCLI_PORT;
  else process.env.PODCLI_PORT = savedPort;
  rmSync(tmp, { recursive: true, force: true });
});

function getHandler(name: string) {
  const server = createServer() as unknown as {
    _registeredTools: Record<string, { handler: (args: unknown, extra: unknown) => Promise<{ content: { text: string }[]; isError?: boolean }> }>;
  };
  return server._registeredTools[name].handler;
}

function writeMismatchedState() {
  writeFileSync(
    uiStatePath,
    JSON.stringify({
      videoPath,
      filePath: videoPath,
      suggestions: [{ start_second: 10, end_second: 20, title: "a moment" }],
      deselectedIndices: [],
      transcript: { words: [{ word: "hi", start: 0, end: 1 }] },
      // Deliberately wrong size so it never matches the real file on disk.
      transcriptVideoIdentity: { path: videoPath, size: 999999, mtimeMs: 1 },
    }),
  );
}

describe("create_clip offline identity guard", () => {
  it("rejects clip_number-only calls when the session transcript doesn't match the video", async () => {
    writeMismatchedState();
    const handler = getHandler("create_clip");
    const result = await handler({ clip_number: 1 }, {});
    expect(result.content[0].text).toContain("belongs to a different video");
  });
});

describe("batch_create_clips offline identity guard", () => {
  it("rejects export_selected calls when the session transcript doesn't match the video", async () => {
    writeMismatchedState();
    const handler = getHandler("batch_create_clips");
    const result = await handler({ export_selected: true }, {});
    expect(result.content[0].text).toContain("belongs to a different video");
  });
});
