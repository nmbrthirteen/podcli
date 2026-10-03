import { describe, it, expect } from "vitest";
import { playbackDuration, playbackRanges, validateHook } from "./clip-hook.js";

describe("validateHook", () => {
  it("accepts no hook", () => {
    expect(validateHook(undefined, 10, 40)).toBeNull();
    expect(validateHook(null, 10, 40)).toBeNull();
  });

  it("accepts a hook inside the range, edges included", () => {
    expect(validateHook({ start: 10, end: 12, mode: "repeat" }, 10, 40)).toBeNull();
    expect(validateHook({ start: 38, end: 40, mode: "move" }, 10, 40)).toBeNull();
  });

  it("rejects an end at or before the start", () => {
    expect(validateHook({ start: 15, end: 15, mode: "repeat" }, 10, 40)).toMatch(/greater than/);
  });

  it("rejects hooks under 1s or over 15s", () => {
    expect(validateHook({ start: 12, end: 12.5, mode: "repeat" }, 10, 40)).toMatch(/between 1 and 15/);
    expect(validateHook({ start: 12, end: 28, mode: "repeat" }, 10, 40)).toMatch(/between 1 and 15/);
  });

  it("rejects a hook outside the range", () => {
    expect(validateHook({ start: 8, end: 11, mode: "repeat" }, 10, 40)).toMatch(/not inside/);
  });

  it("rejects a hook in a gap between segments", () => {
    const segments = [{ start: 10, end: 15 }, { start: 20, end: 30 }];
    expect(validateHook({ start: 14, end: 17, mode: "repeat" }, 10, 30, segments)).toMatch(/not inside/);
    expect(validateHook({ start: 21, end: 24, mode: "repeat" }, 10, 30, segments)).toBeNull();
  });

  it("rejects a bad mode or non-numeric bounds", () => {
    expect(validateHook({ start: 12, end: 14, mode: "loop" }, 10, 40)).toMatch(/repeat/);
    expect(validateHook({ start: 12, end: 14 }, 10, 40)).toMatch(/repeat/);
    expect(validateHook({ start: "12", end: 14, mode: "move" }, 10, 40)).toMatch(/numbers/);
    expect(validateHook("12-14", 10, 40)).toMatch(/object/);
  });

  it("rejects a move hook that would take the whole clip", () => {
    expect(validateHook({ start: 10, end: 14, mode: "move" }, 10, 14)).toMatch(/whole clip/);
    expect(validateHook({ start: 10, end: 14, mode: "repeat" }, 10, 14)).toBeNull();
  });
});

describe("playbackRanges", () => {
  const segments = [{ start: 10, end: 20 }, { start: 22, end: 30 }];

  it("is the body when there is no hook", () => {
    expect(playbackRanges(10, 30, segments)).toEqual(segments);
    expect(playbackRanges(10, 30)).toEqual([{ start: 10, end: 30 }]);
  });

  it("puts a repeat hook first and keeps the body whole", () => {
    expect(playbackRanges(10, 30, segments, { start: 24, end: 27, mode: "repeat" })).toEqual([
      { start: 24, end: 27 },
      { start: 10, end: 20 },
      { start: 22, end: 30 },
    ]);
  });

  it("puts a move hook first and cuts it from the body", () => {
    expect(playbackRanges(10, 30, segments, { start: 24, end: 27, mode: "move" })).toEqual([
      { start: 24, end: 27 },
      { start: 10, end: 20 },
      { start: 22, end: 24 },
      { start: 27, end: 30 },
    ]);
  });
});

describe("playbackDuration", () => {
  it("adds a repeat hook's length and leaves a move hook's total unchanged", () => {
    expect(playbackDuration(10, 30)).toBe(20);
    expect(playbackDuration(10, 30, undefined, { start: 12, end: 15, mode: "repeat" })).toBe(23);
    expect(playbackDuration(10, 30, undefined, { start: 12, end: 15, mode: "move" })).toBe(20);
  });
});
