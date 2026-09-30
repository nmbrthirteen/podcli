import React from "react";
import { Easing, interpolate, spring } from "remotion";
import type { Entrance, Motion, Role, Side, Theme } from "./theme";

export const held = (motion: Motion, frame: number) => frame - (frame % Math.max(1, motion.holdEvery));

const easing = (motion: Motion) =>
  motion.ease === "in-out" ? Easing.inOut(Easing.cubic) : motion.ease === "linear" ? Easing.linear : Easing.out(Easing.cubic);

export const progress = (motion: Motion, frame: number, at: number, frames = motion.frames) =>
  interpolate(held(motion, frame), [at, at + Math.max(1, frames)], [0, 1], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp", easing: easing(motion),
  });

export const wipe = (p: number, from: "left" | "bottom" = "left") => {
  const hidden = `${(1 - p) * 100}%`;
  return from === "bottom" ? `inset(${hidden} 0 0 0)` : `inset(-20% ${hidden} -20% -5%)`;
};

const TEXT_ENTRANCES = new Set<Entrance>(["type", "scramble"]);

export const settle = (t: number, bounces: number) => {
  const k = Math.min(1, Math.max(0, t));
  return 1 - (1 - k) ** 2 * Math.cos((Math.PI * (2 * bounces + 1) * k) / 2);
};

const units = (text: string, unit: Motion["textUnit"]) =>
  unit === "word" ? text.split(/(\s+)/).filter((part) => part.length > 0) : Array.from(text);

const unitCount = (text: string, unit: Motion["textUnit"]) =>
  units(text, unit).filter((part) => part.trim().length > 0).length;

export const entranceFrames = (motion: Motion, entrance: Entrance, text?: string) => {
  if (entrance === "pop") return 1;
  if (TEXT_ENTRANCES.has(entrance) && text) {
    const perPose = motion.textUnit === "word" ? 1 : motion.charsPerPose;
    return Math.ceil(unitCount(text, motion.textUnit) / perPose) * Math.max(1, motion.holdEvery);
  }
  return motion.frames;
};

const offset = (from: Side, distance: number) => {
  if (from === "left") return `translateX(${-distance}px)`;
  if (from === "right") return `translateX(${distance}px)`;
  if (from === "top") return `translateY(${-distance}px)`;
  return `translateY(${distance}px)`;
};

const mask = (entrance: Entrance, p: number) => {
  if (entrance === "mask-circle") return `circle(${p * 75}% at 50% 50%)`;
  if (entrance === "mask-split") return `inset(-10% ${(1 - p) * 50}% -10% ${(1 - p) * 50}%)`;
  const x = p * 220 - 10;
  return `polygon(-10% -10%, ${x}% -10%, ${x - 100}% 110%, -10% 110%)`;
};

export const roleFrames = (theme: Theme, role: Role, text?: string) =>
  entranceFrames(theme.motion, theme.motion.roles[role], text);

const GLYPHS = "ABCDEFGHJKLMNPQRSTUVWXYZ0123456789#%&";

export const Reveal: React.FC<{
  theme: Theme;
  role: Role;
  f: number;
  at: number;
  fps: number;
  style?: React.CSSProperties;
  children: React.ReactNode;
}> = ({ theme, role, f, at, fps, style, children }) => {
  const { motion } = theme;
  const entrance = motion.roles[role];
  const base: React.CSSProperties = { position: "absolute", ...style };
  if (f < at) return null;
  if (entrance === "pop" || TEXT_ENTRANCES.has(entrance)) return <div style={base}>{children}</div>;

  if (entrance === "wipe" || entrance === "wipe-up") {
    const p = progress(motion, f, at);
    return <div style={{ ...base, clipPath: p < 1 ? wipe(p, entrance === "wipe-up" ? "bottom" : "left") : undefined }}>{children}</div>;
  }

  if (entrance === "spring") {
    const k = spring({
      frame: held(motion, f) - at, fps,
      config: { damping: 10 + (1 - motion.overshoot) * 30, stiffness: 180, mass: 0.6 },
      durationInFrames: motion.frames * 2,
    });
    const opacity = interpolate(held(motion, f) - at, [0, 2], [0, 1], { extrapolateRight: "clamp" });
    return (
      <div style={{ ...base, opacity, transform: `${base.transform ?? ""} scale(${0.82 + 0.18 * k})` }}>{children}</div>
    );
  }

  const t = interpolate(held(motion, f), [at, at + Math.max(1, motion.frames)], [0, 1], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp",
  });
  const moved = motion.bounces > 0 ? settle(t, motion.bounces) : progress(motion, f, at);
  const shown = interpolate(held(motion, f) - at, [0, 3], [0, 1], { extrapolateRight: "clamp" });

  if (entrance === "slide") {
    return (
      <div style={{ ...base, opacity: shown, transform: `${base.transform ?? ""} ${offset(motion.from, (1 - moved) * 140)}` }}>
        {children}
      </div>
    );
  }

  if (entrance === "scale") {
    return (
      <div style={{ ...base, opacity: shown, transform: `${base.transform ?? ""} scale(${0.6 + 0.4 * moved})` }}>{children}</div>
    );
  }

  if (entrance === "blur") {
    const p = progress(motion, f, at);
    return <div style={{ ...base, opacity: p, filter: p < 1 ? `blur(${(1 - p) * 18}px)` : undefined }}>{children}</div>;
  }

  if (entrance === "mask-circle" || entrance === "mask-diagonal" || entrance === "mask-split") {
    const p = progress(motion, f, at);
    return <div style={{ ...base, clipPath: p < 1 ? mask(entrance, p) : undefined }}>{children}</div>;
  }

  const p = progress(motion, f, at);
  const rise = entrance === "rise" ? offset("bottom", (1 - (motion.bounces > 0 ? moved : p)) * 40) : "";
  return <div style={{ ...base, opacity: p, transform: `${base.transform ?? ""} ${rise}` }}>{children}</div>;
};

