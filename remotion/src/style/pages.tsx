import React from "react";
import {
  AbsoluteFill, getRemotionEnvironment, Img, interpolate, OffthreadVideo, staticFile, useCurrentFrame,
  useVideoConfig, Video,
} from "remotion";
import type { Card } from "../cards";
import { fontOf } from "./theme";
import type { Role, Theme } from "./theme";
import { Label, Paper, Tape } from "./materials";
import { camera, progress, Reveal, RevealText, roleFrames, TEXT_ENTRANCES } from "./reveal";
import type { Shot } from "./reveal";
import { Scene } from "../components/Scene";
import { hasMedia, SCENE_WIDTH, sceneHeight } from "../scene";

type PageProps<K extends Card["kind"]> = {
  card: Extract<Card, { kind: K }>;
  theme: Theme;
  s: number;
  f: number;
  fps: number;
  inset: number;
  k: number;
};

const planner = (theme: Theme, start = 2) => {
  let cursor = start;
  return (role: Role, text?: string) => {
    const at = cursor;
    const spoken = TEXT_ENTRANCES.has(theme.motion.roles[role]);
    cursor = at + (spoken ? roleFrames(theme, role, text) : 0) + Math.max(2, theme.motion.stagger);
    return at;
  };
};

const CONTENT_TOP = 190;
const EXIT_FRAMES = 8;
const CONTENT_BOTTOM = 1250;

const Page: React.FC<{
  theme: Theme;
  s: number;
  f: number;
  length: number;
  shots: Shot[];
  punch: Shot | null;
  inset: number;
  k: number;
  children: React.ReactNode;
}> = ({ theme, s, f, length, shots, punch, inset, k, children }) => {
  const { width, height } = useVideoConfig();
  const shift = Math.max(0, inset - CONTENT_TOP + 20);
  const squeeze = (CONTENT_BOTTOM - CONTENT_TOP - shift) / (CONTENT_BOTTOM - CONTENT_TOP);
  const lens = (shot: Shot) => ({ ...shot, x: shot.x * s, y: shot.y * s });
  const fade = theme.motion.exit === "fade"
    ? interpolate(f, [length - EXIT_FRAMES * k, length], [1, 0], { extrapolateLeft: "clamp", extrapolateRight: "clamp" })
    : 1;
  return (
    <AbsoluteFill style={{ overflow: "hidden", opacity: fade, backgroundColor: theme.color.ground }}>
      <div style={{
        position: "absolute", inset: 0, transformOrigin: "0 0",
        transform: camera(theme, f, shots.map(lens), punch && lens(punch), length, width, height),
      }}>
        {theme.textures.paper && theme.texture.ground > 0 && (
          <div style={{
            position: "absolute", inset: 0, backgroundImage: `url(${theme.textures.paper})`,
            backgroundSize: `${1100 * s}px`, opacity: theme.texture.ground,
          }} />
        )}
        <div style={{
          position: "absolute", inset: 0, transformOrigin: `50% ${CONTENT_TOP * s}px`,
          transform: shift > 0 ? `translateY(${shift * s}px) scale(${squeeze})` : undefined,
        }}>
          {children}
        </div>
      </div>
    </AbsoluteFill>
  );
};

const Circle: React.FC<{ theme: Theme; f: number; at: number; s: number; inset: number }> = ({
  theme, f, at, s, inset,
}) => {
  const p = progress(theme.motion, f, at, 10);
  if (p <= 0) return null;
  const ring = (dx: string, dy: string, rot: number) => (
    <ellipse cx={dx} cy={dy} rx="55%" ry="70%" transform={`rotate(${rot})`} pathLength={1}
      style={{ transformOrigin: "center", transformBox: "fill-box" }}
      fill="none" stroke={theme.color.accent} strokeWidth={6 * s} strokeLinecap="round"
      strokeDasharray="1" strokeDashoffset={1 - p} />
  );
  return (
    <svg style={{ position: "absolute", inset: -inset * s, width: `calc(100% + ${2 * inset * s}px)`,
      height: `calc(100% + ${2 * inset * s}px)`, overflow: "visible" }}>
      {ring("50%", "50%", -4)}
      {theme.surface === "paper" && ring("50.5%", "49%", 3)}
    </svg>
  );
};

const figureSize = (value: string) => {
  const length = Array.from(value).length;
  return length > 9 ? 150 : length > 6 ? 200 : 280;
};

