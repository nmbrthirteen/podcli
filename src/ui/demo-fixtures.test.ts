import { describe, it, expect, afterEach } from "vitest";
import { existsSync } from "fs";
import { demoClips, DEMO_ASSETS_DIR } from "./demo-fixtures.js";

describe("demo fixtures", () => {
  it("exposes clips that light up the library and analytics", () => {
    const clips = demoClips();
    expect(clips.length).toBeGreaterThanOrEqual(6);
    for (const c of clips) {
      expect(c.id).toBeTruthy();
      expect(c.title).toBeTruthy();
      expect(c.thumbnail_config?.preview_path).toContain("demo-assets");
      // Analytics counts "published" only from clips with view/retention metrics.
      expect(c.metrics?.views).toBeGreaterThan(0);
      expect(c.metrics?.retention).toBeGreaterThan(0);
    }
  });

  it("groups into more than one episode", () => {
    const sources = new Set(demoClips().map((c) => c.source_video));
    expect(sources.size).toBeGreaterThan(1);
  });

  it("ships a thumbnail png for every clip", () => {
    for (const c of demoClips()) {
      expect(existsSync(c.thumbnail_config!.preview_path!)).toBe(true);
    }
    expect(existsSync(DEMO_ASSETS_DIR)).toBe(true);
  });
});

describe("ClipsHistory in demo mode", () => {
  afterEach(() => { delete process.env.PODCLI_DEMO; });

  it("serves fixtures and never persists writes", async () => {
    const { ClipsHistory } = await import("../services/clips-history.js");
    process.env.PODCLI_DEMO = "1";
    const history = new ClipsHistory();

    const listed = await history.list(500);
    expect(listed.length).toBe(demoClips().length);

    const first = listed[0];
    // A remove must not throw or delete shipped artifacts; it just echoes the entry.
    const removed = await history.remove(first.id);
    expect(removed?.id).toBe(first.id);
    expect(existsSync(first.thumbnail_config!.preview_path!)).toBe(true);
    // Still all there — the delete was a no-op against read-only fixtures.
    expect((await history.list(500)).length).toBe(demoClips().length);
  });
});