export const RevealText: React.FC<{
  theme: Theme;
  role: Role;
  f: number;
  at: number;
  text: string;
  mark?: string;
  markAt?: number;
  s: number;
}> = ({ theme, role, f, at, text, mark, markAt, s }) => {
  const { motion } = theme;
  const entrance = motion.roles[role];
  const typing = TEXT_ENTRANCES.has(entrance);
  const pieces = units(text, motion.textUnit);
  const perPose = motion.textUnit === "word" ? 1 : motion.charsPerPose;
  const poses = Math.floor((held(motion, f) - at) / Math.max(1, motion.holdEvery)) + 1;
  const total = pieces.filter((part) => part.trim().length > 0).length;
  const count = typing ? Math.max(0, Math.min(total, poses * perPose)) : total;
  const seed = Math.floor(held(motion, f) / Math.max(1, motion.holdEvery));
  const markStart = mark ? text.indexOf(mark) : -1;
  const markEnd = markStart + (mark?.length ?? 0);
  const markP = markStart >= 0 && markAt !== undefined ? progress(motion, f, markAt) : 0;

  let seen = 0;
  let cursor = 0;
  const spans: { text: string; from: number; visible: boolean; fresh: boolean }[] = pieces.map((piece) => {
    const word = piece.trim().length > 0;
    const index = word ? seen++ : seen - 1;
    const from = cursor;
    cursor += piece.length;
    const settled = index < count;
    const shown = entrance === "scramble" ? f >= at : settled || (!word && index < count);
    const body = entrance === "scramble" && !settled && word
      ? Array.from(piece).map((_, i) => GLYPHS[((from + i) * 7 + seed * 13) % GLYPHS.length]).join("")
      : piece;
    return { text: body, from, visible: shown, fresh: entrance === "type" && word && index === count - 1 && count < total };
  });

  const render = (lo: number, hi: number) => spans
    .filter((span) => span.from >= lo && span.from < hi)
    .map((span) => (
      <span key={span.from} style={{
        opacity: span.visible ? 1 : 0,
        filter: span.fresh ? `blur(${3 * s}px)` : undefined,
      }}>{span.text}</span>
    ));

  if (markStart < 0 || motion.textUnit === "word" && !spans.some((span) => span.from === markStart)) {
    return <>{render(0, text.length)}</>;
  }
  if (theme.mark === "color") {
    return (
      <>
        {render(0, markStart)}
        <span style={{ color: markP > 0 ? theme.color.accent : undefined }}>{render(markStart, markEnd)}</span>
        {render(markEnd, text.length)}
      </>
    );
  }
  return (
    <>
      {render(0, markStart)}
      <span style={{ position: "relative", display: "inline-block", zIndex: 0 }}>
        <span style={{
          position: "absolute", left: -10 * s, right: -14 * s, top: "22%", bottom: "2%",
          backgroundColor: theme.color.highlight, zIndex: -1, clipPath: wipe(markP),
          transform: "rotate(-1deg)",
        }} />
        {render(markStart, markEnd)}
      </span>
      {render(markEnd, text.length)}
    </>
  );
};

export interface Shot {
  at: number;
  zoom: number;
  x: number;
  y: number;
}

export const camera = (
  theme: Theme, f: number, shots: Shot[], punch: Shot | null, length: number, width: number, height: number,
) => {
  const { motion } = theme;
  const h = held(motion, f);
  let zoom = 1;
  let x = width / 2;
  let y = height / 2;
  if (motion.camera === "push") {
    zoom = 1 + 0.045 * Math.min(1, h / Math.max(1, length));
  } else if (motion.camera === "punch" && punch) {
    const k = interpolate(h, [punch.at, punch.at + 1, punch.at + 14], [0, 1, 0.35], {
      extrapolateLeft: "clamp", extrapolateRight: "clamp", easing: Easing.out(Easing.cubic),
    });
    zoom = 1 + (punch.zoom - 1) * k;
    x = width / 2 + (punch.x - width / 2) * k;
    y = height / 2 + (punch.y - height / 2) * k;
  } else if (motion.camera === "track" && shots.length) {
    const next = shots.findIndex((shot) => shot.at > h);
    const last = shots[shots.length - 1];
    if (next === -1) {
      zoom = last.zoom * (1 + 0.02 * Math.min(1, (h - last.at) / 120));
      ({ x, y } = last);
    } else if (next === 0) {
      ({ zoom, x, y } = shots[0]);
    } else {
      const a = shots[next - 1];
      const b = shots[next];
      const k = interpolate(h, [a.at, b.at], [0, 1], { easing: Easing.inOut(Easing.cubic) });
      zoom = a.zoom + (b.zoom - a.zoom) * k;
      x = a.x + (b.x - a.x) * k;
      y = a.y + (b.y - a.y) * k;
    }
  }
  if (zoom === 1) return "none";
  const tx = Math.min(0, Math.max(width - width * zoom, width / 2 - x * zoom));
  const ty = Math.min(0, Math.max(height - height * zoom, height / 2 - y * zoom));
  return `translate(${tx}px, ${ty}px) scale(${zoom})`;
};