const tilt = (theme: Theme, k: number) => ({ transform: `rotate(${theme.tilt * k}deg)` });

const titleSize = (text: string, base: number) => {
  const length = Array.from(text).length;
  if (length > 22) return base * 0.66;
  if (length > 14) return base * 0.8;
  return base;
};

const Bar: React.FC<{ theme: Theme; seed: number; s: number; color: string; height: number; children?: React.ReactNode }> = ({
  theme, seed, s, color, height, children,
}) => (theme.surface === "none"
  ? <div style={{ height: height * s, backgroundColor: color, position: "relative" }}>{children}</div>
  : (
    <Paper theme={theme} seed={seed} scale={s} color={color}>
      <div style={{ height: height * s, position: "relative" }}>{children}</div>
    </Paper>
  ));

const Strip: React.FC<{ theme: Theme; seed: number; s: number; color?: string; size?: number; children: React.ReactNode }> = ({
  theme, seed, s, color, size = 48, children,
}) => (
  <Paper theme={theme} seed={seed} color={color ?? theme.color.stripAlt} scale={s}>
    <div style={{ ...fontOf(theme.type.label, size * s * (theme.surface === "none" ? 1.35 : 1)), color: theme.color.ink, padding: `${16 * s}px ${34 * s}px` }}>
      {children}
    </div>
  </Paper>
);

const StatPage: React.FC<PageProps<"stat"> & { length: number }> = ({ card, theme, s, f, fps, length, inset, k }) => {
  const title = card.eyebrow;
  const next = planner(theme);
  const titleAt = title ? next("title", title) : 0;
  const stripAt = next("strip");
  const sheetAt = next("media");
  const figureAt = next("figure", card.value) + 4;
  const circleAt = figureAt + 8;
  return (
    <Page theme={theme} s={s} f={f} length={length} inset={inset} k={k}
      punch={{ at: figureAt, zoom: 1.12, x: 540, y: 820 }}
      shots={[
        { at: 0, zoom: 1.35, x: 360, y: 300 }, { at: stripAt, zoom: 1.35, x: 360, y: 300 },
        { at: figureAt, zoom: 1.2, x: 540, y: 800 }, { at: circleAt + 10, zoom: 1.2, x: 540, y: 800 },
        { at: circleAt + 22, zoom: 1, x: 540, y: 960 },
      ]}>
      {theme.surface === "paper" && (
        <Reveal theme={theme} role="media" f={f} at={sheetAt} fps={fps}
          style={{ left: -40 * s, top: 500 * s, width: 1000 * s, height: 800 * s, ...tilt(theme, -1.2) }}>
          <Paper theme={theme} seed={11} color={theme.color.sheet} scale={s} style={{ width: "100%", height: "100%" }} />
          <Tape theme={theme} seed={1} scale={s} width={260} style={{ left: 760 * s, top: -24 * s, transform: "rotate(32deg)" }} />
        </Reveal>
      )}
      <div style={{ position: "absolute", left: 50 * s, top: 190 * s, width: 980 * s, display: "flex", flexDirection: "column", alignItems: "flex-start", gap: 14 * s }}>
        <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-start", gap: 14 * s, minHeight: 360 * s }}>
          {title && (
            <Reveal theme={theme} role="title" f={f} at={titleAt} fps={fps} style={{ position: "relative", ...tilt(theme, -1) }}>
              <Paper theme={theme} seed={12} color={theme.color.strip} scale={s}>
                <div style={{ ...fontOf(theme.type.display, titleSize(title, 124) * s), color: theme.color.ink, padding: `${22 * s}px ${48 * s}px ${34 * s}px` }}>
                  <RevealText theme={theme} role="title" f={f} at={titleAt} text={title} s={s} />
                </div>
              </Paper>
            </Reveal>
          )}
          {card.caption && (
            <Reveal theme={theme} role="strip" f={f} at={stripAt} fps={fps} style={{ position: "relative", marginLeft: 60 * s, ...tilt(theme, 0.6) }}>
              <Strip theme={theme} seed={13} s={s}>{card.caption}</Strip>
            </Reveal>
          )}
        </div>
        <Reveal theme={theme} role="figure" f={f} at={figureAt} fps={fps}
          style={{ position: "relative", alignSelf: "center", marginTop: 60 * s, ...tilt(theme, 1.8) }}>
          <div style={{ position: "relative" }}>
            <Label theme={theme} seed={14} scale={s} size={figureSize(card.value) * s} ink={theme.color.figure} pad={0.16}>
              <RevealText theme={theme} role="figure" f={f} at={figureAt} text={card.value} s={s} />
            </Label>
            <Circle theme={theme} f={f} at={circleAt} s={s} inset={22} />
          </div>
        </Reveal>
      </div>
    </Page>
  );
};

