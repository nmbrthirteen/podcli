import React from "react";
import { AbsoluteFill, interpolate, spring, useCurrentFrame, useVideoConfig } from "remotion";

export interface BookendProps {
  mode: "intro" | "outro";
  title: string;        // intro: the headline; outro: the call-to-action ("Follow for more")
  handle?: string;      // e.g. "@yourbrand"
  platforms: string[];  // subset of: tiktok, instagram, youtube, x
  bg?: string;          // background color
  accent?: string;      // accent color (active word / underline / icons)
}

const FONT = "'DM Sans', sans-serif";

// --- Recognizable social glyphs as inline SVG (monochrome, fill=currentColor) ---
const Glyph: React.FC<{ name: string; size: number; color: string }> = ({ name, size, color }) => {
  const common = { width: size, height: size, viewBox: "0 0 24 24", fill: color } as const;
  switch (name) {
    case "tiktok":
      return (
        <svg {...common}>
          <path d="M16.6 5.82A4.28 4.28 0 0 1 15.54 3h-3.1v12.4a2.59 2.59 0 1 1-2.59-2.59c.27 0 .53.04.78.12V9.77a5.7 5.7 0 0 0-.78-.05A5.69 5.69 0 1 0 15.54 15.4V9.01a7.34 7.34 0 0 0 4.3 1.38V7.3a4.28 4.28 0 0 1-3.24-1.48z" />
        </svg>
      );
    case "instagram":
      return (
        <svg {...common}>
          <path d="M12 2.16c3.2 0 3.58.01 4.85.07 1.17.05 1.8.25 2.23.41.56.22.96.48 1.38.9.42.42.68.82.9 1.38.16.42.36 1.06.41 2.23.06 1.27.07 1.65.07 4.85s-.01 3.58-.07 4.85c-.05 1.17-.25 1.8-.41 2.23-.22.56-.48.96-.9 1.38-.42.42-.82.68-1.38.9-.42.16-1.06.36-2.23.41-1.27.06-1.65.07-4.85.07s-3.58-.01-4.85-.07c-1.17-.05-1.8-.25-2.23-.41a3.7 3.7 0 0 1-1.38-.9 3.7 3.7 0 0 1-.9-1.38c-.16-.42-.36-1.06-.41-2.23C2.17 15.58 2.16 15.2 2.16 12s.01-3.58.07-4.85c.05-1.17.25-1.8.41-2.23.22-.56.48-.96.9-1.38.42-.42.82-.68 1.38-.9.42-.16 1.06-.36 2.23-.41C8.42 2.17 8.8 2.16 12 2.16zm0 3.68A6.16 6.16 0 1 0 18.16 12 6.16 6.16 0 0 0 12 5.84zm0 10.16A4 4 0 1 1 16 12a4 4 0 0 1-4 4zm6.4-10.4a1.44 1.44 0 1 1-1.44-1.44 1.44 1.44 0 0 1 1.44 1.44z" />
        </svg>
      );
    case "youtube":
      return (
        <svg {...common}>
          <path d="M23.5 6.2a3 3 0 0 0-2.1-2.1C19.5 3.5 12 3.5 12 3.5s-7.5 0-9.4.6A3 3 0 0 0 .5 6.2 31 31 0 0 0 0 12a31 31 0 0 0 .5 5.8 3 3 0 0 0 2.1 2.1c1.9.6 9.4.6 9.4.6s7.5 0 9.4-.6a3 3 0 0 0 2.1-2.1A31 31 0 0 0 24 12a31 31 0 0 0-.5-5.8zM9.6 15.6V8.4l6.3 3.6z" />
        </svg>
      );
    case "x":
      return (
        <svg {...common}>
          <path d="M18.9 2H22l-7.1 8.1L23.3 22h-6.6l-5.2-6.8L5.6 22H2.5l7.6-8.7L1 2h6.8l4.7 6.2zm-1.1 18h1.8L7.3 3.9H5.4z" />
        </svg>
      );
    default:
      return null;
  }
};

const PLATFORM_LABEL: Record<string, string> = {
  tiktok: "TikTok",
  instagram: "Instagram",
  youtube: "YouTube",
  x: "X",
};

export const Bookend: React.FC<BookendProps> = ({
  mode,
  title,
  handle,
  platforms,
  bg = "#0B0B0F",
  accent = "#FFE000",
}) => {
  const frame = useCurrentFrame();
  const { fps, width, height } = useVideoConfig();

  // Title slides up + fades in
  const titleProgress = spring({ frame, fps, config: { damping: 200 }, durationInFrames: Math.round(fps * 0.6) });
  const titleY = interpolate(titleProgress, [0, 1], [60, 0]);
  const titleOpacity = interpolate(titleProgress, [0, 1], [0, 1]);

  // Icons pop in staggered, after the title
  const iconBaseFrame = Math.round(fps * 0.5);

  const iconSize = Math.round(width * 0.085);

  return (
    <AbsoluteFill
      style={{
        backgroundColor: bg,
        fontFamily: FONT,
        justifyContent: "center",
        alignItems: "center",
        padding: width * 0.08,
      }}
    >
      {/* Accent bar */}
      <div
        style={{
          width: interpolate(titleProgress, [0, 1], [0, width * 0.18]),
          height: 10,
          backgroundColor: accent,
          borderRadius: 5,
          marginBottom: height * 0.04,
        }}
      />

      {/* Title / CTA */}
      <div
        style={{
          transform: `translateY(${titleY}px)`,
          opacity: titleOpacity,
          color: "#FFFFFF",
          fontSize: mode === "outro" ? width * 0.12 : width * 0.095,
          fontWeight: 700,
          lineHeight: 1.05,
          textAlign: "center",
          letterSpacing: -1,
          textTransform: mode === "outro" ? "none" : "none",
        }}
      >
        {title}
      </div>

      {handle && (
        <div
          style={{
            opacity: titleOpacity,
            color: accent,
            fontSize: width * 0.06,
            fontWeight: 700,
            marginTop: height * 0.02,
          }}
        >
          {handle}
        </div>
      )}

      {/* Social icons (outro emphasises them; intro shows them small if present) */}
      {platforms.length > 0 && (
        <div
          style={{
            display: "flex",
            gap: width * 0.05,
            marginTop: height * 0.05,
            alignItems: "center",
            justifyContent: "center",
            flexWrap: "wrap",
          }}
        >
          {platforms.map((p, i) => {
            const f = frame - (iconBaseFrame + i * Math.round(fps * 0.12));
            const pop = spring({ frame: Math.max(0, f), fps, config: { damping: 12, mass: 0.6 }, durationInFrames: Math.round(fps * 0.5) });
            const scale = interpolate(pop, [0, 1], [0.2, 1]);
            const op = interpolate(pop, [0, 1], [0, 1]);
            return (
              <div
                key={p}
                style={{
                  display: "flex",
                  flexDirection: "column",
                  alignItems: "center",
                  gap: 8,
                  transform: `scale(${scale})`,
                  opacity: op,
                }}
              >
                <div
                  style={{
                    width: iconSize * 1.6,
                    height: iconSize * 1.6,
                    borderRadius: iconSize * 0.4,
                    backgroundColor: "rgba(255,255,255,0.08)",
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                  }}
                >
                  <Glyph name={p} size={iconSize} color="#FFFFFF" />
                </div>
                <div style={{ color: "rgba(255,255,255,0.7)", fontSize: width * 0.028, fontWeight: 700 }}>
                  {PLATFORM_LABEL[p] || p}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </AbsoluteFill>
  );
};
