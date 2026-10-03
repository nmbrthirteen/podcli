import { describe, it, expect } from "vitest";
import { findGroundingText } from "./transcript.js";

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
