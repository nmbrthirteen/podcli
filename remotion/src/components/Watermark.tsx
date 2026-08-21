import React from "react";
import { Img, staticFile } from "remotion";
import type { LogoPosition } from "../types";
import { captionScale } from "../types";

// Unscaled logo box, shared with BrandedCaptions' caption-margin guard so the
// mark and the gap left for it cannot drift apart.
export const LOGO_INSET = 180;
export const LOGO_HEIGHT = 126;
export const LOGO_CAPTION_GAP = 24;

/**
 * The show's logo, on every clip.
 *
 * It used to live inside the branded caption component, which meant `--logo`
 * did nothing at all on the other three styles: the flag was accepted, the
 * file was resolved, and the renderer dropped it. Same position and size as
 * before, one level up, so a branded render is unchanged and the rest finally
 * carry the mark they were told to.
 */
export const Watermark: React.FC<{
  src?: string;
  height: number;
  position?: LogoPosition;
}> = ({ src, height, position = "top-left" }) => {
  if (!src) return null;
  const s = captionScale(height);

  return (
    <Img
      src={src.startsWith("http") ? src : staticFile(src)}
      style={{
        position: "absolute",
        ...(position.startsWith("top-") ? { top: LOGO_INSET * s } : { bottom: LOGO_INSET * s }),
        ...(position.endsWith("-left")
          ? { left: 108 * s }
          : position.endsWith("-right")
            ? { right: 108 * s }
            : { left: "50%", transform: "translateX(-50%)" }),
        width: 255 * s,
        height: LOGO_HEIGHT * s,
        objectFit: "contain",
      }}
    />
  );
};
