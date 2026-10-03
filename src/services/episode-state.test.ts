import { describe, it, expect, beforeAll, afterAll, afterEach } from "vitest";
import { mkdtempSync, rmSync, writeFileSync } from "fs";
import { tmpdir } from "os";
import { join } from "path";

const tmp = mkdtempSync(join(tmpdir(), "podcli-episode-state-"));
const savedHome = process.env.PODCLI_HOME;

let EpisodeState: typeof import("./episode-state.js").EpisodeState;
let videoA: string;
let videoB: string;

beforeAll(async () => {
  process.env.PODCLI_HOME = join(tmp, "home");
  ({ EpisodeState } = await import("./episode-state.js"));
  videoA = join(tmp, "episode-a.mp4");
  videoB = join(tmp, "episode-b.mp4");
  writeFileSync(videoA, "a".repeat(100));
  writeFileSync(videoB, "b".repeat(200));
});

afterAll(() => {
  if (savedHome === undefined) delete process.env.PODCLI_HOME;
  else process.env.PODCLI_HOME = savedHome;
  rmSync(tmp, { recursive: true, force: true });
});

afterEach(() => {
  rmSync(join(tmp, "home", "episode-decisions.json"), { force: true });
});

describe("EpisodeState", () => {
  it("has no decisions for a video that was never recorded", async () => {
    const state = new EpisodeState();
    expect(await state.get(videoA)).toBeNull();
  });

  it("records and reads back decisions for one video", async () => {
    const state = new EpisodeState();
    await state.record(videoA, { clipCount: 5, captionStyle: "hormozi" });
    const got = await state.get(videoA);
    expect(got?.clipCount).toBe(5);
    expect(got?.captionStyle).toBe("hormozi");
  });

  it("merges new decisions without clearing previously recorded ones", async () => {
    const state = new EpisodeState();
    await state.record(videoA, { clipCount: 5 });
    await state.record(videoA, { captionsEnabled: true });
    const got = await state.get(videoA);
    expect(got?.clipCount).toBe(5);
    expect(got?.captionsEnabled).toBe(true);
  });

  it("keeps two videos' decisions independent, keyed by path and size", async () => {
    const state = new EpisodeState();
    await state.record(videoA, { clipCount: 5 });
    await state.record(videoB, { clipCount: 9 });
    expect((await state.get(videoA))?.clipCount).toBe(5);
    expect((await state.get(videoB))?.clipCount).toBe(9);
  });

  it("lists every decision as open before anything is recorded", async () => {
    const state = new EpisodeState();
    const open = await state.openQuestions(videoA);
    expect(open.length).toBeGreaterThan(0);
    expect(open.map((q) => q.field)).toContain("clipCount");
  });

  it("drops a field from open questions once it's answered", async () => {
    const state = new EpisodeState();
    await state.record(videoA, { clipCount: 5 });
    const open = await state.openQuestions(videoA);
    expect(open.map((q) => q.field)).not.toContain("clipCount");
    expect(open.map((q) => q.field)).toContain("captionStyle");
  });

  it("treats a video that doesn't exist on disk as having no key", async () => {
    const state = new EpisodeState();
    expect(await state.keyFor(join(tmp, "nope.mp4"))).toBeNull();
    expect(await state.get(join(tmp, "nope.mp4"))).toBeNull();
  });
});
