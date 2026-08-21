import React from "react";
import { useCurrentFrame, useVideoConfig } from "remotion";
import type { Word, CaptionStyle } from "../types";
import { captionScale } from "../types";
import { buildChunks, activeChunkAt, splitCaptionLines } from "../chunks";

interface Props {
  words: Word[];
  style: CaptionStyle;
  singleLine?: boolean;
  /** Accepted for one shape across the caption components; karaoke holds still. */
  motion?: unknown;
}

const KaraokeLine: React.FC<{
  words: Word[];
  currentTime: number;
  style: CaptionStyle;
  singleLine?: boolean;
}> = ({ words, currentTime, style, singleLine = false }) => {
  return (
    <div
      style={{
        textAlign: "center",
        fontFamily: style.fontFamily,
        fontSize: style.fontSize,
        fontWeight: 600,
        lineHeight: 1.25,
        textShadow: "0 2px 12px rgba(0,0,0,0.9), 0 0 40px rgba(0,0,0,0.4)",
        whiteSpace: singleLine ? "nowrap" : undefined,
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

export const KaraokeCaptions: React.FC<Props> = ({ words, style, singleLine = false }) => {
  const frame = useCurrentFrame();
  const { fps, height, durationInFrames } = useVideoConfig();
  const s = captionScale(height);
  const currentTime = frame / fps;

  const chunks = buildChunks(words, {
    perChunk: style.wordsPerChunk,
    absorbTail: 1,
    clipEnd: durationInFrames / fps,
  });
  const activeChunk = activeChunkAt(chunks, currentTime);

  if (!activeChunk) return null;

  const [line1, line2] = splitCaptionLines(
    activeChunk.words,
    Math.ceil(activeChunk.words.length / 2),
    singleLine,
  );
  const scaledStyle = { ...style, fontSize: style.fontSize * s };

  return (
    <div
      style={{
        position: "absolute",
        bottom: style.marginBottom * s,
        left: 60 * s,
        right: 60 * s,
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        gap: 4 * s,
      }}
    >
      <KaraokeLine words={line1} currentTime={currentTime} style={scaledStyle} singleLine={singleLine} />
      {line2.length > 0 && (
        <KaraokeLine words={line2} currentTime={currentTime} style={scaledStyle} singleLine={singleLine} />
      )}
    </div>
  );
};
