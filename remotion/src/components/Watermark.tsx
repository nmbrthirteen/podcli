import React from "react";
import { Img, staticFile } from "remotion";
import { captionScale, LOGO_EDGE, LOGO_HEIGHT, LOGO_INSET, LOGO_WIDTH } from "../types";
import type { LogoPosition } from "../types";

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
  scale?: number;
}> = ({ src, height, position = "top-left", scale = 1 }) => {
  if (!src) return null;
  const s = captionScale(height);

  return (
    <Img
      src={src.startsWith("http") ? src : staticFile(src)}
      style={{
        position: "absolute",
        ...(position.startsWith("top-")
          ? { top: LOGO_INSET * s }
          : { bottom: LOGO_INSET * s }),
        ...(position.endsWith("-left")
          ? { left: LOGO_EDGE * s }
          : position.endsWith("-right")
            ? { right: LOGO_EDGE * s }
            : { left: "50%", transform: "translateX(-50%)" }),
        width: LOGO_WIDTH * s * scale,
        height: LOGO_HEIGHT * s * scale,
        objectFit: "contain",
      }}
    />
  );
};