const HeadlinePage: React.FC<PageProps<"headline"> & { length: number }> = ({ card, theme, s, f, fps, length, inset, k }) => {
  const next = planner(theme);
  const eyebrowAt = next("label");
  const leadAt = next("title", card.lead);
  const markAt = leadAt + roleFrames(theme, "title", card.lead) + 2;
  const subAt = next("strip") + 6;
  const mark = Array.isArray(card.emphasis) ? card.emphasis[0] : card.emphasis;
  return (
    <Page theme={theme} s={s} f={f} length={length} inset={inset} k={k}
      punch={{ at: markAt, zoom: 1.1, x: 540, y: 640 }}
      shots={[
        { at: 0, zoom: 1.18, x: 540, y: 560 }, { at: markAt, zoom: 1.18, x: 540, y: 560 }, { at: markAt + 16, zoom: 1, x: 540, y: 960 },
      ]}>
      {theme.surface === "paper" && (
        <Paper theme={theme} seed={21} color={theme.color.sheet} scale={s} strength={0.4}
          style={{ position: "absolute", left: 160 * s, top: 640 * s, width: 1000 * s, height: 640 * s, ...tilt(theme, 2) }} />
      )}
      {card.eyebrow && (
        <Reveal theme={theme} role="label" f={f} at={eyebrowAt} fps={fps} style={{ left: 80 * s, top: 250 * s, ...tilt(theme, -2) }}>
          <Label theme={theme} seed={22} scale={s} size={(theme.surface === "none" ? 64 : 50) * s}>{card.eyebrow}</Label>
        </Reveal>
      )}
      <div style={{ position: "absolute", left: 40 * s, top: 340 * s, width: 990 * s, display: "flex", flexDirection: "column", alignItems: "flex-start", gap: 48 * s }}>
        <Reveal theme={theme} role="title" f={f} at={leadAt} fps={fps} style={{ position: "relative", width: "100%", ...tilt(theme, -0.8) }}>
          <Paper theme={theme} seed={23} color={theme.color.sheet} scale={s}>
            <div style={{ ...fontOf(theme.type.display, 128 * s), color: theme.color.ink, padding: `${50 * s}px ${60 * s}px ${64 * s}px` }}>
              <RevealText theme={theme} role="title" f={f} at={leadAt} text={card.lead} mark={mark} markAt={markAt} s={s} />
            </div>
          </Paper>
        </Reveal>
        {card.sub && (
          <Reveal theme={theme} role="strip" f={f} at={subAt} fps={fps} style={{ position: "relative", marginLeft: 80 * s, maxWidth: 860 * s, ...tilt(theme, 1) }}>
            <Strip theme={theme} seed={24} s={s} size={50}>{card.sub}</Strip>
          </Reveal>
        )}
      </div>
    </Page>
  );
};

