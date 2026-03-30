import React from "react";
import { useCurrentFrame, useVideoConfig } from "remotion";
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
    if (words.length - end === 1) end = words.length;
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
  if (words.length <= 3) return [words, []];
  const mid = Math.ceil(words.length / 2);
  return [words.slice(0, mid), words.slice(mid)];
}

const KaraokeLine: React.FC<{
  words: Word[];
  currentTime: number;
  style: CaptionStyle;
}> = ({ words, currentTime, style }) => {
  return (
    <div
      style={{
        textAlign: "center",
        fontFamily: style.fontFamily,
        fontSize: style.fontSize,
        fontWeight: 600,
        lineHeight: 1.25,
        textShadow: "0 2px 12px rgba(0,0,0,0.9), 0 0 40px rgba(0,0,0,0.4)",
      }}
    >
      {words.map((word, i) => {
        const isSpoken = currentTime >= word.start;
        const progress = isSpoken
          ? Math.min(1, (currentTime - word.start) / Math.max(0.05, word.end - word.start))
          : 0;

        return (
          <React.Fragment key={i}>
            {i > 0 ? " " : ""}
            <span style={{ position: "relative", display: "inline" }}>
              {/* Base text (dim) */}
              <span style={{ color: style.color }}>{word.word}</span>
              {/* Active overlay (clips left to right) */}
              <span
                style={{
                  position: "absolute",
                  top: 0,
                  left: 0,
                  color: style.activeColor,
                  clipPath: `inset(0 ${(1 - progress) * 100}% 0 0)`,
                  whiteSpace: "nowrap",
                }}
              >
                {word.word}
              </span>
            </span>
          </React.Fragment>
        );
      })}
    </div>
  );
};

export const KaraokeCaptions: React.FC<Props> = ({ words, style }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const currentTime = frame / fps;

  const chunks = buildChunks(words, style.wordsPerChunk);
  const activeChunk = chunks.find(
    (c) => currentTime >= c.start && currentTime < c.end
  );

  if (!activeChunk) return null;

  const [line1, line2] = splitIntoLines(activeChunk.words);

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
        gap: 4,
      }}
    >
      <KaraokeLine words={line1} currentTime={currentTime} style={style} />
      {line2.length > 0 && (
        <KaraokeLine words={line2} currentTime={currentTime} style={style} />
      )}
    </div>
  );
};
