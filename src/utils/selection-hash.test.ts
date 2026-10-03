import { describe, it, expect } from "vitest";
import { computeSelectionHash } from "./selection-hash.js";

function clip(overrides: Partial<Parameters<typeof computeSelectionHash>[0]> = {}) {
  return {
    start_second: 10,
    end_second: 20,
    segments: undefined,
    suggested_caption_style: "hormozi",
    title: "A title",
    ...overrides,
  };
}

const words = [
  { word: "one", start: 9, end: 9.5, confidence: 1 },
  { word: "two", start: 10, end: 10.5, confidence: 1 },
  { word: "three", start: 15, end: 15.5, confidence: 1 },
  { word: "four", start: 20, end: 20.5, confidence: 1 },
];

describe("computeSelectionHash", () => {
  it("is stable for the same clip and words", () => {
    const a = computeSelectionHash(clip(), words);
    const b = computeSelectionHash(clip(), words);
    expect(a).toBe(b);
  });

  it("changes when the range changes", () => {
    const a = computeSelectionHash(clip(), words);
    const b = computeSelectionHash(clip({ end_second: 25 }), words);
    expect(a).not.toBe(b);
  });

  it("changes when the caption style changes", () => {
    const a = computeSelectionHash(clip(), words);
    const b = computeSelectionHash(clip({ suggested_caption_style: "karaoke" }), words);
    expect(a).not.toBe(b);
  });

  it("changes when the title changes", () => {
    const a = computeSelectionHash(clip(), words);
    const b = computeSelectionHash(clip({ title: "A different title" }), words);
    expect(a).not.toBe(b);
  });

  it("changes when segments change", () => {
    const a = computeSelectionHash(clip(), words);
    const b = computeSelectionHash(
      clip({ segments: [{ start: 10, end: 15 }] }),
      words,
    );
    expect(a).not.toBe(b);
  });

  it("changes when the transcript words inside the range change", () => {
    const a = computeSelectionHash(clip(), words);
    const editedWords = words.map((w) =>
      w.word === "three" ? { ...w, word: "THREE-EDITED" } : w,
    );
    const b = computeSelectionHash(clip(), editedWords);
    expect(a).not.toBe(b);
  });

  it("ignores words outside the clip range", () => {
    const a = computeSelectionHash(clip(), words);
    const extraOutside = [...words, { word: "far-away", start: 500, end: 501, confidence: 1 }];
    // "far-away" is outside [10, 20) only because of its start time. Adding
    // a word inside the range should matter, outside should not.
    const b = computeSelectionHash(clip(), extraOutside);
    expect(a).toBe(b);
  });

  it("is unaffected by a hook field that isn't set", () => {
    const a = computeSelectionHash(clip(), words);
    const b = computeSelectionHash({ ...clip(), hook: undefined }, words);
    expect(a).toBe(b);
  });

  it("changes when a hook is added", () => {
    const a = computeSelectionHash(clip(), words);
    const b = computeSelectionHash(
      { ...clip(), hook: { start: 10, end: 12, mode: "repeat" } },
      words,
    );
    expect(a).not.toBe(b);
  });

  it("changes when the hook's range or mode is edited", () => {
    const hooked = { ...clip(), hook: { start: 12, end: 14, mode: "repeat" as const } };
    const base = computeSelectionHash(hooked, words);
    const moved = computeSelectionHash({ ...hooked, hook: { ...hooked.hook, start: 12.5 } }, words);
    const remoded = computeSelectionHash({ ...hooked, hook: { ...hooked.hook, mode: "move" } }, words);
    expect(moved).not.toBe(base);
    expect(remoded).not.toBe(base);
  });

  it("changes when a hook is cleared", () => {
    const hooked = { ...clip(), hook: { start: 12, end: 14, mode: "repeat" as const } };
    expect(computeSelectionHash({ ...hooked, hook: undefined }, words)).not.toBe(
      computeSelectionHash(hooked, words),
    );
  });
});