const EntityPage: React.FC<PageProps<"entity"> & { length: number }> = ({ card, theme, s, f, fps, length, inset, k }) => {
  const next = planner(theme, 0);
  const mediaAt = next("media");
  const nameAt = next("figure", card.name) + 10;
  const noteAt = next("strip") + 10;
  const src = card.src && (theme.media === "halftone" ? theme.textures.halftone?.[card.src] ?? card.src : card.src);
  return (
    <Page theme={theme} s={s} f={f} length={length} inset={inset} k={k}
      punch={{ at: nameAt, zoom: 1.1, x: 300, y: 1090 }}
      shots={[{ at: 0, zoom: 1.4, x: 560, y: 420 }, { at: 12, zoom: 1.4, x: 560, y: 420 }, { at: 34, zoom: 1, x: 540, y: 960 }]}>
      {src && <Reveal theme={theme} role="media" f={f} at={mediaAt} fps={fps} style={{ left: 120 * s, top: 170 * s, width: 840 * s, ...tilt(theme, 1.3) }}>
        <Paper theme={theme} seed={31} color="#E8E4D8" scale={s} strength={0.3}>
          <div style={{ padding: theme.surface === "paper" ? 22 * s : 0 }}>
            {src && <Img src={src} style={{ width: 796 * s, height: (theme.surface === "none" ? 780 : 1035) * s, display: "block", objectFit: "cover" }} />}
          </div>
        </Paper>
        <Tape theme={theme} seed={3} scale={s} width={250} style={{ left: -60 * s, top: 0, transform: "rotate(-28deg)" }} />
        <Tape theme={theme} seed={1} scale={s} width={250} style={{ left: 680 * s, top: 20 * s, transform: "rotate(24deg)" }} />
      </Reveal>}
      <div style={{ position: "absolute", left: 50 * s, right: 50 * s, top: (src ? (theme.surface === "none" ? 990 : 1030) : 560) * s, display: "flex", flexDirection: "column", alignItems: "flex-start", gap: 36 * s }}>
        <Reveal theme={theme} role="figure" f={f} at={nameAt} fps={fps} style={{ position: "relative", ...tilt(theme, -2) }}>
          <Label theme={theme} seed={32} scale={s} size={(src ? 150 : 190) * s} ink={theme.color.figure} pad={0.18}>
            <RevealText theme={theme} role="figure" f={f} at={nameAt} text={card.name} s={s} />
          </Label>
        </Reveal>
        {card.note && (
          <Reveal theme={theme} role="strip" f={f} at={noteAt} fps={fps} style={{ position: "relative", marginLeft: 60 * s, maxWidth: 860 * s, ...tilt(theme, 0.8) }}>
            <Strip theme={theme} seed={33} s={s}>{card.note}</Strip>
          </Reveal>
        )}
      </div>
    </Page>
  );
};

const BulletsPage: React.FC<PageProps<"bullets"> & { length: number }> = ({ card, theme, s, f, fps, length, inset, k }) => {
  const title = card.eyebrow;
  const next = planner(theme);
  const titleAt = title ? next("title", title) : 0;
  const items = card.items.slice(0, 3).map(() => next("item"));
  const lift = title ? 0 : 220;
  const stocks = [theme.color.sheet, theme.color.stripAlt, theme.color.strip];
  const lefts = theme.surface === "paper" ? [50, 110, 70] : [50, 50, 50];
  return (
    <Page theme={theme} s={s} f={f} length={length} inset={inset} k={k}
      punch={{ at: items[0] ?? 0, zoom: 1.08, x: 480, y: 600 }}
      shots={[{ at: 0, zoom: 1.3, x: 380, y: 290 }, { at: items[0] ?? 0, zoom: 1.3, x: 380, y: 290 }, { at: (items[0] ?? 0) + 12, zoom: 1, x: 540, y: 960 }]}>
      {theme.surface === "paper" && (
        <Paper theme={theme} seed={40} color={theme.color.stripAlt} scale={s} texture="kraft"
          style={{ position: "absolute", left: 300 * s, top: 420 * s, width: 900 * s, height: 860 * s, ...tilt(theme, 3) }} />
      )}
      {title && (
        <Reveal theme={theme} role="title" f={f} at={titleAt} fps={fps} style={{ left: 50 * s, top: 200 * s, ...tilt(theme, -1) }}>
          <Paper theme={theme} seed={41} color={theme.color.strip} scale={s}>
            <div style={{ ...fontOf(theme.type.display, titleSize(title, 120) * s), color: theme.color.ink, padding: `${22 * s}px ${48 * s}px ${34 * s}px` }}>
              <RevealText theme={theme} role="title" f={f} at={titleAt} text={title} s={s} />
            </div>
          </Paper>
        </Reveal>
      )}
      {card.items.slice(0, 3).map((item, i) => (
        <Reveal key={item} theme={theme} role="item" f={f} at={items[i]} fps={fps}
          style={{ left: lefts[i] * s, top: (500 - lift + i * 220) * s, width: 900 * s, ...tilt(theme, i % 2 ? 0.9 : -0.8) }}>
          <Paper theme={theme} seed={42 + i} color={stocks[i]} scale={s} texture={i === 1 ? "kraft" : "paper"}>
            <div style={{ ...fontOf(theme.type.display, 84 * s), color: theme.color.ink, padding: `${36 * s}px ${50 * s}px ${44 * s}px ${170 * s}px` }}>
              <RevealText theme={theme} role="item" f={f} at={items[i]} text={item} s={s} />
            </div>
          </Paper>
          <div style={{ position: "absolute", left: 30 * s, top: 22 * s, ...tilt(theme, -2) }}>
            <Label theme={theme} seed={50 + i} scale={s} size={96 * s} ink={theme.color.figure} pad={0.14}>{i + 1}</Label>
          </div>
        </Reveal>
      ))}
    </Page>
  );
};

