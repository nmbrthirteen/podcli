import type React from "react";

export type EdgeKind = "straight" | "torn";
export type Layout = "takeover";
export type Role = "title" | "figure" | "item" | "media" | "label" | "strip" | "caption" | "name";
export type Entrance =
  | "type" | "scramble" | "wipe" | "wipe-up" | "pop" | "rise" | "fade" | "spring"
  | "slide" | "scale" | "blur" | "mask-circle" | "mask-diagonal" | "mask-split";
export type Side = "left" | "right" | "top" | "bottom";
export type TextUnit = "char" | "word";
export type Ease = "out" | "in-out" | "linear";
export type CameraMove = "none" | "track" | "push" | "punch";
export type Exit = "cut" | "fade";
export type CaptionChoice = "pack" | "template" | "hormozi" | "karaoke" | "subtle" | "branded" | "outline";

export const CAPTION_CHOICES: CaptionChoice[] = ["pack", "template", "hormozi", "karaoke", "subtle", "branded", "outline"];

export const ROLES: Role[] = ["title", "figure", "item", "media", "label", "strip", "caption", "name"];
export const ENTRANCES: Entrance[] = [
  "type", "scramble", "wipe", "wipe-up", "pop", "rise", "fade", "spring",
  "slide", "scale", "blur", "mask-circle", "mask-diagonal", "mask-split",
];
export const SIDES: Side[] = ["left", "right", "top", "bottom"];

export interface Face {
  family: string;
  weight: number;
  italic?: boolean;
  tracking?: number;
  lineHeight?: number;
  size?: number;
}

export interface Motion {
  roles: Record<Role, Entrance>;
  frames: number;
  stagger: number;
  holdEvery: number;
  charsPerPose: number;
  ease: Ease;
  overshoot: number;
  bounces: number;
  from: Side;
  textUnit: TextUnit;
  camera: CameraMove;
  exit: Exit;
}

export interface Theme {
  pack: string;
  color: {
    ground: string;
    sheet: string;
    strip: string;
    stripAlt: string;
    ink: string;
    muted: string;
    accent: string;
    highlight: string;
    label: string;
    onLabel: string;
    figure: string;
  };
  type: { display: Face; label: Face; mono: Face; caption: number };
  edge: { kind: EdgeKind; wave: number; fiber: number; rim: number };
  surface: "paper" | "none";
  captions: CaptionChoice;
  mark: "fill" | "color";
  shadow: number;
  texture: { strength: number; ground: number };
  tape: boolean;
  media: "halftone" | "photo";
  tilt: number;
  motion: Motion;
  layout: Record<string, Layout>;
  textures: { paper?: string; kraft?: string; tape?: string[]; halftone?: Record<string, string> };
}

export type DeepPartial<T> = {
  [K in keyof T]?: T[K] extends (infer U)[] ? U[] : T[K] extends object ? DeepPartial<T[K]> : T[K];
};

const every = (entrance: Entrance, extra: Partial<Record<Role, Entrance>> = {}): Record<Role, Entrance> => ({
  title: entrance, figure: entrance, item: entrance, media: entrance,
  label: entrance, strip: entrance, caption: "pop", name: entrance, ...extra,
});

