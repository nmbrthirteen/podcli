import { describe, it, expect, beforeAll, afterAll, vi } from "vitest";
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "fs";
import { tmpdir } from "os";
import { join } from "path";

// Loading persisted state at startup used to wipe the transcript and
// suggestions whenever the saved videoPath didn't exist on disk (e.g. an
// external drive is unmounted), losing a whole session for something that
// might just be temporarily unreachable. It should keep them and flag the
// video as missing instead.

const tmp = mkdtempSync(join(tmpdir(), "podcli-video-missing-"));
const savedEnv: Record<string, string | undefined> = {
  PODCLI_HOME: process.env.PODCLI_HOME,
  PODCLI_DATA: process.env.PODCLI_DATA,
  PODCLI_PORT: process.env.PODCLI_PORT,
};

const PORT = 18428;

vi.mock("../services/python-executor.js", async (importOriginal) => {
  const original = await importOriginal<typeof import("../services/python-executor.js")>();
  class StubExecutor {
    async execute() {
      return { data: {} };
    }
  }
  return { ...original, PythonExecutor: StubExecutor };
});

async function waitForServer(): Promise<void> {
  for (let i = 0; i < 50; i++) {
    try {
      const res = await fetch(`http://127.0.0.1:${PORT}/api/ui-state`);
      if (res.ok) return;
    } catch {
      // not up yet
    }
    await new Promise((r) => setTimeout(r, 100));
  }
  throw new Error("web server never came up");
}

const missingVideoPath = join(tmp, "unmounted-drive", "episode.mp4");

beforeAll(async () => {
  process.env.PODCLI_HOME = join(tmp, "home");
  process.env.PODCLI_DATA = join(tmp, "data");
  process.env.PODCLI_PORT = String(PORT);
  mkdirSync(process.env.PODCLI_HOME, { recursive: true });

  writeFileSync(
    join(process.env.PODCLI_HOME, "ui-state.json"),
    JSON.stringify({
      videoPath: missingVideoPath,
      filePath: missingVideoPath,
      transcript: { words: [{ word: "hi", start: 0, end: 1 }] },
      suggestions: [
        { clip_id: "c1", title: "a moment", start_second: 0, end_second: 10, duration: 10, reasoning: "", preview_text: "" },
      ],
      deselectedIndices: [],
    }),
  );

  await import("./web-server.js");
  await waitForServer();
}, 20_000);

afterAll(() => {
  for (const [key, value] of Object.entries(savedEnv)) {
    if (value === undefined) delete process.env[key];
    else process.env[key] = value;
  }
  rmSync(tmp, { recursive: true, force: true });
});

describe("loading persisted state with a missing video", () => {
  it("keeps the transcript and suggestions and flags videoMissing instead of wiping them", async () => {
    const state = await (await fetch(`http://127.0.0.1:${PORT}/api/ui-state`)).json();
    expect(state.videoMissing).toBe(true);
    expect(state.transcriptWordCount).toBe(1);
    expect(state.transcript?.words?.[0]?.word).toBe("hi");
    expect(state.suggestions).toHaveLength(1);
    expect(state.suggestions[0].title).toBe("a moment");
  });
});
