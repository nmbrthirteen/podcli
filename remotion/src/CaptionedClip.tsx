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
import { Cards } from "./components/Cards";
import { cardAt } from "./cards";
import type { Card } from "./cards";
import type { Brand } from "./components/Cards";
import { MOTION, motionAt } from "./motion";
import type { Motion } from "./motion";
import {
  brandCaptions, captionZone, LOGO_CAPTION_GAP, LOGO_HEIGHT, LOGO_INSET, safeFor,
} from "./types";
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
  const { fps, height, width } = useVideoConfig();
  const SAFE = safeFor(width, height);
  const frame = useCurrentFrame();

  const nameCardSeconds = nameCard?.title ? (nameCard.seconds ?? 3) : 0;
  const pastNameCard = frame / fps >= nameCardSeconds;
  const cardPlanned = Boolean(cards?.length);
  const upNow = pastNameCard ? cardAt(cards ?? [], frame / fps) : null;
  /*
   * How far in the card is, rather than whether it is.
   *
   * The card cross-fades over six frames and the captions used to answer on
   * the frame it started: shrinking and jumping while the thing they were
   * getting out of the way of was still arriving. Reading the card's own fade
   * means the two move together, so a card coming up looks like one move
   * instead of a card fading under captions that already snapped.
   */
  const cardIn = upNow
    ? motionAt({
      frame, fps, start: upNow.start, end: upNow.end,
      motion: upNow.motion ?? MOTION.card,
    }).opacity
    : 0;
  const restingShrink = cardPlanned ? 0.75 : 1;
  const captionShrink = restingShrink + (0.6 - restingShrink) * cardIn;
  /*
   * Captions used to drop toward the bottom edge while a card held the frame,
   * on the reasoning that there is no chin down there to clear. There is no
   * chin, but there is a YouTube title, a handle and a link chip, and the
   * margin that bought the speaker a taller band was spending the one part of
   * the frame the viewer never sees. They hold above the chrome now, and the
   * band the speaker lost is taken off its own floor instead.
   */
  /*
   * A four-stop placement model, easier to reason about than pixels.
   *
   * The three named stops are fractions of the frame and travel between
   * shapes on their own. Auto does not. A style's own margin was authored
   * against a 1920-tall phone, and the same number on a 1080-tall landscape
   * frame is half the picture. Off a phone, auto means as low as the
   * furniture allows.
   */
  const placementMargin = captionPosition === "upper" ? 1120
    : captionPosition === "center" ? 820
      : captionPosition === "lower" ? SAFE.bottom
        : width < height ? style.marginBottom : SAFE.bottom;
  const captionStyle: CaptionStyle = {
    ...brandCaptions(style, brand),
    marginBottom: Math.max(placementMargin, SAFE.bottom),
    fontSize: style.fontSize * captionSize * captionShrink,
  };

  /*
   * What the card lays itself out against.
   *
   * Not the caption that is halfway through shrinking. The reserved band is a
   * function of the caption's size, so handing the card the interpolating one
   * made its whole body creep and resize through the six frames of its own
   * fade. It reserves for the settled size instead: the card holds still and
   * only the captions move.
   */
  const settledStyle: CaptionStyle = {
    ...captionStyle,
    fontSize: style.fontSize * captionSize * 0.6,
  };

  /*
   * Who owns the top corners, so nothing else writes into them.
   *
   * The logo and the chip both default to the same corner at the same inset,
   * which drew one straight through the other on any clip carrying both. The
   * chip drops below the logo when they collide, and a card given the whole
   * frame is told how much of the top is already spoken for.
   */
  const logoTop = Boolean(logoSrc) && logoPosition.startsWith("top-");
  const chipCorner = topic?.position ?? "top-left";
  const chipClashes = logoTop
    && chipCorner.startsWith("top-")
    && chipCorner.slice(4) === logoPosition.slice(4);
  const chipInset = LOGO_INSET
    + (chipClashes ? LOGO_HEIGHT * logoScale + LOGO_CAPTION_GAP : 0);
  const topTaken = logoTop || chipCorner.startsWith("top-")
    ? chipInset + (chipClashes ? 48 : LOGO_HEIGHT * logoScale) + LOGO_CAPTION_GAP
    : SAFE.top;

  const captionMotion: Motion = {
    ...(MOTION[style.name] ?? MOTION.subtle), ...(motion?.captions ?? {}),
  };
  const cardMotion: Motion = { ...MOTION.nameCard, ...(motion?.nameCard ?? {}) };
  const CaptionComponent = {
    hormozi: HormoziCaptions,
    karaoke: KaraokeCaptions,
    subtle: SubtleCaptions,
    branded: BrandedCaptions,
    outline: SubtleCaptions,
  }[style.name];

  return (
    <AbsoluteFill style={{ backgroundColor: "transparent" }}>
      {/* First, so everything below stays up while a card holds the frame. */}
      {cards && cards.length > 0 && pastNameCard && (
        <Cards
          cards={cards}
          videoSrc={videoSrc}
          startFrom={startFrom}
          style={settledStyle}
          topInset={topTaken}
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
      {nameCard?.title && (
        <NameCard
          {...nameCard}
          bottom={nameCard.bottom ?? captionZone(captionStyle) + 24}
          motion={cardMotion}
        />
      )}
      {topic?.label && <TopicChip {...topic} inset={chipInset} />}
      {progress && <ProgressBar {...progress} />}
    </AbsoluteFill>
  );
};
