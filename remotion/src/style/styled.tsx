import React from "react";
import { useCurrentFrame, useVideoConfig } from "remotion";
import { captionScale } from "../types";
import type { CaptionStyle, Word } from "../types";
import { cardAt } from "../cards";
import type { Card } from "../cards";
import { activeChunkAt, buildChunks } from "../chunks";
import { fontOf } from "./theme";
import type { Theme } from "./theme";
import { Label, Paper } from "./materials";
import { progress, Reveal } from "./reveal";
import { TakeoverPage } from "./pages";

export const StyledCards: React.FC<{ cards: Card[]; theme: Theme; topInset?: number }> = ({ cards, theme, topInset = 0 }) => {
  const frame = useCurrentFrame();
  const { fps, height } = useVideoConfig();
  const card = cardAt(cards.filter((c) => theme.layout[c.kind] === "takeover"), frame / fps);
  if (!card) return null;
  const start = Math.round(card.start * fps);
  return (
    <TakeoverPage card={card} theme={theme} s={captionScale(height)} start={start}
      length={Math.round(card.end * fps) - start} inset={topInset} />
  );
};

export const StyledNameCard: React.FC<{
  theme: Theme;
  title: string;
  subtitle?: string;
  seconds: number;
  bottom: number;
}> = ({ theme, title, subtitle, seconds, bottom }) => {
  const frame = useCurrentFrame();
  const { fps, height } = useVideoConfig();
  const s = captionScale(height);
  const end = Math.round(seconds * fps);
  const out = progress(theme.motion, frame, end - 6, 6);
  if (frame < 8 || out >= 1) return null;
  return (
    <Reveal theme={theme} role="name" f={frame} at={8} fps={fps} style={{
      left: 60 * s, bottom, display: "flex", flexDirection: "column",
      alignItems: "flex-start", gap: 6 * s, opacity: 1 - out,
      textShadow: theme.surface === "none" ? `0 ${2 * s}px ${14 * s}px rgba(0,0,0,0.8)` : undefined,
    }}>
      <Label theme={theme} seed={71} scale={s} size={92 * s} ink={theme.surface === "none" ? theme.color.ink : undefined}
        style={{ transform: `rotate(${-theme.tilt}deg)` }}>
        {title}
      </Label>
      {subtitle && (
        <Paper theme={theme} seed={72} color={theme.color.stripAlt} scale={s}
          style={{ marginLeft: theme.surface === "none" ? 0 : 30 * s, transform: `rotate(${theme.tilt * 0.6}deg)` }}>
          <div style={{ ...fontOf(theme.type.label, 42 * s), color: theme.surface === "none" ? theme.color.accent : theme.color.ink,
            padding: theme.surface === "none" ? 0 : `${12 * s}px ${26 * s}px` }}>
            {subtitle}
          </div>
        </Paper>
      )}
    </Reveal>
  );
};

export const StyledCaptions: React.FC<{
  theme: Theme;
  words: Word[];
  style: CaptionStyle;
}> = ({ theme, words, style }) => {
  const frame = useCurrentFrame();
  const { fps, height, durationInFrames } = useVideoConfig();
  const s = captionScale(height);
  const now = frame / fps;
  const chunks = buildChunks(words, {
    perChunk: style.wordsPerChunk, absorbTail: 1, clipEnd: durationInFrames / fps,
  });
  const chunk = activeChunkAt(chunks, now);
  if (!chunk) return null;
  const seed = Math.round(chunk.start * 31);
  const size = theme.type.caption * s;
  const bare = theme.surface === "none";
  const at = Math.round(chunk.start * fps);

  return (
    <Reveal theme={theme} role="caption" f={frame} at={at} fps={fps}
      style={{ left: 0, right: 0, bottom: style.marginBottom * s, display: "flex", justifyContent: "center" }}>
      <Paper theme={theme} seed={seed} color={theme.color.sheet} scale={s} strength={0.3}
        style={{ maxWidth: 880 * s, transform: `rotate(${((seed % 3) - 1) * theme.tilt * 0.4}deg)` }}>
        <div style={{ ...fontOf(theme.type.label, size), fontWeight: 700, color: theme.color.ink,
          padding: `${14 * s}px ${30 * s}px ${10 * s}px`, textAlign: "center",
          textShadow: bare ? `0 ${2 * s}px ${12 * s}px rgba(0,0,0,0.85), 0 0 ${3 * s}px rgba(0,0,0,0.6)` : undefined }}>
          {chunk.words.map((w, i) => {
            const on = now >= w.start && now < w.end;
            return (
              <React.Fragment key={i}>
                {i > 0 ? " " : ""}
                <span style={bare ? { color: on ? theme.color.accent : undefined } : {
                  backgroundColor: on ? theme.color.highlight : undefined,
                  boxShadow: on ? `0 0 0 ${6 * s}px ${theme.color.highlight}` : undefined,
                }}>{w.word}</span>
              </React.Fragment>
            );
          })}
        </div>
      </Paper>
    </Reveal>
  );
};
