import type React from "react";

export type EdgeKind = "straight" | "torn";
export type Layout = "takeover";
export type Role = "title" | "figure" | "item" | "media" | "label" | "strip" | "caption" | "name";
export type Entrance =
  | "type" | "scramble" | "wipe" | "wipe-up" | "pop" | "rise" | "fade" | "spring"
  | "slide" | "scale" | "blur" | "mask-circle" | "mask-diagonal" | "mask-split"
  | "count" | "slam" | "drop" | "register" | "typed";
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
  "count", "slam", "drop", "register", "typed",
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
  settle: {
    roles: every("rise", { figure: "count", media: "fade" }),
    frames: 14, stagger: 4, holdEvery: 1, charsPerPose: 2, ease: "out", overshoot: 0, bounces: 0,
    from: "bottom", textUnit: "word", camera: "none", exit: "fade",
  },
  pinned: {
    roles: every("drop", { figure: "count", caption: "pop" }),
    frames: 6, stagger: 5, holdEvery: 2, charsPerPose: 2, ease: "out", overshoot: 0, bounces: 0,
    from: "left", textUnit: "word", camera: "push", exit: "fade",
  },
  highlighter: {
    roles: every("rise", { title: "wipe", label: "wipe", strip: "wipe", figure: "count", media: "mask-diagonal" }),
    frames: 12, stagger: 6, holdEvery: 1, charsPerPose: 2, ease: "in-out", overshoot: 0, bounces: 0,
    from: "left", textUnit: "word", camera: "push", exit: "fade",
  },
  grid: {
    roles: every("slide", { figure: "wipe-up", media: "mask-split" }),
    frames: 10, stagger: 3, holdEvery: 1, charsPerPose: 2, ease: "in-out", overshoot: 0, bounces: 0,
    from: "left", textUnit: "word", camera: "none", exit: "fade",
  },
  slam: {
    roles: every("slam", { figure: "count", label: "pop", strip: "pop" }),
    frames: 9, stagger: 6, holdEvery: 1, charsPerPose: 3, ease: "out", overshoot: 0.4, bounces: 0,
    from: "bottom", textUnit: "word", camera: "punch", exit: "fade",
  },
  typed: {
    roles: every("typed", { figure: "scramble", media: "wipe-up" }),
    frames: 8, stagger: 2, holdEvery: 2, charsPerPose: 1, ease: "linear", overshoot: 0, bounces: 0,
    from: "left", textUnit: "char", camera: "none", exit: "fade",
  },
  misprint: {
    roles: every("register"),
    frames: 12, stagger: 5, holdEvery: 2, charsPerPose: 2, ease: "out", overshoot: 0, bounces: 0,
    from: "bottom", textUnit: "word", camera: "none", exit: "fade",
  },
} satisfies Record<string, Motion>;

export type MotionId = keyof typeof MOTIONS;

export type FontId = "grotesk" | "serif" | "condensed" | "mono";

export const FONT_IDS: FontId[] = ["grotesk", "serif", "condensed", "mono"];

export interface ThemeInput {
  pack: PackId;
  motion?: MotionId;
  fonts?: { display?: FontId; label?: FontId };
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
  motion: MOTIONS.pinned,
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
  motion: MOTIONS.highlighter,
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
    label: face("DM Sans", 700, { tracking: 0, lineHeight: 1.1, size: 0.95 }),
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
  motion: MOTIONS.settle,
};

const swiss: Theme = {
  ...clean,
  pack: "swiss",
  color: {
    ground: "#F3F1EC",
    sheet: "#F3F1EC",
    strip: "#F3F1EC",
    stripAlt: "#F3F1EC",
    ink: "#111111",
    muted: "#5E5C58",
    accent: "#D02A1C",
    highlight: "#D02A1C",
    label: "#D02A1C",
    onLabel: "#F3F1EC",
    figure: "#D02A1C",
  },
  type: {
    display: face("DM Sans", 700, { tracking: -3, lineHeight: 1 }),
    label: face("DM Sans", 700, { tracking: -0.5, lineHeight: 1.1 }),
    mono: face("DM Sans", 400, { lineHeight: 1.5 }),
    caption: 60,
  },
  motion: MOTIONS.grid,
};

const poster: Theme = {
  ...clean,
  pack: "poster",
  color: {
    ground: "#1D3FD8",
    sheet: "#1D3FD8",
    strip: "#1D3FD8",
    stripAlt: "#1D3FD8",
    ink: "#FFFFFF",
    muted: "#C4CEF7",
    accent: "#FFD23F",
    highlight: "#FFD23F",
    label: "#FFD23F",
    onLabel: "#1D3FD8",
    figure: "#FFD23F",
  },
  type: {
    display: face("Barlow Condensed", 700, { tracking: -0.5, lineHeight: 0.95 }),
    label: face("Barlow Condensed", 600, { tracking: 0.5, lineHeight: 1.05, size: 1.15 }),
    mono: face("Barlow Condensed", 600, { lineHeight: 1.3 }),
    caption: 68,
  },
  motion: MOTIONS.slam,
};

