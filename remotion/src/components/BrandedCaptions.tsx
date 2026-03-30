import React from "react";
import {
  useCurrentFrame,
  useVideoConfig,
  spring,
  Img,
  staticFile,
} from "remotion";
import type { Word, CaptionStyle } from "../types";

interface Props {
  words: Word[];
  style: CaptionStyle;
  logoSrc?: string;
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

    // Only absorb 1 leftover word, not 2
    const remaining = words.length - end;
    if (remaining === 1) {
      end = words.length;
    }

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
  if (words.length <= 3) {
    return [words, []];
  }
  const mid = Math.ceil(words.length / 2);
  return [words.slice(0, mid), words.slice(mid)];
}

/**
 * Active pill rendered as an absolutely positioned background behind the word.
 * The word itself is always rendered as plain inline text so layout doesn't shift.
 */
const WordWithPill: React.FC<{
  word: Word;
  isActive: boolean;
  frame: number;
  fps: number;
}> = ({ word, isActive, frame, fps }) => {
  const wordEntryFrame = Math.round(word.start * fps);

  const pillOpacity = isActive
    ? spring({
        frame: frame - wordEntryFrame,
        fps,
        config: { damping: 14, stiffness: 200, mass: 0.4 },
      })
    : 0;

  return (
    <span style={{ position: "relative", display: "inline" }}>
      {/* Pill background — absolutely positioned, doesn't affect layout */}
      <span
        style={{
          position: "absolute",
          top: -8,
          left: -32,
          right: -32,
          bottom: -8,
          backgroundColor: "rgba(0, 0, 0, 0.85)",
          borderRadius: 36,
          boxShadow: "0 8px 40px rgba(0, 0, 0, 0.5)",
          opacity: pillOpacity,
          pointerEvents: "none",
        }}
      />
      {/* Word text — always inline, never shifts */}
      <span style={{ position: "relative", zIndex: 1 }}>{word.word}</span>
    </span>
  );
};

const CaptionLine: React.FC<{
  words: Word[];
  currentTime: number;
  frame: number;
  fps: number;
  style: CaptionStyle;
}> = ({ words, currentTime, frame, fps, style }) => {
  return (
    <div
      style={{
        textAlign: "center",
        fontFamily: style.fontFamily,
        fontSize: style.fontSize,
        fontWeight: 400,
        color: style.color,
        textShadow: "0 0 80px rgba(0, 0, 0, 0.15), 0 0 160px rgba(0, 0, 0, 0.1), 0 0 300px rgba(0, 0, 0, 0.08)",
        lineHeight: 1.25,
      }}
    >
      {words.map((word, i) => {
        const isActive = currentTime >= word.start && currentTime < word.end;
        const prefix = i > 0 ? " " : "";

        return (
          <React.Fragment key={i}>
            {prefix}
            <WordWithPill
              word={word}
              isActive={isActive}
              frame={frame}
              fps={fps}
            />
          </React.Fragment>
        );
      })}
    </div>
  );
};

export const BrandedCaptions: React.FC<Props> = ({
  words,
  style,
  logoSrc,
}) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const currentTime = frame / fps;

  const chunks = buildChunks(words, style.wordsPerChunk);
  const activeChunk = chunks.find(
    (c) => currentTime >= c.start && currentTime < c.end
  );

  return (
    <>
      {logoSrc && (
        <Img
          src={logoSrc.startsWith("http") ? logoSrc : staticFile(logoSrc)}
          style={{
            position: "absolute",
            top: 361,
            left: 216,
            width: 510,
            height: 252,
            objectFit: "contain",
          }}
        />
      )}

      {activeChunk && (() => {
        const [line1, line2] = splitIntoLines(activeChunk.words);

        return (
          <div
            style={{
              position: "absolute",
              bottom: style.marginBottom,
              left: 120,
              right: 120,
              display: "flex",
              flexDirection: "column",
              alignItems: "center",
              gap: 8,
            }}
          >
            <CaptionLine
              words={line1}
              currentTime={currentTime}
              frame={frame}
              fps={fps}
              style={style}
            />
            {line2.length > 0 && (
              <CaptionLine
                words={line2}
                currentTime={currentTime}
                frame={frame}
                fps={fps}
                style={style}
              />
            )}
          </div>
        );
      })()}
    </>
  );
};
