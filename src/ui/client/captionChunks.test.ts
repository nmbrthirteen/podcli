import { describe, expect, it } from "vitest";
import { buildPreviewChunks, activePreviewChunk, type PreviewWord } from "./captionChunks";
import { buildChunks } from "../../../remotion/src/chunks";
import type { Word } from "../../../remotion/src/types";

const words: PreviewWord[] = [
  { text: "the", start: 0, end: 0.2, speaker: "A" },
  { text: "secret", start: 0.2, end: 0.6, speaker: "A" },
  { text: "to", start: 0.6, end: 0.7, speaker: "A" },
  { text: "growth.", start: 0.7, end: 1.2, speaker: "A" },
  { text: "is", start: 1.3, end: 1.5, speaker: "A" },
  { text: "boring", start: 1.5, end: 1.9, speaker: "A" },
  { text: "work", start: 1.9, end: 2.3, speaker: "A" },
  { text: "really?", start: 4.0, end: 4.4, speaker: "B" },
  { text: "yes", start: 4.5, end: 4.8, speaker: "B" },
];

const asRendererWords = (ws: PreviewWord[]): Word[] =>
  ws.map((w) => ({ word: w.text, start: w.start, end: w.end, speaker: w.speaker ?? null })) as Word[];

const shapes = (chunks: { words: { text?: string; word?: string }[] }[]) =>
  chunks.map((c) => c.words.map((w) => w.text ?? w.word));

describe("buildPreviewChunks", () => {
  // The phone preview exists to show what the MP4 will look like; if the two
  // chunkers disagree, it is showing a different video.
  it("groups and holds exactly like the renderer", () => {
    for (const cfg of [
      { wordsPerChunk: 3, maxCharsPerChunk: 18, splitTail: true },
      { wordsPerChunk: 3, absorbTail: 1 },
      { wordsPerChunk: 5, absorbTail: 1 },
      { wordsPerChunk: 6, absorbTail: 2 },
    ]) {
      const preview = buildPreviewChunks(words, cfg, 6);
      const rendered = buildChunks(asRendererWords(words), {
        perChunk: cfg.wordsPerChunk,
        maxChars: cfg.maxCharsPerChunk,
        absorbTail: cfg.absorbTail,
        splitTail: cfg.splitTail,
        clipEnd: 6,
      });
      expect(shapes(preview)).toEqual(shapes(rendered));
      expect(preview.map((c) => c.displayEnd)).toEqual(rendered.map((c) => c.displayEnd));
    }
  });

  it("breaks on terminal punctuation, speaker change and long gaps", () => {
    const chunks = buildPreviewChunks(words, { wordsPerChunk: 6, absorbTail: 2 });
    expect(shapes(chunks)).toEqual([
      ["the", "secret", "to", "growth."],
      ["is", "boring", "work"],
      ["really?"],
      ["yes"],
    ]);
  });

  it("holds a chunk through a short pause but not through a long one", () => {
    const chunks = buildPreviewChunks(words, { wordsPerChunk: 6, absorbTail: 2 });
    // 0.1s gap to the next chunk: held all the way to it.
    expect(chunks[0].displayEnd).toBeCloseTo(1.3);
    // 1.7s gap: held for the 0.4s fill, then the caption clears.
    expect(chunks[1].displayEnd).toBeCloseTo(2.7);
    expect(activePreviewChunk(chunks, 3.5)).toBeUndefined();
  });

  it("caps the last chunk's hold at the clip end", () => {
    const chunks = buildPreviewChunks(words, { wordsPerChunk: 6, absorbTail: 2 }, 5.0);
    expect(chunks[chunks.length - 1].displayEnd).toBeCloseTo(5.0);
  });
});
