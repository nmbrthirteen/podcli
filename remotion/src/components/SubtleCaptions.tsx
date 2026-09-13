import React from "react";
import { useCurrentFrame, useVideoConfig } from "remotion";
import type { Word, CaptionStyle } from "../types";
import { captionScale } from "../types";
import { buildChunks, activeChunkAt, splitCaptionLines } from "../chunks";
import { MOTION, motionAt } from "../motion";
import type { Motion } from "../motion";

interface Props {
  words: Word[];
  style: CaptionStyle;
  singleLine?: boolean;
  motion?: Motion;
}

/**
 * One line of the caption, word by word.
 *
 * Drawn as spans rather than as a joined string so an emphasised word can be
 * coloured; the line reads identically when nothing is emphasised.
 */
const Line: React.FC<{
  words: Word[];
  style: CaptionStyle;
  scale: number;
  nowrap?: boolean;
}> = ({ words, style, scale, nowrap = false }) => (
  <span
    style={{
      fontFamily: style.fontFamily,
      fontSize: style.fontSize * scale,
      fontWeight: style.stroke ? 600 : 400,
      color: style.color,
      /*
       * One legibility trick or the other, never both. A stroked letter over
       * three stacked shadows reads as mud at the size this style is set at.
       */
      ...(style.stroke
        ? {
          WebkitTextStrokeWidth: `${style.fontSize * scale * 0.13}px`,
          WebkitTextStrokeColor: style.stroke,
          paintOrder: "stroke fill",
        }
        : {
          textShadow:
            "0 1px 3px rgba(0,0,0,0.95), 0 0 20px rgba(0,0,0,0.6), 0 0 50px rgba(0,0,0,0.3)",
        }),
      textAlign: "center",
      lineHeight: style.stroke ? 1.1 : 1.35,
      whiteSpace: nowrap ? "nowrap" : undefined,
    }}
  >
    {words.map((word, i) => (
      <React.Fragment key={i}>
        {i > 0 ? " " : ""}
        <span
          style={
            word.emphasis
              ? { color: style.emphasisColor ?? style.activeColor }
              : undefined
          }
        >
          {word.word}
        </span>
      </React.Fragment>
    ))}
  </span>
);

export const SubtleCaptions: React.FC<Props> = ({
  words, style, singleLine = false, motion,
}) => {
  const frame = useCurrentFrame();
  const { fps, height, durationInFrames } = useVideoConfig();
  const s = captionScale(height);
  const currentTime = frame / fps;

  const chunks = buildChunks(words, {
    perChunk: style.wordsPerChunk,
    absorbTail: 2,
    clipEnd: durationInFrames / fps,
  });
  const activeChunk = activeChunkAt(chunks, currentTime);

  if (!activeChunk) return null;

  const { opacity, shift: translateY } = motionAt({
    frame, fps,
    start: activeChunk.start,
    end: activeChunk.end,
    motion: motion ?? MOTION.subtle,
    scale: 8 * s,
  });

  const [line1, line2] = splitCaptionLines(
    activeChunk.words,
    Math.ceil(activeChunk.words.length / 2),
    singleLine,
  );
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
        gap: 2 * s,
        opacity,
        transform: `translateY(${translateY}px)`,
      }}
    >
      <Line words={line1} style={style} scale={s} nowrap={singleLine} />
      {line2.length > 0 && <Line words={line2} style={style} scale={s} />}
    </div>
  );
};
