import { describe, it, expect, beforeEach } from "vitest";
import { mkdtempSync, writeFileSync, rmSync, mkdirSync } from "fs";
import { tmpdir } from "os";
import { join } from "path";

// Isolate PODCLI_HOME before the module (and its paths import) are evaluated.
const tmp = mkdtempSync(join(tmpdir(), "podcli-test-"));
process.env.PODCLI_HOME = tmp;
process.env.PODCLI_DATA = tmp;

const { ClipsHistory } = await import("./clips-history.js");

function makeFakeOutput(name: string): string {
  const p = join(tmp, name);
  writeFileSync(p, "stub");
  return p;
}

describe("ClipsHistory", () => {
  let history: InstanceType<typeof ClipsHistory>;

  beforeEach(() => {
    // Reset history dir for each test
    rmSync(join(tmp, "history"), { recursive: true, force: true });
    mkdirSync(join(tmp, "history"), { recursive: true });
    history = new ClipsHistory();
  });

  it("records and lists clips", async () => {
    await history.record({
      source_video: "/videos/show.mp4",
      output_path: makeFakeOutput("a.mp4"),
      start_second: 10,
      end_second: 40,
      caption_style: "karaoke",
      crop_strategy: "smart",
      title: "hook A",
    } as any);

    const list = await history.list();
    expect(list).toHaveLength(1);
    expect(list[0].title).toBe("hook A");
  });

  it("findDuplicate matches within ±2s tolerance", async () => {
    const output = makeFakeOutput("b.mp4");
    await history.record({
      source_video: "/videos/show.mp4",
      output_path: output,
      start_second: 10,
      end_second: 40,
      caption_style: "karaoke",
      crop_strategy: "smart",
      title: "original",
    } as any);

    const dup = await history.findDuplicate("/videos/show.mp4", 11, 41, "karaoke", "smart");
    expect(dup).not.toBeNull();
    expect(dup?.title).toBe("original");

    const miss = await history.findDuplicate("/videos/show.mp4", 15, 45, "karaoke", "smart");
    expect(miss).toBeNull();
  });

  it("findDuplicate ignores entries whose output file is missing", async () => {
    const output = makeFakeOutput("c.mp4");
    await history.record({
      source_video: "/videos/show.mp4",
      output_path: output,
      start_second: 100,
      end_second: 130,
      caption_style: "karaoke",
      crop_strategy: "smart",
      title: "ghost",
    } as any);

    rmSync(output);
    const dup = await history.findDuplicate("/videos/show.mp4", 100, 130, "karaoke", "smart");
    expect(dup).toBeNull();
  });

  it("recordBatchResults skips failed/output-less rows and applies defaults", async () => {
    const ok = makeFakeOutput("batch-ok.mp4");
    const recorded = await history.recordBatchResults(
      [
        { status: "success", output_path: ok, start_second: 5, end_second: 20, title: "kept" },
        { status: "error", error: "boom" },
        { status: "success", title: "no output" },
      ] as any,
      { sourceVideo: "/videos/show.mp4", defaultCaptionStyle: "hormozi", defaultCropStrategy: "speaker" },
    );

    expect(recorded).toHaveLength(1);
    expect(recorded[0].title).toBe("kept");
    expect(recorded[0].caption_style).toBe("hormozi");
    expect(recorded[0].crop_strategy).toBe("speaker");

    const list = await history.list();
    expect(list).toHaveLength(1);
  });

  it("recordBatchResults resolves content_type and transcript_slice per row", async () => {
    const ok = makeFakeOutput("batch-ct.mp4");
    const recorded = await history.recordBatchResults(
      [{ status: "success", output_path: ok, start_second: 0, end_second: 10, title: "ct" }] as any,
      {
        sourceVideo: "/videos/show.mp4",
        transcriptWords: [
          { word: "hello", start: 1, end: 2 },
          { word: "world", start: 3, end: 4 },
          { word: "later", start: 99, end: 100 },
        ] as any,
        contentTypeFor: (s, e) => (s === 0 && e === 10 ? "hook" : undefined),
      },
    );

    expect(recorded[0].content_type).toBe("hook");
    expect(recorded[0].transcript_slice).toBe("hello world");
  });

  it("recordBatchResults tolerates undefined results", async () => {
    const recorded = await history.recordBatchResults(undefined, { sourceVideo: "/videos/show.mp4" });
    expect(recorded).toEqual([]);
  });

  it("findDuplicate matches by basename, not full path", async () => {
    const output = makeFakeOutput("d.mp4");
    await history.record({
      source_video: "/absolute/path/one/show.mp4",
      output_path: output,
      start_second: 0,
      end_second: 30,
      caption_style: "karaoke",
      crop_strategy: "smart",
      title: "basename test",
    } as any);

    const dup = await history.findDuplicate("/different/path/show.mp4", 0, 30, "karaoke", "smart");
    expect(dup).not.toBeNull();
  });
});
