import React from "react";
import { useCurrentFrame, useVideoConfig } from "remotion";
import { captionScale } from "../types";

export interface ProgressBarProps {
  color?: string;
  /** The unfilled remainder. Transparent leaves the video showing through. */
  track?: string;
  /** Bar thickness in unscaled units. */
  thickness?: number;
}

/**
 * How much of the clip is left, as a line across the bottom.
 *
 * A viewer deciding whether to keep watching is asking how long this goes on
 * for, and a clip that answers keeps them past the first seconds. The player's
 * own scrubber does not: it belongs to the feed, it is not always drawn, and
 * on a reposted file it is gone.
 *
 * Anchored to the very bottom edge rather than inset, because a progress bar
 * that floats reads as a design element and one on the edge reads as a fact.
 */
export const ProgressBar: React.FC<ProgressBarProps> = ({
  color = "#3B9CFF",
  track = "rgba(255,255,255,0.15)",
  thickness = 8,
}) => {
  const frame = useCurrentFrame();
  const { durationInFrames, height } = useVideoConfig();
  const s = captionScale(height);

  // The last frame should read as full rather than as one frame short.
  const progress = durationInFrames > 1
    ? Math.min(1, frame / (durationInFrames - 1))
    : 1;

  return (
    <div
      style={{
        position: "absolute",
        left: 0,
        right: 0,
        bottom: 0,
        height: thickness * s,
        backgroundColor: track,
      }}
    >
      <div
        style={{
          width: `${progress * 100}%`,
          height: "100%",
          backgroundColor: color,
        }}
      />
    </div>
  );
};
