import "@fontsource/dm-sans/400.css";
import "@fontsource/dm-sans/700.css";
import React from "react";
import { Composition, continueRender, delayRender, getInputProps } from "remotion";
import { CaptionedClip } from "./CaptionedClip";
import { Bookend } from "./Bookend";
import { STYLES } from "./types";
import type { Word } from "./types";

// @fontsource only injects @font-face CSS and the browser fetches lazily, so
// early frames can rasterize with fallback glyphs. Force the load and block
// rendering on it. fonts.load() is used because fonts.ready resolves
// immediately while no rendered text references the family yet.
const fontsReady = delayRender("Waiting for DM Sans");
Promise.all([
  document.fonts.load("400 16px 'DM Sans'"),
  document.fonts.load("700 16px 'DM Sans'"),
])
  .then(() => document.fonts.ready)
  .then(() => continueRender(fontsReady))
  .catch((err) => {
    // An unfetchable or unparseable woff2 must degrade to the fallback font. An
    // uncleared delayRender fails every frame instead, killing the whole render.
    console.warn("DM Sans failed to load, falling back:", err);
    continueRender(fontsReady);
  });

const inputProps = getInputProps() as {
  videoSrc?: string;
  words?: Word[];
  styleName?: string;
  logoSrc?: string;
  faceY?: number | null;
  durationInFrames?: number;
  fps?: number;
  // Bookend props
  bookendMode?: "intro" | "outro";
  bookendTitle?: string;
  bookendHandle?: string;
  bookendPlatforms?: string[];
  bookendBg?: string;
  bookendAccent?: string;
};

export const RemotionRoot: React.FC = () => {
  const fps = inputProps.fps || 30;
  const durationInFrames = inputProps.durationInFrames || 900;

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
          style: STYLES[inputProps.styleName || "branded"],
          logoSrc: inputProps.logoSrc,
          faceY: inputProps.faceY ?? null,
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
