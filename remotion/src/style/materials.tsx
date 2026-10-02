import React from "react";
import { Img } from "remotion";
import { fontOf } from "./theme";
import type { Theme } from "./theme";

const random = (seed: number) => {
  let t = (seed * 2654435761) >>> 0;
  return () => {
    t = (t + 0x6d2b79f5) >>> 0;
    let r = Math.imul(t ^ (t >>> 15), 1 | t);
    r = (r + Math.imul(r ^ (r >>> 7), 61 | r)) ^ r;
    return ((r ^ (r >>> 14)) >>> 0) / 4294967296;
  };
};

export interface Sides { top?: boolean; right?: boolean; bottom?: boolean; left?: boolean }

export const fibrous = (seed: number, wave: number, fiber: number, sides: Sides = {}, steps = 220) => {
  const r = random(seed);
  const { top = true, right = true, bottom = true, left = true } = sides;
  const points: string[] = [];
  const edge = (on: boolean, point: (t: number, d: number) => string) => {
    if (!on) {
      points.push(point(0, 0));
      return;
    }
    const p1 = r() * 6.28;
    const p2 = r() * 6.28;
    const f1 = 2 + r() * 2.5;
    const f2 = 9 + r() * 6;
    for (let i = 0; i < steps; i++) {
      const t = (i / steps) * 100;
      const u = i / steps;
      const smooth = (Math.sin(u * f1 * 6.28 + p1) * 0.6 + Math.sin(u * f2 * 6.28 + p2) * 0.4 + 1) / 2;
      const spike = r() < 0.05 ? fiber * 2.2 : 0;
      points.push(point(t, 1 + wave * smooth + fiber * r() + spike));
    }
  };
  edge(top, (t, d) => `${t.toFixed(2)}% ${d.toFixed(1)}px`);
  edge(right, (t, d) => `calc(100% - ${d.toFixed(1)}px) ${t.toFixed(2)}%`);
  edge(bottom, (t, d) => `${(100 - t).toFixed(2)}% calc(100% - ${d.toFixed(1)}px)`);
  edge(left, (t, d) => `${d.toFixed(1)}px ${(100 - t).toFixed(2)}%`);
  return `polygon(${points.join(",")})`;
};

const lighter = (hex: string, k: number) =>
  `color-mix(in oklab, ${hex}, #FFFFFF ${Math.round(k * 100)}%)`;

export const Paper: React.FC<{
  theme: Theme;
  seed: number;
  color: string;
  scale: number;
  texture?: "paper" | "kraft" | "none";
  strength?: number;
  sides?: Sides;
  rimColor?: string;
  shadow?: boolean;
  style?: React.CSSProperties;
  children?: React.ReactNode;
}> = ({
  theme, seed, color, scale: s, texture = "paper", strength, sides, rimColor, shadow = true, style, children,
}) => {
  if (theme.surface === "none") return <div style={{ position: "relative", ...style }}>{children}</div>;
  const torn = theme.edge.kind === "torn";
  const rim = theme.edge.rim * s;
  const outer = torn ? fibrous(seed, theme.edge.wave * s, theme.edge.fiber * 1.6 * s, sides) : undefined;
  const inner = torn ? fibrous(seed + 5, theme.edge.wave * 0.6 * s, theme.edge.fiber * s, sides) : undefined;
  const amount = strength ?? theme.texture.strength;
  const src = texture === "none" || amount <= 0 ? undefined : theme.textures[texture];
  const inset = (on?: boolean) => (on === false ? 0 : rim * 0.55);
  const layer: React.CSSProperties = { position: "absolute", inset: 0 };

  return (
    <div style={{ position: "relative", ...style }}>
      {shadow && theme.shadow > 0 && [1.5, 3.5, 6.5].map((dy, i) => (
        <div key={i} style={{
          ...layer, transform: `translate(${dy * 0.3 * s}px, ${dy * s}px)`,
          backgroundColor: `rgba(40, 30, 15, ${theme.shadow})`, clipPath: outer,
        }} />
      ))}
      {torn && (
        <div style={{ ...layer, backgroundColor: rimColor ?? lighter(color, 0.3), clipPath: outer }} />
      )}
      <div
        style={{
          position: "absolute",
          top: torn ? inset(sides?.top) : 0,
          right: torn ? inset(sides?.right) : 0,
          bottom: torn ? inset(sides?.bottom) : 0,
          left: torn ? inset(sides?.left) : 0,
          backgroundColor: color,
          clipPath: inner,
          overflow: "hidden",
          isolation: "isolate",
        }}
      >
        {src && (
          <div style={{
            ...layer, backgroundImage: `url(${src})`, backgroundSize: `${900 * s}px`,
            backgroundPosition: `${(seed * 137) % 900}px ${(seed * 311) % 900}px`,
            opacity: amount,
          }} />
        )}
      </div>
      <div style={{ position: "relative" }}>{children}</div>
    </div>
  );
};

export const Label: React.FC<{
  theme: Theme;
  seed: number;
  scale: number;
  size: number;
  color?: string;
  ink?: string;
  pad?: number;
  style?: React.CSSProperties;
  children: React.ReactNode;
}> = ({ theme, seed, scale: s, size: base, color, ink, pad = 0.28, style, children }) => {
  const size = base * (theme.type.label.size ?? 1);
  if (theme.surface === "none") {
    return (
      <div style={{ display: "inline-block", ...fontOf({ ...theme.type.label, size: 1 }, size), fontWeight: 700, lineHeight: 1,
        color: ink ?? theme.color.accent, ...style }}>
        {children}
      </div>
    );
  }
  return (
    <Paper
      theme={theme}
      seed={seed}
      color={color ?? theme.color.label}
      rimColor={theme.edge.kind === "torn" ? "#E6DFD2" : undefined}
      scale={s}
      strength={0.35}
      style={{ display: "inline-block", ...style }}
    >
      <div style={{
        ...fontOf({ ...theme.type.label, size: 1 }, size), fontWeight: 700, lineHeight: 1,
        color: ink ?? theme.color.onLabel,
        padding: `${size * pad}px ${size * 0.38}px ${size * (pad - 0.06)}px`,
      }}>
        {children}
      </div>
    </Paper>
  );
};

export const Tape: React.FC<{
  theme: Theme;
  seed: number;
  scale: number;
  width: number;
  style?: React.CSSProperties;
}> = ({ theme, seed, scale: s, width, style }) => {
  const tapes = theme.textures.tape ?? [];
  if (!theme.tape || !tapes.length) return null;
  return (
    <Img
      src={tapes[seed % tapes.length]}
      style={{ position: "absolute", width: width * s, height: width * s * 0.256, ...style }}
    />
  );
};
