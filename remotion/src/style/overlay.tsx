import React from "react";
import { Img, interpolate, useCurrentFrame, useVideoConfig } from "remotion";
import type { Card } from "../cards";
import { fontOf } from "./theme";
import type { Theme } from "./theme";
import { Label, Paper } from "./materials";
import { progress, Reveal, RevealText, roleFrames } from "./reveal";
import { Bar, EXIT_FRAMES, paced, planner, tempo } from "./pages";

export const OVERLAY_KINDS = new Set<Card["kind"]>([
  "stat", "headline", "entity", "bullets", "quote", "compare", "change", "share",
]);

type Props<K extends Card["kind"]> = { card: Extract<Card, { kind: K }>; theme: Theme; s: number; f: number; fps: number };

const Eyebrow: React.FC<{ theme: Theme; s: number; f: number; fps: number; at: number; text?: string }> = ({
  theme, s, f, fps, at, text,
}) => (text ? (
  <Reveal theme={theme} role="label" f={f} at={at} fps={fps} style={{ position: "relative" }}>
    <Label theme={theme} seed={91} scale={s} size={40 * s}>{text}</Label>
  </Reveal>
) : null);

const Line: React.FC<{ theme: Theme; s: number; f: number; fps: number; at: number; size: number; color?: string; children: React.ReactNode }> = ({
  theme, s, f, fps, at, size, color, children,
}) => (
  <Reveal theme={theme} role="strip" f={f} at={at} fps={fps} style={{ position: "relative" }}>
    <div style={{ ...fontOf(theme.type.label, size * s), color: color ?? theme.color.muted }}>{children}</div>
  </Reveal>
);

const Figure: React.FC<{ theme: Theme; s: number; size: number; color?: string; children: React.ReactNode }> = ({
  theme, s, size, color, children,
}) => (theme.surface === "paper" ? (
  <Label theme={theme} seed={93} scale={s} size={size * s} ink={theme.color.figure} pad={0.12}>{children}</Label>
) : (
  <div style={{ ...fontOf(theme.type.display, size * s), color: color ?? theme.color.accent, lineHeight: 1 }}>{children}</div>
));

const StatBody: React.FC<Props<"stat">> = ({ card, theme, s, f, fps }) => {
  const next = planner(theme);
  const eyebrowAt = card.eyebrow ? next("label") : 0;
  const figureAt = next("figure", card.value);
  const captionAt = next("strip");
  return (
    <>
      <Eyebrow theme={theme} s={s} f={f} fps={fps} at={eyebrowAt} text={card.eyebrow} />
      <Reveal theme={theme} role="figure" f={f} at={figureAt} fps={fps} style={{ position: "relative" }}>
        <Figure theme={theme} s={s} size={120}>
          <RevealText theme={theme} role="figure" f={f} at={figureAt} text={card.value} s={s} />
        </Figure>
      </Reveal>
      {card.caption && <Line theme={theme} s={s} f={f} fps={fps} at={captionAt} size={46} color={theme.color.ink}>{card.caption}</Line>}
    </>
  );
};

const HeadlineBody: React.FC<Props<"headline">> = ({ card, theme, s, f, fps }) => {
  const next = planner(theme);
  const eyebrowAt = card.eyebrow ? next("label") : 0;
  const leadAt = next("title", card.lead);
  const markAt = leadAt + roleFrames(theme, "title", card.lead) + 2;
  const subAt = next("strip");
  const mark = Array.isArray(card.emphasis) ? card.emphasis[0] : card.emphasis;
  return (
    <>
      <Eyebrow theme={theme} s={s} f={f} fps={fps} at={eyebrowAt} text={card.eyebrow} />
      <Reveal theme={theme} role="title" f={f} at={leadAt} fps={fps} style={{ position: "relative" }}>
        <div style={{ ...fontOf(theme.type.display, 70 * s), color: theme.color.ink }}>
          <RevealText theme={theme} role="title" f={f} at={leadAt} text={card.lead} mark={mark} markAt={markAt} s={s} />
        </div>
      </Reveal>
      {card.sub && <Line theme={theme} s={s} f={f} fps={fps} at={subAt} size={42}>{card.sub}</Line>}
    </>
  );
};

