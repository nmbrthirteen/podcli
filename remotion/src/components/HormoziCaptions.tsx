import React from "react";
import {
  useCurrentFrame,
  useVideoConfig,
  interpolate,
  spring,
} from "remotion";
import type { Word, CaptionStyle } from "../types";
import { captionScale } from "../types";
import { buildChunks, activeChunkAt } from "../chunks";

interface Props {
  words: Word[];
  style: CaptionStyle;
}

export const HormoziCaptions: React.FC<Props> = ({ words, style }) => {
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
        bottom: style.marginBottom * s,
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
          borderRadius: 16 * s,
          padding: `${14 * s}px ${32 * s}px`,
          maxWidth: `calc(100% - ${120 * s}px)`,
          boxSizing: "border-box",
          overflowWrap: "anywhere",
          textAlign: "center",
          fontFamily: style.fontFamily,
          fontSize: style.fontSize * s,
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
