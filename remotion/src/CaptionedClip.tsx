import React from "react";
import { AbsoluteFill, useVideoConfig } from "remotion";
import { HormoziCaptions } from "./components/HormoziCaptions";
import { KaraokeCaptions } from "./components/KaraokeCaptions";
import { SubtleCaptions } from "./components/SubtleCaptions";
import { BrandedCaptions } from "./components/BrandedCaptions";
import { NameCard } from "./components/NameCard";
import type { NameCardProps } from "./components/NameCard";
import { Watermark } from "./components/Watermark";
import { MOTION } from "./motion";
import type { Motion } from "./motion";
import type { Word, CaptionStyle, CaptionPosition, LogoPosition } from "./types";

export interface CaptionedClipProps {
  videoSrc: string;
  words: Word[];
  style: CaptionStyle;
  logoSrc?: string;
  faceY?: number | null;
  /** Who is speaking, shown for the first few seconds. */
  nameCard?: NameCardProps | null;
  captionPosition?: CaptionPosition;
  logoPosition?: LogoPosition;
  singleLine?: boolean;
  /** Per-part overrides; each part falls back to its style's own motion. */
  motion?: { captions?: Partial<Motion>; nameCard?: Partial<Motion> } | null;
}

export const CaptionedClip: React.FC<CaptionedClipProps> = ({
  words,
  style,
  logoSrc,
  faceY,
  nameCard,
  captionPosition = "auto",
  logoPosition = "top-left",
  singleLine = false,
  motion,
}) => {
  const { height } = useVideoConfig();
  const captionMotion: Motion = {
    ...(MOTION[style.name] ?? MOTION.subtle), ...(motion?.captions ?? {}),
  };
  const cardMotion: Motion = { ...MOTION.nameCard, ...(motion?.nameCard ?? {}) };
  const CaptionComponent = {
    hormozi: HormoziCaptions,
    karaoke: KaraokeCaptions,
    subtle: SubtleCaptions,
    branded: BrandedCaptions,
  }[style.name];

  return (
    <AbsoluteFill style={{ backgroundColor: "transparent" }}>
      <Watermark src={logoSrc} height={height} position={logoPosition} />
      {style.name === "branded" ? (
        <BrandedCaptions words={words} style={style} logoSrc={logoSrc} faceY={faceY}
          captionPosition={captionPosition} logoPosition={logoPosition} singleLine={singleLine} />
      ) : (
        <CaptionComponent words={words} style={style} singleLine={singleLine} motion={captionMotion} />
      )}
      {nameCard?.title && <NameCard {...nameCard} motion={cardMotion} />}
    </AbsoluteFill>
  );
};
