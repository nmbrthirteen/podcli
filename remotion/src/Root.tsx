import "@fontsource/dm-sans/400.css";
import "@fontsource/dm-sans/700.css";
import React from "react";
import { Composition, getInputProps } from "remotion";
import { CaptionedClip } from "./CaptionedClip";
import { STYLES } from "./types";
import type { Word } from "./types";

const inputProps = getInputProps() as {
  videoSrc?: string;
  words?: Word[];
  styleName?: string;
  logoSrc?: string;
  faceY?: number | null;
  durationInFrames?: number;
  fps?: number;
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
    </>
  );
};