const QuotePage: React.FC<PageProps<"quote"> & { length: number }> = ({ card, theme, s, f, fps, length, inset, k }) => {
  const next = planner(theme);
  const textAt = next("title", card.text);
  const byAt = next("label") + 4;
  return (
    <Page theme={theme} s={s} f={f} length={length} inset={inset} k={k}
      punch={{ at: byAt, zoom: 1.06, x: 540, y: 700 }}
      shots={[{ at: 0, zoom: 1.15, x: 520, y: 620 }, { at: byAt - 4, zoom: 1.15, x: 520, y: 620 }, { at: byAt + 10, zoom: 1, x: 540, y: 960 }]}>
      {theme.surface === "paper" && (
        <Paper theme={theme} seed={60} color={theme.color.strip} scale={s} texture="kraft"
          style={{ position: "absolute", left: -80 * s, top: 700 * s, width: 820 * s, height: 600 * s, ...tilt(theme, -3) }} />
      )}
      <div style={{ position: "absolute", left: 60 * s, top: 300 * s, width: 960 * s, display: "flex", flexDirection: "column", alignItems: "flex-start", gap: 40 * s }}>
      <Reveal theme={theme} role="title" f={f} at={textAt} fps={fps} style={{ position: "relative", width: "100%", ...tilt(theme, 1) }}>
        <Paper theme={theme} seed={61} color={theme.color.sheet} scale={s}>
          <div style={{ padding: `${40 * s}px ${70 * s}px ${90 * s}px` }}>
            <div style={{ ...fontOf(theme.type.display, 300 * s), color: theme.color.accent, height: 170 * s, lineHeight: 1 }}>“</div>
            <div style={{ ...fontOf({ ...theme.type.display, weight: 700 }, 92 * s), color: theme.color.ink }}>
              <RevealText theme={theme} role="title" f={f} at={textAt} text={card.text} s={s} />
            </div>
          </div>
        </Paper>
        <Tape theme={theme} seed={2} scale={s} width={280} style={{ left: 340 * s, top: -30 * s, transform: "rotate(-4deg)" }} />
      </Reveal>
      {card.attribution && (
        <Reveal theme={theme} role="label" f={f} at={byAt} fps={fps} style={{ position: "relative", marginLeft: 60 * s, ...tilt(theme, -2) }}>
          <Label theme={theme} seed={62} scale={s} size={64 * s}>{card.attribution}</Label>
        </Reveal>
      )}
      </div>
    </Page>
  );
};

const Title: React.FC<{ theme: Theme; s: number; f: number; fps: number; at: number; text: string; seed: number }> = ({
  theme, s, f, fps, at, text, seed,
}) => (
  <Reveal theme={theme} role="title" f={f} at={at} fps={fps} style={{ left: 50 * s, top: 200 * s, ...tilt(theme, -1) }}>
    <Paper theme={theme} seed={seed} color={theme.color.strip} scale={s}>
      <div style={{ ...fontOf(theme.type.display, titleSize(text, 112) * s), color: theme.color.ink, padding: `${22 * s}px ${48 * s}px ${34 * s}px` }}>
        <RevealText theme={theme} role="title" f={f} at={at} text={text} s={s} />
      </div>
    </Paper>
  </Reveal>
);

