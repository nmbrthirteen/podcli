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

  return (
    <AbsoluteFill style={{ backgroundColor: "black" }}>
      {videoSrc ? (
        <OffthreadVideo
          src={videoSrc.startsWith("http") ? videoSrc : staticFile(videoSrc)}
          style={{ width: "100%", height: "100%", objectFit: "cover" }}
        />
      ) : null}
      {style.name === "branded" ? (
        <BrandedCaptions words={words} style={style} logoSrc={logoSrc} />
      ) : (
        <CaptionComponent words={words} style={style} />
      )}
    </AbsoluteFill>
  );
};