export const MOTIONS = {
  "stop-motion": {
    roles: every("pop", { title: "type", item: "wipe", media: "wipe-up", strip: "wipe", name: "wipe" }),
    frames: 8, stagger: 8, holdEvery: 2, charsPerPose: 1, ease: "out", overshoot: 0, bounces: 0,
    from: "left", textUnit: "char", camera: "track", exit: "fade",
  },
  smooth: {
    roles: every("rise", { media: "fade" }),
    frames: 12, stagger: 5, holdEvery: 1, charsPerPose: 2, ease: "out", overshoot: 0, bounces: 0,
    from: "bottom", textUnit: "word", camera: "push", exit: "fade",
  },
  kinetic: {
    roles: every("spring", { label: "pop", strip: "wipe", name: "wipe" }),
    frames: 7, stagger: 4, holdEvery: 1, charsPerPose: 3, ease: "out", overshoot: 0.35, bounces: 1,
    from: "right", textUnit: "word", camera: "punch", exit: "fade",
  },
  calm: {
    roles: every("fade"),
    frames: 15, stagger: 0, holdEvery: 1, charsPerPose: 2, ease: "in-out", overshoot: 0, bounces: 0,
    from: "bottom", textUnit: "word", camera: "none", exit: "fade",
  },
  digital: {
    roles: every("scramble", { media: "pop", label: "pop", strip: "wipe", name: "pop" }),
    frames: 10, stagger: 6, holdEvery: 2, charsPerPose: 2, ease: "linear", overshoot: 0, bounces: 0,
    from: "left", textUnit: "char", camera: "none", exit: "fade",
  },
  snappy: {
    roles: every("pop"),
    frames: 4, stagger: 3, holdEvery: 1, charsPerPose: 4, ease: "out", overshoot: 0, bounces: 0,
    from: "left", textUnit: "word", camera: "punch", exit: "fade",
  },
  elastic: {
    roles: every("spring", { media: "scale", label: "scale" }),
    frames: 9, stagger: 5, holdEvery: 1, charsPerPose: 3, ease: "out", overshoot: 0.6, bounces: 2,
    from: "bottom", textUnit: "word", camera: "push", exit: "fade",
  },
  cinematic: {
    roles: every("blur", { media: "fade", label: "fade" }),
    frames: 18, stagger: 9, holdEvery: 1, charsPerPose: 2, ease: "in-out", overshoot: 0, bounces: 0,
    from: "bottom", textUnit: "word", camera: "push", exit: "fade",
  },
  slide: {
    roles: every("slide", { media: "wipe" }),
    frames: 10, stagger: 5, holdEvery: 1, charsPerPose: 3, ease: "out", overshoot: 0, bounces: 0,
    from: "left", textUnit: "word", camera: "track", exit: "fade",
  },
  reveal: {
    roles: every("mask-circle", { title: "wipe", item: "mask-diagonal", label: "fade", strip: "wipe" }),
    frames: 14, stagger: 6, holdEvery: 1, charsPerPose: 2, ease: "in-out", overshoot: 0, bounces: 0,
    from: "left", textUnit: "word", camera: "none", exit: "fade",
  },
  split: {
    roles: every("mask-split", { label: "fade", strip: "wipe", name: "wipe" }),
    frames: 12, stagger: 6, holdEvery: 1, charsPerPose: 2, ease: "out", overshoot: 0, bounces: 0,
    from: "left", textUnit: "word", camera: "push", exit: "fade",
  },
  typewriter: {
    roles: every("type", { media: "pop", figure: "pop" }),
    frames: 6, stagger: 4, holdEvery: 1, charsPerPose: 1, ease: "linear", overshoot: 0, bounces: 0,
    from: "left", textUnit: "char", camera: "none", exit: "fade",
  },
  zoom: {
    roles: every("scale", { label: "pop", strip: "wipe" }),
    frames: 8, stagger: 4, holdEvery: 1, charsPerPose: 3, ease: "out", overshoot: 0.2, bounces: 1,
    from: "bottom", textUnit: "word", camera: "punch", exit: "fade",
  },
  newsroom: {
    roles: every("wipe", { figure: "wipe-up", media: "wipe-up", label: "pop" }),
    frames: 9, stagger: 5, holdEvery: 1, charsPerPose: 3, ease: "out", overshoot: 0, bounces: 0,
    from: "left", textUnit: "word", camera: "track", exit: "fade",
  },
} satisfies Record<string, Motion>;

export type MotionId = keyof typeof MOTIONS;

export interface ThemeInput {
  pack: PackId;
  motion?: MotionId;
  overrides?: DeepPartial<Theme>;
  textures?: Theme["textures"];
}

const GEORGIAN = "'Noto Sans Georgian'";

const face = (family: string, weight: number, extra: Partial<Face> = {}): Face => ({
  family: `'${family}', ${GEORGIAN}, sans-serif`,
  weight,
  ...extra,
});

const TAKEOVER: Record<string, Layout> = {
  stat: "takeover", headline: "takeover", entity: "takeover", bullets: "takeover", quote: "takeover",
  compare: "takeover", change: "takeover", share: "takeover", image: "takeover", video: "takeover", scene: "takeover",
};