const CompareRows: React.FC<PageProps<"compare"> & { length: number }> = ({ card, theme, s, f, fps, length, inset, k }) => {
  const title = card.eyebrow;
  const next = planner(theme);
  const titleAt = title ? next("title", title) : 0;
  const rows = card.rows.slice(0, 5);
  const ats = rows.map(() => next("item"));
  const top = Math.max(...rows.map((r) => r.value), 1);
  return (
    <Page theme={theme} s={s} f={f} length={length} inset={inset} k={k} punch={{ at: ats[0] ?? 0, zoom: 1.06, x: 540, y: 700 }}
      shots={[{ at: 0, zoom: 1.3, x: 380, y: 290 }, { at: ats[0] ?? 0, zoom: 1.3, x: 380, y: 290 }, { at: (ats[0] ?? 0) + 12, zoom: 1, x: 540, y: 960 }]}>
      {title && <Title theme={theme} s={s} f={f} fps={fps} at={titleAt} text={title} seed={81} />}
      {rows.map((row, i) => (
        <Reveal key={`${row.label}-${i}`} theme={theme} role="item" f={f} at={ats[i]} fps={fps}
          style={{ left: 70 * s, top: (500 + i * 210) * s, width: 940 * s }}>
          <div style={{ ...fontOf(theme.type.label, 60 * s), color: theme.color.ink, marginBottom: 14 * s }}>{row.label}</div>
          <div style={{ display: "flex", alignItems: "center", gap: 18 * s }}>
            <div style={{ width: `${Math.max(10, (row.value / top) * 70)}%` }}>
              <Bar theme={theme} seed={82 + i} s={s} height={76}
                color={row.subject ? theme.color.accent : theme.color.muted} />
            </div>
            <div style={{ ...fontOf(theme.type.label, 76 * s), fontWeight: 700,
              color: row.subject && theme.surface === "none" ? theme.color.accent : theme.color.ink }}>
              {row.display ?? String(row.value)}
            </div>
          </div>
        </Reveal>
      ))}
    </Page>
  );
};

const ChangePage: React.FC<PageProps<"change"> & { length: number }> = ({ card, theme, s, f, fps, length, inset, k }) => {
  const title = card.eyebrow ?? card.label;
  const next = planner(theme);
  const titleAt = title ? next("title", title) : 0;
  const fromAt = next("item");
  const arrowAt = fromAt + 6;
  const toAt = next("figure", card.to.value) + 8;
  const p = progress(theme.motion, f, arrowAt, 10);
  const end = (value: string, note: string | undefined, at: number, role: "item" | "figure", top: number, seed: number, strong: boolean) => (
    <Reveal theme={theme} role={role} f={f} at={at} fps={fps} style={{ left: 0, right: 0, top: top * s, display: "flex", flexDirection: "column", alignItems: "center", gap: 14 * s }}>
      <Label theme={theme} seed={seed} scale={s} size={(strong ? 200 : 120) * s} pad={0.16}
        ink={strong ? theme.color.figure : undefined}>
        <RevealText theme={theme} role={role} f={f} at={at} text={value} s={s} />
      </Label>
      {note && <div style={{ ...fontOf(theme.type.label, 54 * s), color: theme.color.muted }}>{note}</div>}
    </Reveal>
  );
  return (
    <Page theme={theme} s={s} f={f} length={length} inset={inset} k={k} punch={{ at: toAt, zoom: 1.1, x: 540, y: 1000 }}
      shots={[{ at: 0, zoom: 1.25, x: 540, y: 560 }, { at: arrowAt, zoom: 1.25, x: 540, y: 560 }, { at: toAt + 8, zoom: 1, x: 540, y: 960 }]}>
      {title && <Title theme={theme} s={s} f={f} fps={fps} at={titleAt} text={title} seed={91} />}
      {end(card.from.value, card.from.note, fromAt, "item", 440, 92, false)}
      {p > 0 && (
        <svg style={{ position: "absolute", left: 470 * s, top: 690 * s, width: 140 * s, height: 190 * s, overflow: "visible" }}>
          <path d={`M ${70 * s} ${10 * s} L ${70 * s} ${170 * s} M ${30 * s} ${130 * s} L ${70 * s} ${172 * s} L ${110 * s} ${130 * s}`}
            fill="none" stroke={theme.color.accent} strokeWidth={9 * s} strokeLinecap="round" strokeLinejoin="round"
            pathLength={1} strokeDasharray="1" strokeDashoffset={1 - p} />
        </svg>
      )}
      {end(card.to.value, card.to.note, toAt, "figure", 920, 93, true)}
    </Page>
  );
};

