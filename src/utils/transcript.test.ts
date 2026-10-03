import { describe, it, expect } from "vitest";
import { reconcileSegmentsForRange } from "./transcript.js";

describe("reconcileSegmentsForRange", () => {
  it("passes through when there are no segments", () => {
    expect(reconcileSegmentsForRange(undefined, 0, 10)).toBeUndefined();
    expect(reconcileSegmentsForRange([], 0, 10)).toEqual([]);
  });

  it("drops segments entirely outside the new range", () => {
    const segments = [
      { start: 0, end: 5 },
      { start: 20, end: 25 },
    ];
    expect(reconcileSegmentsForRange(segments, 20, 25)).toBeUndefined();
  });

  it("clamps segments straddling a new boundary", () => {
    const segments = [
      { start: 0, end: 10 },
      { start: 15, end: 25 },
    ];
    expect(reconcileSegmentsForRange(segments, 5, 20)).toEqual([
      { start: 5, end: 10 },
      { start: 15, end: 20 },
    ]);
  });

  it("clears a single segment that ends up spanning the whole new range", () => {
    const segments = [{ start: 0, end: 30 }];
    expect(reconcileSegmentsForRange(segments, 5, 20)).toBeUndefined();
  });

  it("keeps a multi-segment edit that still carries editorial cuts", () => {
    const segments = [
      { start: 2, end: 8 },
      { start: 12, end: 18 },
    ];
    expect(reconcileSegmentsForRange(segments, 0, 20)).toEqual([
      { start: 2, end: 8 },
      { start: 12, end: 18 },
    ]);
  });
});
