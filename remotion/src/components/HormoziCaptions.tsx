import React from "react";
import {
  useCurrentFrame,
  useVideoConfig,
  interpolate,
  spring,
} from "remotion";
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

export const HormoziCaptions: React.FC<Props> = ({ words, style }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const currentTime = frame / fps;

  const chunks = buildChunks(words, style.wordsPerChunk);
  const activeChunk = chunks.find(
    (c) => currentTime >= c.start && currentTime < c.end
  );

  if (!activeChunk) return null;

  const entryFrame = Math.round(activeChunk.start * fps);

  const scale = spring({
    frame: frame - entryFrame,
    fps,
    config: { damping: 12, stiffness: 180, mass: 0.5 },
  });

  const opacity = interpolate(
    frame - entryFrame,
    [0, 3],
    [0, 1],
    { extrapolateRight: "clamp" }
  );

  return (
    <div
      style={{
        position: "absolute",
        bottom: style.marginBottom,
        left: 0,
        right: 0,
        display: "flex",
        justifyContent: "center",
        opacity,
        transform: `scale(${scale})`,
      }}
    >
      <div
        style={{
          backgroundColor: "rgba(0, 0, 0, 0.8)",
          borderRadius: 16,
          padding: "14px 32px",
          textAlign: "center",
          fontFamily: style.fontFamily,
          fontSize: style.fontSize,
          fontWeight: 800,
          lineHeight: 1.2,
          textShadow: "0 0 20px rgba(0, 0, 0, 0.5)",
        }}
      >
        {activeChunk.words.map((word, i) => {
          const isActive = currentTime >= word.start && currentTime < word.end;
          const text = style.uppercase
            ? word.word.toUpperCase()
            : word.word;

          return (
            <React.Fragment key={i}>
              {i > 0 ? " " : ""}
              <span
                style={{
                  color: isActive ? style.activeColor : style.color,
                  textShadow: isActive
                    ? `0 0 30px ${style.activeColor}60, 0 0 60px ${style.activeColor}30`
                    : "none",
                }}
              >
                {text}
              </span>
            </React.Fragment>
          );
        })}
      </div>
    </div>
  );
};