const SharePage: React.FC<PageProps<"share"> & { length: number }> = ({ card, theme, s, f, fps, length, inset, k }) => {
  const share = Math.max(0, Math.min(1, card.value));
  const display = card.display ?? `${Math.round(share * 100)}%`;
  const title = card.eyebrow;
  const next = planner(theme);
  const titleAt = title ? next("title", title) : 0;
  const figureAt = next("figure", display) + 4;
  const barAt = next("item");
  const fill = progress(theme.motion, f, barAt, Math.max(12, theme.motion.frames * 2));
  return (
    <Page theme={theme} s={s} f={f} length={length} inset={inset} k={k} punch={{ at: figureAt, zoom: 1.1, x: 540, y: 700 }}
      shots={[{ at: 0, zoom: 1.3, x: 380, y: 290 }, { at: figureAt, zoom: 1.3, x: 380, y: 290 }, { at: figureAt + 12, zoom: 1, x: 540, y: 960 }]}>
      {title && <Title theme={theme} s={s} f={f} fps={fps} at={titleAt} text={title} seed={101} />}
      <Reveal theme={theme} role="figure" f={f} at={figureAt} fps={fps}
        style={{ left: 0, right: 0, top: 520 * s, display: "flex", justifyContent: "center", ...tilt(theme, 1.4) }}>
        <Label theme={theme} seed={102} scale={s} size={260 * s} ink={theme.color.figure} pad={0.16}>
          <RevealText theme={theme} role="figure" f={f} at={figureAt} text={display} s={s} />
        </Label>
      </Reveal>
      <Reveal theme={theme} role="item" f={f} at={barAt} fps={fps} style={{ left: 90 * s, top: 960 * s, width: 900 * s }}>
        <Bar theme={theme} seed={103} s={s} height={70}
          color={theme.surface === "none" ? `color-mix(in oklab, ${theme.color.muted} 45%, transparent)` : theme.color.stripAlt}>
          <div style={{ position: "absolute", left: 0, top: 0, bottom: 0, width: `${share * fill * 100}%`, backgroundColor: theme.color.accent }} />
        </Bar>
        {card.caption && (
          <div style={{ ...fontOf(theme.type.label, 56 * s), color: theme.color.ink, marginTop: 24 * s }}>{card.caption}</div>
        )}
      </Reveal>
    </Page>
  );
};

const MediaPage: React.FC<PageProps<"image" | "video"> & { length: number }> = ({ card, theme, s, f, fps, length, inset, k }) => {
  const src = card.src.startsWith("http") || card.src.startsWith("/") ? card.src : staticFile(card.src);
  const shown = card.kind === "image" && theme.media === "halftone" ? theme.textures.halftone?.[card.src] ?? src : src;
  const Frame = getRemotionEnvironment().isRendering ? OffthreadVideo : Video;
  const next = planner(theme, 0);
  const mediaAt = next("media");
  const captionAt = next("strip") + 6;
  const bleed = theme.surface === "none";
  const fit: React.CSSProperties = { width: "100%", height: "100%", display: "block", objectFit: card.fit === "fit" ? "contain" : "cover" };
  const body = card.kind === "video"
    ? <Frame src={src} muted startFrom={Math.max(0, Math.round((card.startAt ?? 0) * fps))} style={fit} />
    : <Img src={shown} style={fit} />;
  return (
    <Page theme={theme} s={s} f={f} length={length} inset={inset} k={k} punch={null}
      shots={[{ at: 0, zoom: 1.12, x: 540, y: 800 }, { at: 30, zoom: 1, x: 540, y: 960 }]}>
      <Reveal theme={theme} role="media" f={f} at={mediaAt} fps={fps}
        style={bleed ? { inset: 0 } : { left: 70 * s, top: 170 * s, width: 940 * s, height: 1180 * s, ...tilt(theme, 1.1) }}>
        {!bleed && (
          <Paper theme={theme} seed={111} color="#E8E4D8" scale={s} strength={0.3} style={{ position: "absolute", inset: 0 }} />
        )}
        <div style={{ position: "absolute", inset: bleed ? 0 : 22 * s }}>{body}</div>
        <Tape theme={theme} seed={2} scale={s} width={250} style={{ left: -50 * s, top: -10 * s, transform: "rotate(-26deg)" }} />
        <Tape theme={theme} seed={3} scale={s} width={250} style={{ right: -50 * s, top: 0, transform: "rotate(24deg)" }} />
      </Reveal>
      {card.caption && (
        <Reveal theme={theme} role="strip" f={f} at={captionAt} fps={fps} style={{ left: 110 * s, top: (bleed ? 170 : 1290) * s, ...tilt(theme, -0.8) }}>
          <Strip theme={theme} seed={112} s={s} color={bleed ? theme.color.ground : undefined}>{card.caption}</Strip>
        </Reveal>
      )}
    </Page>
  );
};

