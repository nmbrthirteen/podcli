import { describe, it, expect, beforeAll, afterAll, vi } from "vitest";
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "fs";
import { tmpdir } from "os";
import { join } from "path";

// A re-render reuses its output path, so a "_clean.mp4" variant from a
// previous render that this pass doesn't produce could sit next to the new
// main clip. /api/outputs used to list every "*_clean.mp4" as its own
// unrelated-looking clip instead of folding it into the main clip's entry.

const tmp = mkdtempSync(join(tmpdir(), "podcli-outputs-"));
const savedEnv: Record<string, string | undefined> = {
  PODCLI_HOME: process.env.PODCLI_HOME,
  PODCLI_DATA: process.env.PODCLI_DATA,
  PODCLI_OUTPUT: process.env.PODCLI_OUTPUT,
  PODCLI_PORT: process.env.PODCLI_PORT,
};

const PORT = 18426;

vi.mock("../services/python-executor.js", async (importOriginal) => {
  const original = await importOriginal<typeof import("../services/python-executor.js")>();
  class StubExecutor {
    async execute() {
      return { data: {} };
    }
  }
  return { ...original, PythonExecutor: StubExecutor };
});

let outputDir: string;

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
  outputDir = join(tmp, "data", "output");
  process.env.PODCLI_OUTPUT = outputDir;
  process.env.PODCLI_PORT = String(PORT);
  mkdirSync(process.env.PODCLI_HOME, { recursive: true });
  mkdirSync(outputDir, { recursive: true });

  writeFileSync(join(outputDir, "clip-a.mp4"), "main clip bytes");
  writeFileSync(join(outputDir, "clip-a_clean.mp4"), "clean variant bytes");
  writeFileSync(join(outputDir, "clip-b.mp4"), "a second clip with no clean variant");

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

describe("GET /api/outputs", () => {
  it("folds a clip's _clean.mp4 variant into its own entry instead of listing it separately", async () => {
    const res = await fetch(`http://127.0.0.1:${PORT}/api/outputs`);
    const clips = (await res.json()) as Array<{ filename: string; clean_output_path?: string }>;

    const filenames = clips.map((c) => c.filename);
    expect(filenames).toContain("clip-a.mp4");
    expect(filenames).toContain("clip-b.mp4");
    expect(filenames).not.toContain("clip-a_clean.mp4");

    const clipA = clips.find((c) => c.filename === "clip-a.mp4");
    expect(clipA?.clean_output_path).toBe(join(outputDir, "clip-a_clean.mp4"));

    const clipB = clips.find((c) => c.filename === "clip-b.mp4");
    expect(clipB?.clean_output_path).toBeUndefined();
  });
});
