import { describe, it, expect, beforeAll, afterAll } from "vitest";
import { mkdtempSync, rmSync } from "fs";
import { tmpdir } from "os";
import { join, resolve } from "path";

const tmp = mkdtempSync(join(tmpdir(), "podcli-paths-"));
const savedHome = process.env.PODCLI_HOME;
const savedData = process.env.PODCLI_DATA;

let paths: typeof import("./paths.js").paths;

beforeAll(async () => {
  process.env.PODCLI_HOME = join(tmp, "config-home");
  process.env.PODCLI_DATA = join(tmp, "data-root");
  ({ paths } = await import("./paths.js"));
});

afterAll(() => {
  if (savedHome === undefined) delete process.env.PODCLI_HOME;
  else process.env.PODCLI_HOME = savedHome;
  if (savedData === undefined) delete process.env.PODCLI_DATA;
  else process.env.PODCLI_DATA = savedData;
  rmSync(tmp, { recursive: true, force: true });
});

describe("paths", () => {
  it("resolves PODCLI_HOME and PODCLI_DATA independently", () => {
    expect(paths.home).toBe(resolve(tmp, "config-home"));
    expect(paths.dataDir).toBe(resolve(tmp, "data-root"));
    expect(paths.cache).toBe(resolve(tmp, "data-root", "cache"));
    expect(paths.transcripts).toBe(resolve(tmp, "data-root", "cache", "transcripts"));
    expect(paths.knowledge).toBe(resolve(tmp, "config-home", "knowledge"));
    expect(paths.integrations).toBe(resolve(tmp, "config-home", "integrations.json"));
  });

  it("keeps profile marker at project root", () => {
    expect(paths.homeMarker).toBe(join(paths.projectRoot, ".podcli-home"));
  });
});
