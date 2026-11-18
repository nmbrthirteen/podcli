import { describe, expect, it } from "vitest";
import { advanceProgress, summarizeBatchOutcome, tagSubmittedClip, tagSubmittedClips } from "./clip-results.js";
import type { BatchClipsResult } from "../models/index.js";

const specs = [
  { start_second: 120, end_second: 165 },
  { start_second: 640, end_second: 700 },
];

describe("tagSubmittedClip", () => {
  it("stamps the bounds of the clip that was submitted at that index", () => {
    const row = tagSubmittedClip(
      { clip_index: 1, status: "success", output_path: "/out/b.mp4" },
      specs,
    );

    expect(row.source_start_second).toBe(640);
    expect(row.source_end_second).toBe(700);
  });

  // The renderer trims weak openings, so the row's own start_second is the
  // rendered start, not the requested one.
  it("keeps the submitted bounds even when the render moved start_second", () => {
    const row = tagSubmittedClip(
      { clip_index: 0, status: "success", start_second: 123.4, end_second: 165 },
      specs,
    );

    expect(row.start_second).toBe(123.4);
    expect(row.source_start_second).toBe(120);
  });

  it("leaves rows alone when there is no matching spec", () => {
    expect(tagSubmittedClip({ status: "success" }, specs).source_start_second).toBeUndefined();
    expect(tagSubmittedClip({ clip_index: 9, status: "success" }, specs).source_start_second).toBeUndefined();
    expect(tagSubmittedClip({ clip_index: 0, status: "success" }).source_start_second).toBeUndefined();
  });

  it("stamps error rows too", () => {
    const row = tagSubmittedClip(
      { clip_index: 1, status: "error", error: "ffmpeg died" },
      specs,
    );

    expect(row.source_start_second).toBe(640);
  });
});

describe("tagSubmittedClips", () => {
  it("stamps every row of the final result", () => {
    const data: BatchClipsResult = {
      total_clips: 2,
      successful_clips: 1,
      results: [
        { clip_index: 0, status: "success", output_path: "/out/a.mp4" },
        { clip_index: 1, status: "error", error: "boom" },
      ],
    };

    const tagged = tagSubmittedClips(data, specs);
    expect(tagged?.results.map((r) => r.source_start_second)).toEqual([120, 640]);
  });

  it("passes undefined data through", () => {
    expect(tagSubmittedClips(undefined, specs)).toBeUndefined();
  });
});

describe("advanceProgress", () => {
  // Parallel workers each report their own share, so percentages arrive out of order.
  it("never lets progress run backwards", () => {
    const job = { progress: 0 };
    expect(advanceProgress(job, 50)).toBe(50);
    expect(advanceProgress(job, 12)).toBe(50);
    expect(advanceProgress(job, 75)).toBe(75);
    expect(job.progress).toBe(75);
  });
});

describe("summarizeBatchOutcome", () => {
  const fail = (clip_index: number, error: string) => ({ clip_index, status: "error" as const, error });
  const ok = (clip_index: number) => ({ clip_index, status: "success" as const, output_path: `/out/${clip_index}.mp4` });

  it("fails the job with the first clip's error when nothing rendered", () => {
    const outcome = summarizeBatchOutcome(
      { total_clips: 2, successful_clips: 0, results: [fail(0, "bundle failed"), fail(1, "other")] },
      "Export complete!",
    );

    expect(outcome.status).toBe("error");
    expect(outcome.error).toBe("All 2 clips failed. First error (clip 1): bundle failed");
    expect(outcome.message).toBe(`Error: ${outcome.error}`);
  });

  it("names the rendered count and every failed clip on a partial batch", () => {
    const outcome = summarizeBatchOutcome(
      { total_clips: 3, successful_clips: 1, results: [ok(0), fail(1, "a"), fail(2, "b")] },
      "Export complete!",
    );

    expect(outcome).toEqual({ status: "done", message: "1 of 3 clips rendered. Failed: clip 2: a; clip 3: b" });
  });

  it("keeps the success message when every clip rendered", () => {
    expect(summarizeBatchOutcome({ total_clips: 1, successful_clips: 1, results: [ok(0)] }, "Batch complete!"))
      .toEqual({ status: "done", message: "Batch complete!" });
  });

  it("fails a batch that came back with no rows", () => {
    expect(summarizeBatchOutcome(undefined, "Batch complete!").status).toBe("error");
    expect(summarizeBatchOutcome({ total_clips: 0, successful_clips: 0, results: [] }, "x").status).toBe("error");
  });

  it("shortens a long stack trace to one line in the message", () => {
    const outcome = summarizeBatchOutcome(
      { total_clips: 1, successful_clips: 0, results: [fail(0, `boom\n${"at frame\n".repeat(200)}`)] },
      "x",
    );

    expect(outcome.message).not.toContain("\n");
    expect(outcome.message.length).toBeLessThan(400);
  });
});
