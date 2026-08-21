import React from "react";
import { useCurrentFrame, useVideoConfig } from "remotion";
import { captionScale } from "../types";
import { MOTION, motionAt } from "../motion";
import type { Motion } from "../motion";

export interface NameCardProps {
  /** Who is speaking, and what they are. One line each. */
  title: string;
  subtitle?: string;
  /** How long it stays, from the top of the clip. */
  seconds?: number;
  background?: string;
  color?: string;
  accent?: string;
  motion?: Motion;
}

/**
 * The lower third that says who this is.
 *
 * A clip lifted out of an hour of conversation opens on a stranger. Every
 * show solves it the same way and podcli had no answer at all, so the name
 * card was drawn somewhere else and burned in by hand.
 *
 * Anchored to the bottom rather than centred, sized off the composition the
 * way captions are, and gone by the time anyone would tire of it.
 */
export const NameCard: React.FC<NameCardProps> = ({
  title,
  subtitle,
  seconds = 3,
  background = "rgba(0,0,0,0.85)",
  color = "#FFFFFF",
  accent = "#2ED9C3",
  motion,
}) => {
  const frame = useCurrentFrame();
  const { fps, height } = useVideoConfig();
  const s = captionScale(height);

  if (!title) return null;
  if (frame >= seconds * fps) return null;

  const { opacity, shift } = motionAt({
    frame, fps, start: 0, end: seconds,
    motion: motion ?? MOTION.nameCard,
    scale: 12 * s,
  });
  if (opacity <= 0) return null;

  return (
    <div
      style={{
        position: "absolute",
        left: 0,
        bottom: 620 * s,
        maxWidth: "78%",
        padding: `${18 * s}px ${28 * s}px ${16 * s}px`,
        background,
        borderBottom: `${10 * s}px solid ${accent}`,
        opacity,
        // Rises the last few pixels as it arrives, which reads as arriving
        // rather than appearing.
        transform: `translateY(${shift}px)`,
      }}
    >
      <div
        style={{
          fontFamily: "'DM Sans', sans-serif",
          fontSize: 44 * s,
          fontWeight: 700,
          lineHeight: 1.2,
          color,
        }}
      >
        {title}
      </div>
      {subtitle && (
        <div
          style={{
            fontFamily: "'DM Sans', sans-serif",
            fontSize: 38 * s,
            fontWeight: 400,
            lineHeight: 1.25,
            marginTop: 4 * s,
            color,
            opacity: 0.85,
          }}
        >
          {subtitle}
        </div>
      )}
    </div>
  );
};
