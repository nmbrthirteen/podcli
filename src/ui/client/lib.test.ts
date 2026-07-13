import { describe, expect, it } from "vitest";
import { fmt, fmtMs, findClipResult } from "./lib";

describe("fmt", () => {
  it("formats sub-hour timestamps as m:ss", () => {
    expect(fmt(0)).toBe("0:00");
    expect(fmt(9)).toBe("0:09");
    expect(fmt(767)).toBe("12:47");
    expect(fmt(3599)).toBe("59:59");
  });

  // A podcast past the hour mark rendered as "78:31" instead of "1:18:31".
  it("rolls into hours", () => {
    expect(fmt(3600)).toBe("1:00:00");
    expect(fmt(4711)).toBe("1:18:31");
    expect(fmt(7325)).toBe("2:02:05");
  });

  it("clamps negatives", () => {
    expect(fmt(-5)).toBe("0:00");
  });
});

describe("fmtMs", () => {
  it("appends milliseconds", () => {
    expect(fmtMs(12.5)).toBe("0:12.500");
    expect(fmtMs(4711.25)).toBe("1:18:31.250");
  });
});

describe("findClipResult", () => {
  const clips = [
    { start_second: 10, end_second: 40 },
    { start_second: 120, end_second: 165 },
    { start_second: 640, end_second: 700 },
  ];

  // An agent exporting only clip #3 gets one row back, at clip_index 0. Reading
  // it positionally marked clip #1 as exported with clip #3's file.
  it("lands an agent's single export on the clip it was rendered for", () => {
    const results = [
      { status: "success", output_path: "/out/third.mp4", source_start_second: 640, source_end_second: 700 },
    ];
    expect(findClipResult(results, clips[0], 0)).toBeUndefined();
    expect(findClipResult(results, clips[1], 1)).toBeUndefined();
    expect(findClipResult(results, clips[2], 2)?.output_path).toBe("/out/third.mp4");
  });

  // Parallel renders report as they finish, so results is sparse and out of order.
  it("matches rows that arrived out of order", () => {
    const results = [
      undefined,
      { status: "success", output_path: "/out/b.mp4", source_start_second: 120, source_end_second: 165 },
    ];
    expect(findClipResult(results, clips[0], 0)).toBeUndefined();
    expect(findClipResult(results, clips[1], 1)?.output_path).toBe("/out/b.mp4");
  });

  it("falls back to position only for rows with no bounds", () => {
    const results = [{ status: "success", output_path: "/out/legacy.mp4" }];
    expect(findClipResult(results, clips[0], 0)?.output_path).toBe("/out/legacy.mp4");
  });

  it("tolerates float noise in the bounds", () => {
    const results = [
      { status: "success", output_path: "/out/a.mp4", source_start_second: 10.001, source_end_second: 40 },
    ];
    expect(findClipResult(results, clips[0], 0)?.output_path).toBe("/out/a.mp4");
  });
});
