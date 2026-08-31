import React from "react";
import { useVideoConfig } from "remotion";
import { captionScale, FONT, LOGO_EDGE, LOGO_INSET } from "../types";
import type { LogoPosition } from "../types";

export interface TopicChipProps {
  /** What the clip is about, in two or three words. */
  label: string;
  /** Where it sits. Shares LogoPosition so a template names corners once. */
  position?: LogoPosition;
  color?: string;
  /** Drawn behind the label. Transparent by default, which is the reference look. */
  background?: string;
}

/**
 * A standing label saying what this clip is about.
 *
 * Every produced short carries one and no cut of a podcast does, which is most
 * of why a raw clip reads as a raw clip. It holds still for the whole clip on
 * purpose: it is furniture, not a caption, and anything that moves up there
 * competes with the words.
 *
 * Small, letterspaced and upper case, so it reads as a label rather than as a
 * sentence somebody forgot to finish.
 */
export const TopicChip: React.FC<TopicChipProps> = ({
  label,
  position = "top-left",
  color = "#FFFFFF",
  background = "transparent",
}) => {
  const { height } = useVideoConfig();
  const s = captionScale(height);

  if (!label) return null;

  const padded = background !== "transparent";

  return (
    <div
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
        fontFamily: FONT,
        fontSize: 30 * s,
        fontWeight: 700,
        letterSpacing: 3 * s,
        textTransform: "uppercase",
        color,
        background,
        ...(padded
          ? { padding: `${10 * s}px ${20 * s}px`, borderRadius: 10 * s }
          : { textShadow: "0 2px 12px rgba(0,0,0,0.8)" }),
      }}
    >
      {label}
    </div>
  );
};
