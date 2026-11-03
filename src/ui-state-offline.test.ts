import { describe, it, expect, beforeAll, afterAll, afterEach } from "vitest";
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "fs";
import { tmpdir } from "os";
import { join } from "path";
import { callTool } from "./test-support/mcp-client.js";

// get_ui_state used to report "Web UI is not running" whenever the Web UI
// process was down, even though the same state it would have served lives on
// disk at paths.uiState. These tests pin the fallback: offline reads should
// come from the state file, and the "no session yet" message should only
// appear when there is truly nothing to read.

const tmp = mkdtempSync(join(tmpdir(), "podcli-ui-state-offline-"));
const savedHome = process.env.PODCLI_HOME;
const savedData = process.env.PODCLI_DATA;
const savedFetch = globalThis.fetch;

let createServer: typeof import("./server.js").createServer;
let uiStatePath: string;

beforeAll(async () => {
  process.env.PODCLI_HOME = join(tmp, "home");
  process.env.PODCLI_DATA = join(tmp, "data");
  const { paths } = await import("./config/paths.js");
  uiStatePath = paths.uiState;
  mkdirSync(paths.home, { recursive: true });
  ({ createServer } = await import("./server.js"));
});

afterAll(() => {
  if (savedHome === undefined) delete process.env.PODCLI_HOME;
  else process.env.PODCLI_HOME = savedHome;

  if (savedData === undefined) delete process.env.PODCLI_DATA;
  else process.env.PODCLI_DATA = savedData;
  rmSync(tmp, { recursive: true, force: true });
});

afterEach(() => {
  globalThis.fetch = savedFetch;
  rmSync(uiStatePath, { force: true });
});

const getUiState = (include_transcript: boolean) => callTool(createServer(), "get_ui_state", { include_transcript });

function stubWebUiDown() {
  globalThis.fetch = () => Promise.reject(new Error("fetch failed"));
}

describe("get_ui_state offline fallback", () => {
  it("reads session state from disk when the Web UI is not running", async () => {
    writeFileSync(
      uiStatePath,
      JSON.stringify({
        videoPath: "/videos/ep42.mp4",
        phase: "suggesting",
        settings: { captionStyle: "hormozi", cropStrategy: "speaker" },
        suggestions: [],
        deselectedIndices: [],
        transcript: { words: ["a", "b", "c"] },
      }),
    );
    stubWebUiDown();

    const { text } = await getUiState(false);

    expect(text).toContain("/videos/ep42.mp4");
    expect(text).toContain("Transcript: 3 words");
    expect(text).not.toContain("Web UI is not running");
  });

  it("falls back to start-from-scratch guidance when there is no state anywhere", async () => {
    stubWebUiDown();

    const { text } = await getUiState(false);

    expect(text).toContain("Start from scratch");
  });
});