const ScenePage: React.FC<PageProps<"scene"> & { length: number }> = ({ card, theme, s, f, fps, length, inset, k }) => {
  const next = planner(theme, 0);
  const sheetAt = next("media");
  const bodyAt = next("item");
  const surface = theme.surface === "none" ? theme.color.ground : theme.color.sheet;
  const room = hasMedia(card.blocks)
    ? 940
    : Math.min(940, Math.max(360, sceneHeight(card.blocks, card.layout ?? "stack", card.gap ?? "group") * 920 / SCENE_WIDTH + 80));
  const top = 250 + (940 - room) / 2;
  return (
    <Page theme={theme} s={s} f={f} length={length} inset={inset} k={k} punch={null}
      shots={[{ at: 0, zoom: 1.12, x: 540, y: 700 }, { at: bodyAt + 14, zoom: 1, x: 540, y: 960 }]}>
      <Reveal theme={theme} role="media" f={f} at={sheetAt} fps={fps}
        style={{ left: 40 * s, top: (top - 60) * s, width: 1000 * s, height: (room + 120) * s, ...tilt(theme, -0.8) }}>
        <Paper theme={theme} seed={121} color={surface} scale={s} style={{ width: "100%", height: "100%" }} />
      </Reveal>
      <Reveal theme={theme} role="item" f={f} at={bodyAt} fps={fps}
        style={{ left: 80 * s, top: top * s, width: 920 * s, height: room * s, display: "flex", flexDirection: "column", justifyContent: "center", ...tilt(theme, -0.8) }}>
        <Scene blocks={card.blocks} layout={card.layout} gap={card.gap} scale={(s * 920) / SCENE_WIDTH} room={room * s}
          font={theme.type.label.family}
          brand={{ accent: theme.color.accent, ink: theme.color.ink, surface }} accent={theme.color.accent} />
      </Reveal>
    </Page>
  );
};

const cardTexts = (card: Card): string[] => Object.entries(card).flatMap(([key, value]) => {
  if (key === "kind") return [];
  if (typeof value === "string") return [value];
  if (Array.isArray(value)) return value.filter((item): item is string => typeof item === "string");
  return [];
});

export const paced = (theme: Theme, card: Card, length: number): Theme => {
  const budget = Math.max(6, length * 0.45);
  const hold = Math.max(1, theme.motion.holdEvery);
  const longest = Math.max(0, ...cardTexts(card).map((text) => Array.from(text).length));
  const words = Math.max(0, ...cardTexts(card).map((text) => text.split(/\s+/).filter(Boolean).length));
  if (theme.motion.textUnit === "word") {
    if (words * hold <= budget) return theme;
    return { ...theme, motion: { ...theme.motion, textUnit: "char", charsPerPose: Math.ceil((longest * hold) / budget) } };
  }
  const poses = Math.ceil(longest / theme.motion.charsPerPose);
  if (poses * hold <= budget) return theme;
  return { ...theme, motion: { ...theme.motion, charsPerPose: Math.ceil((longest * hold) / budget) } };
};

export const buildFrames = (theme: Theme, card: Card) => {
  const texts = cardTexts(card);
  const typed = texts.reduce((sum, text) => sum + roleFrames(theme, "title", text), 0);
  return typed + texts.length * (Math.max(2, theme.motion.stagger) + theme.motion.frames) + 24;
};

export const tempo = (theme: Theme, card: Card, length: number) =>
  Math.max(1, buildFrames(theme, card) / Math.max(1, length * 0.55));

export const TakeoverPage: React.FC<{
  card: Card; theme: Theme; s: number; start: number; length: number; inset: number;
}> = ({ card, theme, s, start, length, inset }) => {
  const fitted = paced(theme, card, length);
  const k = tempo(fitted, card, length);
  const f = Math.floor((useCurrentFrame() - start) * k);
  const { fps } = useVideoConfig();
  const props = { theme: fitted, s, f, fps, length: length * k, inset, k };
  if (card.kind === "stat") return <StatPage card={card} {...props} />;
  if (card.kind === "headline") return <HeadlinePage card={card} {...props} />;
  if (card.kind === "entity") return <EntityPage card={card} {...props} />;
  if (card.kind === "bullets") return <BulletsPage card={card} {...props} />;
  if (card.kind === "quote") return <QuotePage card={card} {...props} />;
  if (card.kind === "compare") return <CompareRows card={card} {...props} />;
  if (card.kind === "change") return <ChangePage card={card} {...props} />;
  if (card.kind === "share") return <SharePage card={card} {...props} />;
  if (card.kind === "image" || card.kind === "video") return <MediaPage card={card} {...props} />;
  if (card.kind === "scene") return <ScenePage card={card} {...props} />;
  return null;
};