const EntityBody: React.FC<Props<"entity">> = ({ card, theme, s, f, fps }) => {
  const next = planner(theme);
  const mediaAt = card.src ? next("media") : 0;
  const nameAt = next("figure", card.name);
  const noteAt = next("strip");
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 32 * s }}>
      {card.src && (
        <Reveal theme={theme} role="media" f={f} at={mediaAt} fps={fps} style={{ position: "relative", flex: "none" }}>
          <Img src={card.src} alt="" style={{ display: "block", width: 200 * s, height: 200 * s, objectFit: "cover" }} />
        </Reveal>
      )}
      <div style={{ display: "flex", flexDirection: "column", gap: 10 * s, minWidth: 0 }}>
        <Eyebrow theme={theme} s={s} f={f} fps={fps} at={0} text={card.eyebrow} />
        <Reveal theme={theme} role="figure" f={f} at={nameAt} fps={fps} style={{ position: "relative" }}>
          <Figure theme={theme} s={s} size={80}>
            <RevealText theme={theme} role="figure" f={f} at={nameAt} text={card.name} s={s} />
          </Figure>
        </Reveal>
        {card.note && <Line theme={theme} s={s} f={f} fps={fps} at={noteAt} size={44} color={theme.color.ink}>{card.note}</Line>}
      </div>
    </div>
  );
};

const BulletsBody: React.FC<Props<"bullets">> = ({ card, theme, s, f, fps }) => {
  const next = planner(theme);
  const eyebrowAt = card.eyebrow ? next("title", card.eyebrow) : 0;
  const items = card.items.slice(0, 3);
  const ats = items.map(() => next("item"));
  return (
    <>
      <Eyebrow theme={theme} s={s} f={f} fps={fps} at={eyebrowAt} text={card.eyebrow} />
      {items.map((item, i) => (
        <Reveal key={item} theme={theme} role="item" f={f} at={ats[i]} fps={fps} style={{ position: "relative" }}>
          <div style={{ display: "flex", alignItems: "baseline", gap: 22 * s }}>
            <span style={{ ...fontOf(theme.type.label, 54 * s), color: theme.color.accent, fontWeight: 700 }}>{i + 1}</span>
            <span style={{ ...fontOf(theme.type.display, 54 * s), color: theme.color.ink }}>
              <RevealText theme={theme} role="item" f={f} at={ats[i]} text={item} s={s} />
            </span>
          </div>
        </Reveal>
      ))}
    </>
  );
};

const QuoteBody: React.FC<Props<"quote">> = ({ card, theme, s, f, fps }) => {
  const next = planner(theme);
  const textAt = next("title", card.text);
  const byAt = next("label");
  return (
    <>
      <Reveal theme={theme} role="title" f={f} at={textAt} fps={fps} style={{ position: "relative" }}>
        <div style={{ ...fontOf({ ...theme.type.display, weight: 700 }, 58 * s), color: theme.color.ink }}>
          <span style={{ color: theme.color.accent }}>“</span>
          <RevealText theme={theme} role="title" f={f} at={textAt} text={card.text} s={s} />
        </div>
      </Reveal>
      {card.attribution && <Line theme={theme} s={s} f={f} fps={fps} at={byAt} size={42}>{card.attribution}</Line>}
    </>
  );
};

const CompareBody: React.FC<Props<"compare">> = ({ card, theme, s, f, fps }) => {
  const next = planner(theme);
  const eyebrowAt = card.eyebrow ? next("label") : 0;
  const rows = card.rows.slice(0, 3);
  const ats = rows.map(() => next("item"));
  const top = Math.max(...rows.map((row) => row.value), 1);
  return (
    <>
      <Eyebrow theme={theme} s={s} f={f} fps={fps} at={eyebrowAt} text={card.eyebrow} />
      {rows.map((row, i) => (
        <Reveal key={`${row.label}-${i}`} theme={theme} role="item" f={f} at={ats[i]} fps={fps} style={{ position: "relative", width: "100%" }}>
          <div style={{ ...fontOf(theme.type.label, 44 * s), color: theme.color.ink, marginBottom: 8 * s }}>{row.label}</div>
          <div style={{ display: "flex", alignItems: "center", gap: 16 * s }}>
            <div style={{ width: `${Math.max(10, (row.value / top) * 72)}%` }}>
              <Bar theme={theme} seed={95 + i} s={s} height={44} color={row.subject ? theme.color.accent : theme.color.muted} />
            </div>
            <div style={{ ...fontOf(theme.type.label, 56 * s), fontWeight: 700, color: theme.color.ink }}>{row.display ?? String(row.value)}</div>
          </div>
        </Reveal>
      ))}
    </>
  );
};

