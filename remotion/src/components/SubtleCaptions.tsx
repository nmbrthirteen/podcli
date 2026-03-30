import React from "react";
import { useCurrentFrame, useVideoConfig, interpolate } from "remotion";
import type { Word, CaptionStyle } from "../types";

interface Props {
  words: Word[];
  style: CaptionStyle;
}

interface Chunk {
  words: Word[];
  start: number;
  end: number;
}

function buildChunks(words: Word[], perChunk: number): Chunk[] {
  const chunks: Chunk[] = [];
  let i = 0;
  while (i < words.length) {
    let end = Math.min(i + perChunk, words.length);
    if (words.length - end <= 2) end = words.length;
    const slice = words.slice(i, end);
    chunks.push({
      words: slice,
      start: slice[0].start,
      end: slice[slice.length - 1].end,
    });
    i = end;
  }
  return chunks;
}

function splitIntoLines(words: Word[]): [Word[], Word[]] {
  if (words.length <= 4) return [words, []];
  const mid = Math.ceil(words.length / 2);
  return [words.slice(0, mid), words.slice(mid)];
}

export const SubtleCaptions: React.FC<Props> = ({ words, style }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const currentTime = frame / fps;

  const chunks = buildChunks(words, style.wordsPerChunk);
  const activeChunk = chunks.find(
    (c) => currentTime >= c.start && currentTime < c.end
  );

  if (!activeChunk) return null;

  const entryFrame = Math.round(activeChunk.start * fps);
  const opacity = interpolate(
    frame - entryFrame,
    [0, 5],
    [0, 1],
    { extrapolateRight: "clamp" }
  );

  // Slight upward slide on entry
  const translateY = interpolate(
    frame - entryFrame,
    [0, 6],
    [8, 0],
    { extrapolateRight: "clamp" }
  );

  const [line1, line2] = splitIntoLines(activeChunk.words);
  const text1 = line1.map((w) => w.word).join(" ");
  const text2 = line2.map((w) => w.word).join(" ");

  return (
    <div
      style={{
        position: "absolute",
        bottom: style.marginBottom,
        left: 60,
        right: 60,
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        gap: 2,
        opacity,
        transform: `translateY(${translateY}px)`,
      }}
    >
      <span
        style={{
          fontFamily: style.fontFamily,
          fontSize: style.fontSize,
          fontWeight: 400,
          color: style.color,
          textShadow:
            "0 1px 3px rgba(0,0,0,0.95), 0 0 20px rgba(0,0,0,0.6), 0 0 50px rgba(0,0,0,0.3)",
          textAlign: "center",
          lineHeight: 1.35,
        }}
      >
        {text1}
      </span>
      {text2 && (
        <span
          style={{
            fontFamily: style.fontFamily,
            fontSize: style.fontSize,
            fontWeight: 400,
            color: style.color,
            textShadow:
              "0 1px 3px rgba(0,0,0,0.95), 0 0 20px rgba(0,0,0,0.6), 0 0 50px rgba(0,0,0,0.3)",
            textAlign: "center",
            lineHeight: 1.35,
          }}
        >
          {text2}
        </span>
      )}
    </div>
  );
};
