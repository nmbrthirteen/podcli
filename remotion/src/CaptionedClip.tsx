import React from "react";
import { AbsoluteFill, useCurrentFrame, useVideoConfig } from "remotion";
import { HormoziCaptions } from "./components/HormoziCaptions";
import { KaraokeCaptions } from "./components/KaraokeCaptions";
import { SubtleCaptions } from "./components/SubtleCaptions";
import { BrandedCaptions } from "./components/BrandedCaptions";
import { NameCard } from "./components/NameCard";
import type { NameCardProps } from "./components/NameCard";
import { Watermark } from "./components/Watermark";
import { TopicChip } from "./components/TopicChip";
import type { TopicChipProps } from "./components/TopicChip";
import { ProgressBar } from "./components/ProgressBar";
import type { ProgressBarProps } from "./components/ProgressBar";
import { Cards, CARD_CAPTION_MARGIN } from "./components/Cards";
import { cardAt } from "./cards";
import type { Card } from "./cards";
import type { Brand } from "./components/Cards";
import { MOTION } from "./motion";
import type { Motion } from "./motion";
import type { Word, CaptionStyle, CaptionPosition, LogoPosition } from "./types";

export interface CaptionedClipProps {
  videoSrc: string;
  words: Word[];
  style: CaptionStyle;
  logoSrc?: string;
  faceY?: number | null;
  /** Face height as a fraction of the frame, when one was measured. */
  faceH?: number | null;
  captionPosition?: CaptionPosition;
  captionScale?: number;
  logoPosition?: LogoPosition;
  logoScale?: number;
  singleLine?: boolean;
  /** Who is speaking, shown for the first few seconds. */
  nameCard?: NameCardProps | null;
  /** A standing label saying what the clip is about. Null draws nothing. */
  topic?: TopicChipProps | null;
  /** How much of the clip is left. Null draws nothing. */
  progress?: ProgressBarProps | null;
  /** Cards that take the frame for a window each. Empty draws nothing. */
  cards?: Card[] | null;
  /** Where in the source file this composition begins, in frames. */
  startFrom?: number;
  /** The show's colours, for anything the renderer draws itself. */
  brand?: Brand | null;
  /** Per-part overrides; each part falls back to its style's own motion. */
  motion?: { captions?: Partial<Motion>; nameCard?: Partial<Motion> } | null;
}

export const CaptionedClip: React.FC<CaptionedClipProps> = ({
  videoSrc,
  words,
  style,
  logoSrc,
  faceY,
  faceH,
  captionPosition = "auto",
  captionScale: captionSize = 1,
  logoPosition = "top-left",
  logoScale = 1,
  singleLine = false,
  nameCard,
  topic,
  progress,
  cards,
  startFrom = 0,
  brand,
  motion,
}) => {
  const { fps, height } = useVideoConfig();
  const frame = useCurrentFrame();

  /*
   * Captions drop toward the bottom edge while a card holds the frame.
   *
   * Their usual margin keeps them clear of a speaker's chin and hands. There
   * is no chin down there when a card is up, so the margin is only empty
   * surface, and it was the single biggest thing pushing the speaker's band
   * short enough to cut a face in half.
   */
  const nameCardSeconds = nameCard?.title ? (nameCard.seconds ?? 3) : 0;
  const pastNameCard = frame / fps >= nameCardSeconds;
  const cardUp = pastNameCard && Boolean(cards?.length) && Boolean(cardAt(cards ?? [], frame / fps));
  const cardPlanned = Boolean(cards?.length);
  const captionShrink = cardUp ? 0.6 : cardPlanned ? 0.75 : 1;
  const baseCaptionStyle: CaptionStyle = cardUp
    ? { ...style, marginBottom: Math.min(style.marginBottom, CARD_CAPTION_MARGIN) }
    : style;
  // Keep a simple four-stop placement model. It is easier to reason about
  // than pixels, and stays proportional across every output shape.
  const placementMargin = captionPosition === "upper" ? 760
    : captionPosition === "center" ? 480
      : captionPosition === "lower" ? 220
        : baseCaptionStyle.marginBottom;
  const captionStyle: CaptionStyle = {
    ...baseCaptionStyle,
    marginBottom: placementMargin,
    fontSize: baseCaptionStyle.fontSize * captionSize * captionShrink,
  };

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
      {/* First, so everything below stays up while a card holds the frame. */}
      {cards && cards.length > 0 && pastNameCard && (
        <Cards
          cards={cards}
          videoSrc={videoSrc}
          startFrom={startFrom}
          style={captionStyle}
          faceY={faceY}
          faceH={faceH}
          brand={brand}
        />
      )}
      <Watermark src={logoSrc} height={height} position={logoPosition} scale={logoScale} />
      {style.name === "branded" ? (
        <BrandedCaptions words={words} style={captionStyle} faceY={faceY}
          captionPosition={captionPosition} hasLogo={Boolean(logoSrc)}
          logoPosition={logoPosition} singleLine={singleLine} />
      ) : (
        <CaptionComponent words={words} style={captionStyle} motion={captionMotion}
          singleLine={singleLine} />
      )}
      {nameCard?.title && <NameCard {...nameCard} motion={cardMotion} />}
      {topic?.label && <TopicChip {...topic} />}
      {progress && <ProgressBar {...progress} />}
    </AbsoluteFill>
  );
};
