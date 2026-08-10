import React from "react";
import { Img, staticFile } from "remotion";
import { captionScale } from "../types";

/**
 * The show's logo, on every clip.
 *
 * It used to live inside the branded caption component, which meant `--logo`
 * did nothing at all on the other three styles: the flag was accepted, the
 * file was resolved, and the renderer dropped it. Same position and size as
 * before, one level up, so a branded render is unchanged and the rest finally
 * carry the mark they were told to.
 */
export const Watermark: React.FC<{ src?: string; height: number }> = ({ src, height }) => {
  if (!src) return null;
  const s = captionScale(height);

  return (
    <Img
      src={src.startsWith("http") ? src : staticFile(src)}
      style={{
        position: "absolute",
        top: 180 * s,
        left: 108 * s,
        width: 255 * s,
        height: 126 * s,
        objectFit: "contain",
      }}
    />
  );
};
