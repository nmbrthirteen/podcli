import { describe, it, expect } from "vitest";
import { buildChunks, activeChunkAt } from "./chunks";
import type { Word } from "./types";

function words(...spec: [string, number, number][]): Word[] {
  return spec.map(([word, start, end]) => ({ word, start, end }));
}

describe("buildChunks", () => {
  it("holds a chunk through a short pause so captions don't flicker", () => {
    const chunks = buildChunks(
      words(["one", 0, 0.4], ["two.", 0.4, 0.8], ["three", 1.0, 1.4]),
      { perChunk: 4 }
    );

    expect(chunks).toHaveLength(2);
    expect(chunks[0].end).toBe(0.8);
    expect(chunks[0].displayEnd).toBe(chunks[1].start);
    expect(activeChunkAt(chunks, 0.9)).toBe(chunks[0]);
  });

  it("caps the hold at 0.4s across a long silence", () => {
    const chunks = buildChunks(
      words(["one", 0, 0.4], ["two", 0.4, 0.8], ["three", 15.0, 15.4]),
      { perChunk: 4 }
    );

    expect(chunks).toHaveLength(2);
    expect(chunks[0].displayEnd).toBeCloseTo(1.2, 6);
    expect(activeChunkAt(chunks, 5)).toBeUndefined();
  });

  it("never displays past the next chunk's start", () => {
    const chunks = buildChunks(
      words(["one", 0, 0.9], ["two.", 0.9, 1.5], ["three", 1.5, 2.0]),
      { perChunk: 4 }
    );

    expect(chunks[0].displayEnd).toBe(chunks[1].start);
  });

  it("gives the last chunk a hold, clamped to the composition duration", () => {
    const spec: [string, number, number][] = [["one", 0, 0.4], ["two", 0.4, 0.8]];

    expect(buildChunks(words(...spec), { perChunk: 4 }).at(-1)!.displayEnd).toBeCloseTo(2.0, 6);
    expect(
      buildChunks(words(...spec), { perChunk: 4, clipEnd: 1.0 }).at(-1)!.displayEnd
    ).toBeCloseTo(1.0, 6);
    expect(
      buildChunks(words(...spec), { perChunk: 4, clipEnd: 10 }).at(-1)!.displayEnd
    ).toBeCloseTo(2.0, 6);
  });

  it("breaks on terminal punctuation, speaker change and long gaps", () => {
    const chunks = buildChunks(
      [
        { word: "hi.", start: 0, end: 0.3, speaker: "A" },
        { word: "so", start: 0.3, end: 0.6, speaker: "A" },
        { word: "yes", start: 0.6, end: 0.9, speaker: "B" },
        { word: "later", start: 3.0, end: 3.4, speaker: "B" },
      ],
      { perChunk: 8 }
    );

    expect(chunks.map((c) => c.words.map((w) => w.word).join(" "))).toEqual([
      "hi.",
      "so",
      "yes",
      "later",
    ]);
  });
});
