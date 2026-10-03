import { describe, it, expect } from "vitest";
import { findGroundingText, reconcileSegmentsForRange } from "./transcript.js";

describe("findGroundingText", () => {
  const suggestions = [
    { start_second: 10, end_second: 40, payoff: "the payoff", context_line: "the question", preview_text: "the opening line" },
    { start_second: 100, end_second: 130 }, // no grounding fields at all
  ];

  it("returns the grounding fields of the closest-matching suggestion", () => {
    expect(findGroundingText(suggestions, 10, 40)).toEqual({
      payoff: "the payoff",
      context_line: "the question",
      preview_text: "the opening line",
    });
  });

  it("matches within a small tolerance, not just exactly", () => {
    expect(findGroundingText(suggestions, 11, 39)).toEqual({
      payoff: "the payoff",
      context_line: "the question",
      preview_text: "the opening line",
    });
  });

  it("returns undefined fields when the matched suggestion has none", () => {
    expect(findGroundingText(suggestions, 100, 130)).toEqual({
      payoff: undefined,
      context_line: undefined,
      preview_text: undefined,
    });
  });

  it("returns undefined when nothing matches", () => {
    expect(findGroundingText(suggestions, 500, 530)).toBeUndefined();
  });

  it("returns undefined for an empty or missing suggestions list", () => {
    expect(findGroundingText([], 10, 40)).toBeUndefined();
    expect(findGroundingText(undefined, 10, 40)).toBeUndefined();
    expect(findGroundingText(null, 10, 40)).toBeUndefined();
  });
});

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
