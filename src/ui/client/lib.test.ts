import { describe, expect, it } from "vitest";
import {
  fmt,
  fmtMs,
  findClipResult,
  buildEnergyMap,
  clipKey,
  dropEnergy,
  clampClipIndex,
} from "./lib";

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

describe("buildEnergyMap", () => {
  const clips = [
    { clip_id: "c1", start_second: 10, end_second: 40 },
    { clip_id: "c2", start_second: 120, end_second: 165 },
    { clip_id: "c3", start_second: 640, end_second: 700 },
    { clip_id: "c4", start_second: 900, end_second: 940 },
  ];

  it("scores each clip on its own identity", () => {
    const map = buildEnergyMap([8.2, 5, 2.5, 7], clips);
    expect(map.c1).toEqual({ score: 8.2, level: "high" });
    expect(map.c2).toEqual({ score: 5, level: "medium" });
    expect(map.c3).toEqual({ score: 2.5, level: "low" });
  });

  // Keyed by position, deleting clip #2 slid every score up one row and left the
  // last row showing a badge for a clip that no longer existed.
  it("keeps scores on their clips after an agent deletes one", () => {
    const map = buildEnergyMap([8.2, 5, 2.5, 7], clips);
    const remaining = clips.filter((c) => c.clip_id !== "c2");
    expect(remaining.map((c) => map[clipKey(c)]?.score)).toEqual([8.2, 2.5, 7]);
    expect(Object.keys(map).filter((k) => !remaining.some((c) => clipKey(c) === k))).toEqual(["c2"]);
  });

  it("keeps scores on their clips after a range edit shifts a later clip", () => {
    const map = buildEnergyMap([8.2, 5, 2.5, 7], clips);
    const edited = { ...clips[1], start_second: 118, end_second: 170 };
    const after = [clips[0], edited, clips[2], clips[3]];
    expect(dropEnergy(map, clips[1])[clipKey(edited)]).toBeUndefined();
    expect(after.map((c) => map[clipKey(c)]?.score)).toEqual([8.2, 5, 2.5, 7]);
  });

  // A session persisted before suggestions carried a clip_id.
  it("falls back to bounds when a clip has no id", () => {
    const legacy = [
      { start_second: 10, end_second: 40 },
      { start_second: 120, end_second: 165 },
    ];
    const map = buildEnergyMap([9, 3], legacy);
    expect(map[clipKey(legacy[0])].score).toBe(9);
    expect(map[clipKey(legacy[1])].score).toBe(3);
  });

  it("ignores scores with no clip and clips with no score", () => {
    expect(buildEnergyMap([7, null, 4], clips.slice(0, 2))).toEqual({
      c1: { score: 7, level: "high" },
    });
  });
});

describe("clampClipIndex", () => {
  // j/k left the cursor past the end when an agent deleted the last clip, so
  // space deselected an index no row owned and the header count lied.
  it("pulls the cursor back onto the last row when the list shrinks", () => {
    expect(clampClipIndex(4, 4)).toBe(3);
    expect(clampClipIndex(9, 1)).toBe(0);
  });

  it("leaves an in-range cursor alone", () => {
    expect(clampClipIndex(2, 5)).toBe(2);
    expect(clampClipIndex(0, 1)).toBe(0);
  });

  it("clears the cursor when there are no clips", () => {
    expect(clampClipIndex(0, 0)).toBeNull();
    expect(clampClipIndex(null, 5)).toBeNull();
  });
});
