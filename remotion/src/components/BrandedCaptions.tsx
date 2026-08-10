import React from "react";
import {
  useCurrentFrame,
  useVideoConfig,
  spring,
  Img,
  staticFile,
} from "remotion";
import type { Word, CaptionStyle, CaptionPosition, LogoPosition } from "../types";
import { captionScale } from "../types";
import { buildChunks, activeChunkAt, splitCaptionLines } from "../chunks";

interface Props {
  words: Word[];
  style: CaptionStyle;
  logoSrc?: string;
  faceY?: number | null; // normalized 0-1 (0=top, 1=bottom)
  captionPosition?: CaptionPosition;
  logoPosition?: LogoPosition;
  singleLine?: boolean;
}

const MAX_CHARS_PER_CHUNK = 18;

// Unscaled logo box, shared by the logo style and the caption-margin guard so
// the two cannot drift apart.
const LOGO_INSET = 180;
const LOGO_HEIGHT = 126;
const LOGO_CAPTION_GAP = 24;

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
  const { height } = useVideoConfig();
  const s = captionScale(height);
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
          top: -4 * s,
          left: -16 * s,
          right: -16 * s,
          bottom: -4 * s,
          backgroundColor: "rgba(0, 0, 0, 0.85)",
          borderRadius: 18 * s,
          boxShadow: "0 4px 20px rgba(0, 0, 0, 0.5)",
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
        textShadow: "0 0 40px rgba(0, 0, 0, 0.15), 0 0 80px rgba(0, 0, 0, 0.1), 0 0 150px rgba(0, 0, 0, 0.08)",
        lineHeight: 1.25,
        maxWidth: "100%",
        overflowWrap: "anywhere",
        // No nowrap: MAX_CHARS_PER_CHUNK bounds character count, not rendered
        // width, so at captionFontScale 1.6 a chunk can exceed the safe inset.
        // Wrapping degrades better than bleeding off-frame; a chunk that fits
        // still renders on one line.
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
  faceY,
  captionPosition = "auto",
  logoPosition = "top-left",
  singleLine = false,
}) => {
  const frame = useCurrentFrame();
  const { fps, height, durationInFrames } = useVideoConfig();
  const s = captionScale(height);
  const currentTime = frame / fps;
  const scaledStyle = { ...style, fontSize: style.fontSize * s };

  const chunks = buildChunks(words, {
    perChunk: style.wordsPerChunk,
    maxChars: MAX_CHARS_PER_CHUNK,
    splitTail: true,
    clipEnd: durationInFrames / fps,
  });
  const activeChunk = activeChunkAt(chunks, currentTime);

  // Dynamic margin: if face is in the lower portion, push captions further down
  // faceY is normalized 0-1 (0=top, 1=bottom)
  // Default margin is style.marginBottom. If face center is below 0.55, reduce margin.
  const baseMargin = style.marginBottom * s;
  let dynamicMargin = baseMargin;
  if (captionPosition === "auto" && faceY != null && faceY > 0.55) {
    // Face is low — push captions to the very bottom
    dynamicMargin = Math.max(80 * s, baseMargin - Math.round((faceY - 0.55) * height * 0.6));
  } else if (captionPosition === "auto" && faceY != null && faceY < 0.35) {
    // Face is high — can bring captions up a bit
    dynamicMargin = baseMargin + 60 * s;
  }
  // A bottom-anchored logo spans 180-306 scaled units. Captions sitting inside
  // that band (captionPosition "lower" starts at 220) would render over it.
  if (logoSrc && logoPosition.startsWith("bottom-")) {
    dynamicMargin = Math.max(dynamicMargin, (LOGO_INSET + LOGO_HEIGHT + LOGO_CAPTION_GAP) * s);
  }

  return (
    <>
      {logoSrc && (
        <Img
          src={logoSrc.startsWith("http") ? logoSrc : staticFile(logoSrc)}
          style={{
            position: "absolute",
            ...(logoPosition.startsWith("top-") ? { top: LOGO_INSET * s } : { bottom: LOGO_INSET * s }),
            ...(logoPosition.endsWith("-left")
              ? { left: 108 * s }
              : logoPosition.endsWith("-right")
                ? { right: 108 * s }
                : { left: "50%", transform: "translateX(-50%)" }),
            width: 255 * s,
            height: LOGO_HEIGHT * s,
            objectFit: "contain",
          }}
        />
      )}

      {activeChunk && (() => {
        const [line1, line2] = splitCaptionLines(activeChunk.words, 2, singleLine);

        return (
          <div
            style={{
              position: "absolute",
              bottom: dynamicMargin,
              left: 60 * s,
              right: 60 * s,
              display: "flex",
              flexDirection: "column",
              alignItems: "center",
              gap: 8 * s,
            }}
          >
            <CaptionLine
              words={line1}
              currentTime={currentTime}
              frame={frame}
              fps={fps}
              style={scaledStyle}
            />
            {line2.length > 0 && (
              <CaptionLine
                words={line2}
                currentTime={currentTime}
                frame={frame}
                fps={fps}
                style={scaledStyle}
              />
            )}
          </div>
        );
      })()}
    </>
  );
};
