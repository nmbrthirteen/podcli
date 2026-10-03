import { describe, it, expect, beforeAll, afterAll, vi } from "vitest";
import { mkdirSync, mkdtempSync, rmSync } from "fs";
import { tmpdir } from "os";
import { join } from "path";

// The studio's "_source: 'ui'" suggestions merge in POST /api/ui-state
// restores a clip's segments from server state when the studio's edit
// omitted them (so a stale array scoped to the old range doesn't override
// the new range), but it carried the clip's *old* duration forward verbatim
// instead of recomputing it, and never ran the "this no longer matches what
// was selected" hash check that modify_clip's own endpoint runs. A studio
// edit after approval could silently export something other than what was
// approved, and adding a hook to an approved clip reported a duration that
// excluded it.

const tmp = mkdtempSync(join(tmpdir(), "podcli-studio-edit-"));
const savedEnv: Record<string, string | undefined> = {
  PODCLI_HOME: process.env.PODCLI_HOME,
  PODCLI_DATA: process.env.PODCLI_DATA,
  PODCLI_PORT: process.env.PODCLI_PORT,
};

const PORT = 18427;

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

beforeAll(async () => {
  process.env.PODCLI_HOME = join(tmp, "home");
  process.env.PODCLI_DATA = join(tmp, "data");
  process.env.PODCLI_PORT = String(PORT);
  mkdirSync(process.env.PODCLI_HOME, { recursive: true });

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

const baseClip = {
  clip_id: "c1",
  title: "clip",
  start_second: 10,
  end_second: 30,
  duration: 20,
  reasoning: "",
  preview_text: "",
  segments: [{ start: 10, end: 30 }],
};

describe("POST /api/ui-state studio suggestions merge", () => {
  it("flags changedSinceSelection and recomputes duration to include a newly added hook", async () => {
    // Seed one suggestion from the MCP side.
    await fetch(`http://127.0.0.1:${PORT}/api/ui-state`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ suggestions: [baseClip] }),
    });

    // Approve it: stamps a selectionHash, same as the studio's "select" action.
    await fetch(`http://127.0.0.1:${PORT}/api/suggestions/modify`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action: "toggle", index: 0, selected: true }),
    });

    // The studio edits the clip (adds a 3s hook) and syncs back without
    // segments, as it does after a server round trip strips them.
    const studioUpdate = {
      ...baseClip,
      segments: undefined,
      hook: { start: 12, end: 15, mode: "repeat" },
    };
    await fetch(`http://127.0.0.1:${PORT}/api/ui-state`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ _source: "ui", suggestions: [studioUpdate] }),
    });

    const state = await (await fetch(`http://127.0.0.1:${PORT}/api/ui-state`)).json();
    const clip = state.suggestions[0];

    // Body (20s) + hook (3s) played first, repeat mode.
    expect(clip.duration).toBeCloseTo(23, 1);
    expect(clip.changedSinceSelection).toBe(true);
  });
});