const ChangeBody: React.FC<Props<"change">> = ({ card, theme, s, f, fps }) => {
  const next = planner(theme);
  const title = card.eyebrow ?? card.label;
  const eyebrowAt = title ? next("label") : 0;
  const fromAt = next("item");
  const toAt = next("figure", card.to.value) + 4;
  return (
    <>
      <Eyebrow theme={theme} s={s} f={f} fps={fps} at={eyebrowAt} text={title} />
      <div style={{ display: "flex", alignItems: "baseline", gap: 28 * s, flexWrap: "wrap" }}>
        <Reveal theme={theme} role="item" f={f} at={fromAt} fps={fps} style={{ position: "relative" }}>
          <div style={{ ...fontOf(theme.type.display, 72 * s), color: theme.color.muted }}>{card.from.value}</div>
          {card.from.note && <div style={{ ...fontOf(theme.type.label, 36 * s), color: theme.color.muted, marginTop: 8 * s }}>{card.from.note}</div>}
        </Reveal>
        <Reveal theme={theme} role="item" f={f} at={fromAt + 4} fps={fps} style={{ position: "relative" }}>
          <div style={{ ...fontOf(theme.type.label, 64 * s), color: theme.color.accent }}>→</div>
        </Reveal>
        <Reveal theme={theme} role="figure" f={f} at={toAt} fps={fps} style={{ position: "relative" }}>
          <Figure theme={theme} s={s} size={100}>
            <RevealText theme={theme} role="figure" f={f} at={toAt} text={card.to.value} s={s} />
          </Figure>
          {card.to.note && <div style={{ ...fontOf(theme.type.label, 36 * s), color: theme.color.ink, marginTop: 8 * s }}>{card.to.note}</div>}
        </Reveal>
      </div>
    </>
  );
};

const ShareBody: React.FC<Props<"share">> = ({ card, theme, s, f, fps }) => {
  const share = Math.max(0, Math.min(1, card.value));
  const display = card.display ?? `${Math.round(share * 100)}%`;
  const next = planner(theme);
  const eyebrowAt = card.eyebrow ? next("label") : 0;
  const figureAt = next("figure", display);
  const barAt = next("item");
  const fill = progress(theme.motion, f, barAt, Math.max(12, theme.motion.frames * 2));
  return (
    <>
      <Eyebrow theme={theme} s={s} f={f} fps={fps} at={eyebrowAt} text={card.eyebrow} />
      <Reveal theme={theme} role="figure" f={f} at={figureAt} fps={fps} style={{ position: "relative" }}>
        <Figure theme={theme} s={s} size={110}>
          <RevealText theme={theme} role="figure" f={f} at={figureAt} text={display} s={s} />
        </Figure>
      </Reveal>
      <Reveal theme={theme} role="item" f={f} at={barAt} fps={fps} style={{ position: "relative", width: "100%" }}>
        <Bar theme={theme} seed={99} s={s} height={36} color={theme.color.muted}>
          <div style={{ position: "absolute", left: 0, top: 0, bottom: 0, width: `${share * fill * 100}%`, backgroundColor: theme.color.accent }} />
        </Bar>
        {card.caption && <div style={{ ...fontOf(theme.type.label, 44 * s), color: theme.color.ink, marginTop: 16 * s }}>{card.caption}</div>}
      </Reveal>
    </>
  );
};

const Body: React.FC<{ card: Card; theme: Theme; s: number; f: number; fps: number }> = ({ card, ...rest }) => {
  if (card.kind === "stat") return <StatBody card={card} {...rest} />;
  if (card.kind === "headline") return <HeadlineBody card={card} {...rest} />;
  if (card.kind === "entity") return <EntityBody card={card} {...rest} />;
  if (card.kind === "bullets") return <BulletsBody card={card} {...rest} />;
  if (card.kind === "quote") return <QuoteBody card={card} {...rest} />;
  if (card.kind === "compare") return <CompareBody card={card} {...rest} />;
  if (card.kind === "change") return <ChangeBody card={card} {...rest} />;
  if (card.kind === "share") return <ShareBody card={card} {...rest} />;
  return null;
};

export const OverlayCard: React.FC<{
  card: Card; theme: Theme; s: number; start: number; length: number; top: number;
}> = ({ card, theme, s, start, length, top }) => {
  const fitted = paced(theme, card, length);
  const k = tempo(fitted, card, length);
  const f = Math.floor((useCurrentFrame() - start) * k);
  const { fps } = useVideoConfig();
  const out = interpolate(f, [length * k - EXIT_FRAMES * k, length * k], [1, 0], {
    extrapolateLeft: "clamp", extrapolateRight: "clamp",
  });
  const panelIn = progress(fitted.motion, f, 0, 6);
  const paper = fitted.surface === "paper";
  const inner = (
    <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-start", gap: 16 * s, padding: `${34 * s}px ${44 * s}px ${40 * s}px` }}>
      <Body card={card} theme={fitted} s={s} f={f} fps={fps} />
    </div>
  );
  return (
    <div style={{
      position: "absolute", left: 48 * s, right: 48 * s, top, opacity: out * panelIn,
      transform: `translateY(${(1 - panelIn) * -24 * s}px)`,
    }}>
      {paper ? (
        <Paper theme={fitted} seed={90} color={fitted.color.sheet} scale={s}>{inner}</Paper>
      ) : (
        <div style={{ backgroundColor: fitted.color.ground }}>{inner}</div>
      )}
    </div>
  );
};
