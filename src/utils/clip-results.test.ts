import { describe, expect, it } from "vitest";
import { advanceProgress, tagSubmittedClip, tagSubmittedClips } from "./clip-results.js";
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
