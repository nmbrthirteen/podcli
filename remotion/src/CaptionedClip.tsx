import React from "react";
import { AbsoluteFill, OffthreadVideo, staticFile } from "remotion";
import { HormoziCaptions } from "./components/HormoziCaptions";
import { KaraokeCaptions } from "./components/KaraokeCaptions";
import { SubtleCaptions } from "./components/SubtleCaptions";
import { BrandedCaptions } from "./components/BrandedCaptions";
import type { Word, CaptionStyle } from "./types";

export interface CaptionedClipProps {
  videoSrc: string;
  words: Word[];
  style: CaptionStyle;
  logoSrc?: string;
}

export const CaptionedClip: React.FC<CaptionedClipProps> = ({
  videoSrc,
  words,
  style,
  logoSrc,
}) => {
  const CaptionComponent = {
    hormozi: HormoziCaptions,
    karaoke: KaraokeCaptions,
    subtle: SubtleCaptions,
    branded: BrandedCaptions,
  }[style.name];

  // Render captions only (transparent bg) — FFmpeg composites onto video later
  // This is 10x faster than decoding video through Chrome
  return (
    <AbsoluteFill style={{ backgroundColor: "transparent" }}>
      {style.name === "branded" ? (
        <BrandedCaptions words={words} style={style} logoSrc={logoSrc} />
      ) : (
        <CaptionComponent words={words} style={style} />
      )}
    </AbsoluteFill>
  );
};
