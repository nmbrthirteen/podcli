import React from "react";
import { Composition, continueRender, delayRender, getInputProps } from "remotion";
import { CaptionedClip } from "./CaptionedClip";
import { Bookend } from "./Bookend";
import { Audiogram } from "./Audiogram";
import { fontStack, STYLES } from "./types";
import type { Word } from "./types";
import type { CaptionPosition, LogoPosition } from "./types";
import type { TopicChipProps } from "./components/TopicChip";
import type { ProgressBarProps } from "./components/ProgressBar";
import type { Brand } from "./components/Cards";
import type { Card } from "./cards";
import dmSans400 from "@fontsource/dm-sans/files/dm-sans-latin-400-normal.woff2";
import dmSans700 from "@fontsource/dm-sans/files/dm-sans-latin-700-normal.woff2";

const fontsReady = delayRender("Waiting for DM Sans");

const loadFace = (source: string, weight: string) =>
  new FontFace("DM Sans", `url(${source})`, {
    weight,
    style: "normal",
    display: "swap",
  })
    .load()
    .then((face) => {
      document.fonts.add(face);
    });

Promise.all([loadFace(dmSans400 as string, "400"), loadFace(dmSans700 as string, "700")])
  .then(() => continueRender(fontsReady))
  .catch((err) => {
    console.warn("DM Sans failed to load, falling back:", err);
    continueRender(fontsReady);
  });

const inputProps = getInputProps() as {
  videoSrc?: string;
  words?: Word[];
  styleName?: string;
  logoSrc?: string;
  faceY?: number | null;
  captionPosition?: CaptionPosition;
  captionFontScale?: number;
  logoPosition?: LogoPosition;
  logoScale?: number;
  singleLine?: boolean;
  levels?: number[][];
  coverSrc?: string;
  audiogramBg?: string;
  audiogramAccent?: string;
  audiogramTitle?: string;
  durationInFrames?: number;
  fps?: number;
  bookendMode?: "intro" | "outro";
  bookendTitle?: string;
  bookendHandle?: string;
  bookendPlatforms?: string[];
  bookendBg?: string;
  bookendAccent?: string;
  nameCard?: {
    title: string;
    subtitle?: string;
    seconds?: number;
    background?: string;
    color?: string;
    accent?: string;
  } | null;
  motion?: {
    captions?: Record<string, unknown>;
    nameCard?: Record<string, unknown>;
  } | null;
  /**
   * The parts a show's template switches on, and the colours it draws them in.
   *
   * Every one is optional and absent means "draw nothing", so a render that
   * sends none of them produces exactly the clip it produced before these
   * existed.
   */
  topic?: TopicChipProps | null;
  progress?: ProgressBarProps | null;
  cards?: Card[] | null;
  brand?: Brand | null;
  /** The show's own face, ahead of the stack that covers what it cannot draw. */
  fontFamily?: string | null;
};

export const RemotionRoot: React.FC = () => {
  const fps = inputProps.fps || 30;
  const durationInFrames = inputProps.durationInFrames || 900;
  const baseStyle = STYLES[inputProps.styleName || "branded"];
  const positionMargins: Partial<Record<CaptionPosition, number>> = {
    upper: 760,
    center: 480,
    lower: 220,
  };
  const captionPosition = inputProps.captionPosition || "auto";
  const fontScale = Math.max(0.6, Math.min(1.6, (inputProps.captionFontScale || 100) / 100));
  const style = {
    ...baseStyle,
    fontSize: baseStyle.fontSize * fontScale,
    marginBottom: positionMargins[captionPosition] ?? baseStyle.marginBottom,
    fontFamily: fontStack(inputProps.fontFamily),
  };

  return (
    <>
      <Composition
        id="CaptionedClip"
        component={CaptionedClip}
        durationInFrames={durationInFrames}
        fps={fps}
        width={1080}
        height={1920}
        defaultProps={{
          videoSrc: inputProps.videoSrc || "",
          words: inputProps.words || [],
          style,
          logoSrc: inputProps.logoSrc,
          faceY: inputProps.faceY ?? null,
          nameCard: inputProps.nameCard ?? null,
          captionPosition,
          logoPosition: inputProps.logoPosition || "top-left",
          logoScale: Math.max(0.5, Math.min(1.5, inputProps.logoScale || 1)),
          singleLine: inputProps.singleLine === true,
          motion: inputProps.motion ?? null,
          topic: inputProps.topic ?? null,
          progress: inputProps.progress ?? null,
          cards: inputProps.cards ?? null,
          brand: inputProps.brand ?? null,
        }}
      />
      <Composition
        id="Audiogram"
        component={Audiogram}
        durationInFrames={durationInFrames}
        fps={fps}
        width={1080}
        height={1920}
        defaultProps={{
          words: inputProps.words || [],
          style,
          levels: inputProps.levels || [],
          bg: inputProps.audiogramBg || "#0B0B0F",
          accent: inputProps.audiogramAccent || "#FFE000",
          coverSrc: inputProps.coverSrc,
          title: inputProps.audiogramTitle,
          singleLine: inputProps.singleLine === true,
        }}
      />
      <Composition
        id="Bookend"
        component={Bookend}
        durationInFrames={durationInFrames}
        fps={fps}
        width={1080}
        height={1920}
        defaultProps={{
          mode: inputProps.bookendMode || "outro",
          title: inputProps.bookendTitle || "Follow for more",
          handle: inputProps.bookendHandle,
          platforms: inputProps.bookendPlatforms || ["tiktok", "instagram", "youtube", "x"],
          bg: inputProps.bookendBg || "#0B0B0F",
          accent: inputProps.bookendAccent || "#FFE000",
        }}
      />
    </>
  );
};