const terminal: Theme = {
  ...clean,
  pack: "terminal",
  color: {
    ground: "#0A0D0B",
    sheet: "#0A0D0B",
    strip: "#0A0D0B",
    stripAlt: "#0A0D0B",
    ink: "#D9E4DC",
    muted: "#6E8676",
    accent: "#3DDC84",
    highlight: "#3DDC84",
    label: "#3DDC84",
    onLabel: "#0A0D0B",
    figure: "#3DDC84",
  },
  type: {
    display: face("Courier Prime", 400, { tracking: -1.5, lineHeight: 1.08, size: 0.78 }),
    label: face("Courier Prime", 400, { tracking: 0, lineHeight: 1.2 }),
    mono: face("Courier Prime", 400, { lineHeight: 1.45 }),
    caption: 56,
  },
  motion: MOTIONS.typed,
};

const riso: Theme = {
  ...collage,
  pack: "riso",
  color: {
    ground: "#EFE9DD",
    sheet: "#F8F4EC",
    strip: "#FFC6D5",
    stripAlt: "#F8F4EC",
    ink: "#23308F",
    muted: "#5C67A8",
    accent: "#FF4F81",
    highlight: "#FFB3C8",
    label: "#23308F",
    onLabel: "#F8F4EC",
    figure: "#FF7DA1",
  },
  type: {
    display: face("DM Sans", 700, { tracking: -2.5, lineHeight: 1 }),
    label: face("Barlow Condensed", 600, { tracking: 0.5, lineHeight: 1.05 }),
    mono: face("Courier Prime", 400, { lineHeight: 1.45 }),
    caption: 66,
  },
  edge: { kind: "straight", wave: 0, fiber: 0, rim: 0 },
  shadow: 0,
  texture: { strength: 0.45, ground: 0.7 },
  tape: false,
  tilt: 0.8,
  motion: MOTIONS.misprint,
};

export const PACKS = { clean, collage, editorial, swiss, poster, terminal, riso } satisfies Record<string, Theme>;

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

const FONTS: Record<FontId, { display: Face; label: Face }> = {
  grotesk: {
    display: face("DM Sans", 700, { tracking: -2.5, lineHeight: 1.02 }),
    label: face("DM Sans", 700, { lineHeight: 1.1 }),
  },
  serif: {
    display: face("Playfair Display", 900, { italic: true, tracking: -1, lineHeight: 1.02 }),
    label: face("Playfair Display", 700, { italic: true, lineHeight: 1.1 }),
  },
  condensed: {
    display: face("Barlow Condensed", 700, { tracking: -0.5, lineHeight: 0.95 }),
    label: face("Barlow Condensed", 600, { tracking: 0.5, lineHeight: 1.05 }),
  },
  mono: {
    display: face("Courier Prime", 400, { tracking: -1.5, lineHeight: 1.08, size: 0.78 }),
    label: face("Courier Prime", 400, { lineHeight: 1.2 }),
  },
};

const withFonts = (theme: Theme, fonts: ThemeInput["fonts"]): Theme => {
  const display = fonts?.display && FONTS[fonts.display]?.display;
  const label = fonts?.label && FONTS[fonts.label]?.label;
  if (!display && !label) return theme;
  return { ...theme, type: { ...theme.type, ...(display ? { display } : {}), ...(label ? { label } : {}) } };
};

export const resolveTheme = (input?: ThemeInput | null): Theme | null => {
  if (!input) return null;
  const pack: Theme = withFonts(PACKS[input.pack] ?? PACKS.collage, input.fonts);
  const base = input.motion ? { ...pack, motion: MOTIONS[input.motion] ?? pack.motion } : pack;
  const merged = deepMerge(base, input.overrides ?? {});
  return { ...merged, textures: { ...merged.textures, ...(input.textures ?? {}) } };
};

export const fontOf = (f: Face, size: number): React.CSSProperties => ({
  fontFamily: f.family,
  fontWeight: f.weight,
  fontStyle: f.italic ? "italic" : "normal",
  fontSize: size * (f.size ?? 1),
  letterSpacing: (f.tracking ?? 0) * ((size * (f.size ?? 1)) / 60),
  lineHeight: f.lineHeight ?? 1.2,
});
