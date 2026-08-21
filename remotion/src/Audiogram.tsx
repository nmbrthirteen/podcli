import React from "react";
import { AbsoluteFill, Img, useCurrentFrame, useVideoConfig } from "remotion";
import { HormoziCaptions } from "./components/HormoziCaptions";
import { KaraokeCaptions } from "./components/KaraokeCaptions";
import { SubtleCaptions } from "./components/SubtleCaptions";
import { BrandedCaptions } from "./components/BrandedCaptions";
import type { Word, CaptionStyle } from "./types";

/**
 * What an episode that was never filmed looks like.
 *
 * There is no frame to crop and no face to follow, so the picture has to be
 * made rather than found: the show's artwork behind, the voice drawn as bars,
 * and the same captions every other clip gets.
 *
 * The bars are handed in already reduced, one row of levels per frame, because
 * the samples are read on the Python side for moment detection anyway. Shipping
 * an hour of PCM into a browser to average it here would be the same arithmetic
 * somewhere slower and harder to test.
 */
export interface AudiogramProps {
  words: Word[];
  style: CaptionStyle;
  /** One row per frame, each row a level per bar in 0..1. */
  levels: number[][];
  bg: string;
  accent: string;
  /** The show's artwork, when the file carried any. */
  coverSrc?: string;
  title?: string;
  singleLine?: boolean;
}

/** Bars fall faster than they rise, which is what makes them read as a voice. */
const smooth = (levels: number[][], frame: number, bar: number): number => {
  const now = levels[Math.min(frame, levels.length - 1)]?.[bar] ?? 0;
  const before = levels[Math.max(0, Math.min(frame, levels.length - 1) - 1)]?.[bar] ?? 0;
  return now >= before ? now : before * 0.6 + now * 0.4;
};

export const Audiogram: React.FC<AudiogramProps> = ({
  words,
  style,
  levels,
  bg,
  accent,
  coverSrc,
  title,
  singleLine = false,
}) => {
  const frame = useCurrentFrame();
  const { width, height } = useVideoConfig();

  const CaptionComponent = {
    hormozi: HormoziCaptions,
    karaoke: KaraokeCaptions,
    subtle: SubtleCaptions,
    branded: BrandedCaptions,
  }[style.name] ?? HormoziCaptions;

  const bars = levels[0]?.length ?? 0;
  // The bars sit above the captions rather than behind them: a waveform under
  // moving text is two things competing for the same pixels.
  const bandHeight = Math.round(height * 0.16);
  const barWidth = bars > 0 ? Math.max(2, Math.floor((width * 0.82) / bars / 1.6)) : 0;
  const gap = bars > 0 ? Math.max(2, Math.floor((width * 0.82 - barWidth * bars) / Math.max(1, bars - 1))) : 0;

  return (
    <AbsoluteFill style={{ backgroundColor: bg }}>
      {coverSrc && (
        <AbsoluteFill style={{ alignItems: "center", justifyContent: "center" }}>
          <Img
            src={coverSrc}
            style={{
              width: "100%",
              height: "100%",
              objectFit: "cover",
              // Far enough back that white captions hold against any artwork.
              filter: "brightness(0.35) saturate(0.9)",
            }}
          />
        </AbsoluteFill>
      )}

      {title && (
        <div
          style={{
            position: "absolute",
            top: Math.round(height * 0.07),
            width: "100%",
            textAlign: "center",
            color: "rgba(255,255,255,0.72)",
            fontFamily: "DM Sans, sans-serif",
            fontWeight: 700,
            fontSize: Math.round(height * 0.022),
            letterSpacing: "0.08em",
            textTransform: "uppercase",
          }}
        >
          {title}
        </div>
      )}

      {bars > 0 && (
        <div
          style={{
            position: "absolute",
            top: Math.round(height * 0.5 - bandHeight / 2),
            left: 0,
            width: "100%",
            height: bandHeight,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            gap,
          }}
        >
          {Array.from({ length: bars }, (_, bar) => {
            const level = smooth(levels, frame, bar);
            return (
              <div
                key={bar}
                style={{
                  width: barWidth,
                  // A floor, so silence is a line rather than nothing at all.
                  height: Math.max(barWidth, Math.round(level * bandHeight)),
                  borderRadius: barWidth / 2,
                  backgroundColor: accent,
                  opacity: 0.55 + level * 0.45,
                }}
              />
            );
          })}
        </div>
      )}

      {style.name === "branded" ? (
        <BrandedCaptions words={words} style={style} singleLine={singleLine} />
      ) : (
        <CaptionComponent words={words} style={style} singleLine={singleLine} />
      )}
    </AbsoluteFill>
  );
};
