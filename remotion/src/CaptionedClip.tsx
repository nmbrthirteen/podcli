import React from "react";
import { AbsoluteFill, useVideoConfig } from "remotion";
import { HormoziCaptions } from "./components/HormoziCaptions";
import { KaraokeCaptions } from "./components/KaraokeCaptions";
import { SubtleCaptions } from "./components/SubtleCaptions";
import { BrandedCaptions } from "./components/BrandedCaptions";
import { NameCard } from "./components/NameCard";
import type { NameCardProps } from "./components/NameCard";
import { Watermark } from "./components/Watermark";
import type { Word, CaptionStyle } from "./types";

export interface CaptionedClipProps {
  videoSrc: string;
  words: Word[];
  style: CaptionStyle;
  logoSrc?: string;
  faceY?: number | null;
  /** Who is speaking, shown for the first few seconds. */
  nameCard?: NameCardProps | null;
}

export const CaptionedClip: React.FC<CaptionedClipProps> = ({
  words,
  style,
  logoSrc,
  faceY,
  nameCard,
}) => {
  const { height } = useVideoConfig();
  const CaptionComponent = {
    hormozi: HormoziCaptions,
    karaoke: KaraokeCaptions,
    subtle: SubtleCaptions,
    branded: BrandedCaptions,
  }[style.name];

  return (
    <AbsoluteFill style={{ backgroundColor: "transparent" }}>
      <Watermark src={logoSrc} height={height} />
      {style.name === "branded" ? (
        <BrandedCaptions words={words} style={style} faceY={faceY} />
      ) : (
        <CaptionComponent words={words} style={style} />
      )}
      {nameCard?.title && <NameCard {...nameCard} />}
    </AbsoluteFill>
  );
};