const collage: Theme = {
  pack: "collage",
  color: {
    ground: "#D1CABC",
    sheet: "#F1EBE2",
    strip: "#C8BEA8",
    stripAlt: "#DAD4BF",
    ink: "#2A2723",
    muted: "#6E675D",
    accent: "#D4C159",
    highlight: "#EADA82",
    label: "#2D2925",
    onLabel: "#EFE9E2",
    figure: "#D4C159",
  },
  type: {
    display: face("Playfair Display", 900, { italic: true, tracking: -1, lineHeight: 1.02 }),
    label: face("Barlow Condensed", 600, { tracking: 0.5, lineHeight: 1.05 }),
    mono: face("Courier Prime", 400, { lineHeight: 1.45 }),
    caption: 74,
  },
  edge: { kind: "torn", wave: 5, fiber: 1.6, rim: 9 },
  surface: "paper",
  captions: "pack",
  mark: "fill",
  shadow: 0.07,
  texture: { strength: 0.55, ground: 0.95 },
  tape: true,
  media: "halftone",
  tilt: 1.6,
  motion: MOTIONS["stop-motion"],
  layout: TAKEOVER,
  textures: {},
};

const editorial: Theme = {
  ...collage,
  pack: "editorial",
  color: {
    ...collage.color,
    ground: "#EDE8DF",
    sheet: "#F7F4EE",
    strip: "#E3DDD2",
    stripAlt: "#EDE8DF",
    ink: "#161514",
    accent: "#B3261E",
    highlight: "#F1D3CC",
    label: "#B3261E",
    onLabel: "#F7F4EE",
    figure: "#F7F4EE",
  },
  type: {
    display: face("Playfair Display", 700, { italic: true, tracking: -1, lineHeight: 1.04 }),
    label: face("Barlow Condensed", 600, { tracking: 1, lineHeight: 1.05 }),
    mono: face("Courier Prime", 400, { lineHeight: 1.45 }),
    caption: 72,
  },
  edge: { kind: "straight", wave: 0, fiber: 0, rim: 0 },
  shadow: 0.05,
  texture: { strength: 0.25, ground: 0.4 },
  tape: false,
  tilt: 0,
  motion: MOTIONS.smooth,
};

const clean: Theme = {
  ...collage,
  pack: "clean",
  color: {
    ground: "#08090C",
    sheet: "#08090C",
    strip: "#08090C",
    stripAlt: "#08090C",
    ink: "#F5F6F8",
    muted: "#8B93A1",
    accent: "#4C9DF5",
    highlight: "#4C9DF5",
    label: "#4C9DF5",
    onLabel: "#08090C",
    figure: "#4C9DF5",
  },
  type: {
    display: face("DM Sans", 700, { tracking: -2.5, lineHeight: 1.02 }),
    label: face("DM Sans", 700, { tracking: 0, lineHeight: 1.1, size: 0.8 }),
    mono: face("DM Sans", 400, { lineHeight: 1.5 }),
    caption: 58,
  },
  edge: { kind: "straight", wave: 0, fiber: 0, rim: 0 },
  surface: "none",
  mark: "color",
  shadow: 0,
  texture: { strength: 0, ground: 0 },
  tape: false,
  media: "photo",
  tilt: 0,
  motion: MOTIONS.smooth,
};

export const PACKS = { clean, collage, editorial } satisfies Record<string, Theme>;

export type PackId = keyof typeof PACKS;

const isObject = (v: unknown): v is Record<string, unknown> =>
  typeof v === "object" && v !== null && !Array.isArray(v);

const FORBIDDEN = /gradient|url\(|image-set|filter/i;

const deepMerge = <T>(base: T, patch: unknown): T => {
  if (!isObject(base) || !isObject(patch)) return (patch ?? base) as T;
  const out: Record<string, unknown> = { ...base };
  for (const [key, value] of Object.entries(patch)) {
    if (value === undefined || (typeof value === "string" && FORBIDDEN.test(value))) continue;
    out[key] = isObject(value) && isObject(out[key]) ? deepMerge(out[key], value) : value;
  }
  return out as T;
};

export const resolveTheme = (input?: ThemeInput | null): Theme | null => {
  if (!input) return null;
  const pack: Theme = PACKS[input.pack] ?? PACKS.collage;
  const base = input.motion ? { ...pack, motion: MOTIONS[input.motion] ?? pack.motion } : pack;
  const merged = deepMerge(base, input.overrides ?? {});
  return { ...merged, textures: { ...merged.textures, ...(input.textures ?? {}) } };
};

export const fontOf = (f: Face, size: number): React.CSSProperties => ({
  fontFamily: f.family,
  fontWeight: f.weight,
  fontStyle: f.italic ? "italic" : "normal",
  fontSize: size,
  letterSpacing: (f.tracking ?? 0) * (size / 60),
  lineHeight: f.lineHeight ?? 1.2,
});
