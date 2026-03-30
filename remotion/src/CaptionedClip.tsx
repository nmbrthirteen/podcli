import React from "react";
import { AbsoluteFill } from "remotion";
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
  faceY?: number | null;
}

export const CaptionedClip: React.FC<CaptionedClipProps> = ({
  words,
  style,
  logoSrc,
  faceY,
}) => {
  const CaptionComponent = {
    hormozi: HormoziCaptions,
    karaoke: KaraokeCaptions,
    subtle: SubtleCaptions,
    branded: BrandedCaptions,
  }[style.name];

  return (
    <AbsoluteFill style={{ backgroundColor: "transparent" }}>
      {style.name === "branded" ? (
        <BrandedCaptions words={words} style={style} logoSrc={logoSrc} faceY={faceY} />
      ) : (
        <CaptionComponent words={words} style={style} />
      )}
    </AbsoluteFill>
  );
};
