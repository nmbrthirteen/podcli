import React from "react";
import {
  getRemotionEnvironment, Img, OffthreadVideo, Video, staticFile,
  useCurrentFrame, useVideoConfig,
} from "remotion";
import { captionScale, captionZone, FONT, safeFor } from "../types";
import type { CaptionStyle } from "../types";
import { MOTION, motionAt } from "../motion";
import { cardAt } from "../cards";
import type { Card } from "../cards";
import { context, DEFAULT_BRAND, muted, track } from "./brand";
import type { Brand } from "./brand";
import { fitScene, Scene } from "./Scene";
import { emphasisRuns, MIN_FIT, SCENE_WIDTH, sceneHeight } from "../scene";
import type { Block } from "../scene";

/**
 * A card keeps the person on screen.
 *
 * The formats this borrows from are news explainers: they cut away from the
 * presenter constantly, because what they are narrating is an announcement and
 * the presenter is only reading it out. A podcast is the other way round. The
 * moment is somebody saying something, and cutting their face out mid-sentence
 * throws away the only thing the clip has.
 *
 * So a card takes the band directly above the captions and the speaker holds
 * everything above that. The eye travels from face to point to words without
 * ever leaving the frame, and a card that arrives is an addition rather than a
 * cutaway to a different video.
 */

export type { Brand } from "./brand";
export { DEFAULT_BRAND } from "./brand";

/**
 * A three-step rhythm, so grouping reads from the gaps rather than from rules.
 * The first pass used 22, 24, 26, 28 and 32, which is one gap five times.
 */
const GAP = { tight: 14, group: 36, section: 76 };

/**
 * Marks, thin and unfussy.
 *
 * `cap` rounds the data end of a bar only. `gap` is the surface showing between
 * adjacent marks, which is what keeps two bars from reading as one shape.
 */
const MARK = { bar: 26, cap: 8, gap: 30, rule: 5, dot: 26 };

/** Authored for the 1920-tall canvas, like every other measurement here. */
const TYPE = { label: 34, body: 46, item: 58, quote: 68, lead: 84, figure: 168 };

/**
 * How much of the frame a video card's footage takes.
 *
 * The arithmetic it has to fit: a 1920 canvas, roughly 750 reserved for the
 * captions and for the app's own furniture under them, 76 of gap, and the
 * speaker's floor of 680 above it. What is left is this. Asking for the
 * literal half of the frame would push the band under that floor, and the
 * card would answer by dropping the speaker altogether, which is the opposite
 * of what a split screen is for.
 *
 * It shrinks before the speaker does, because a caption style with tall words
 * has to take its room from somewhere and a face cut in half is worse than
 * footage cropped a little tighter.
 */
const VIDEO_BAND = 540;

/** A picture is capped a little shorter, having no motion to carry it. */
const IMAGE_BAND = 520;

/**
 * The least a file is worth keeping the speaker for.
 *
 * Captions placed high on the frame reserve their band from the same 1920,
 * and what is left after a speaker can be a few dozen pixels. A letterbox
 * slit of a factory floor says nothing while still costing the frame it sits
 * in, so short of this the card takes the whole frame and the file gets a
 * size worth looking at. The same call the speaker's own floor makes, made
 * for the other half.
 */
const MEDIA_MIN = 260;

/** The two kinds that show a file, and are given a height rather than take one. */
const SHOWS_A_FILE = new Set(["image", "video"]);

/**
 * How many lines a card's own caption can run to.
 *
 * Two, because the API cuts a caption at ninety characters and ninety
 * characters of label type do not fit on one line of this frame. Reserving one
 * was reserving for the caption that happens to be short, and the second line
 * of a long one came out of the band below it — which is the caption pill.
 */
const CAPTION_LINES_MEDIA = 2;

/** What a card's own caption costs whatever is above it. */
const CAPTION_ROW = TYPE.label * 1.3 * CAPTION_LINES_MEDIA + GAP.tight;

/**
 * How tall the file is in a body of this height.
 *
 * One formula, because two things ask: the card, to decide whether keeping
 * the speaker still leaves something worth showing, and the body, to draw it.
 * Asked twice and answered differently, the card keeps a speaker for a band
 * it then draws too short to read.
 */
const mediaBand = (
  kind: string, bodyRoom: number, s: number, hasCaption: boolean,
) => Math.min(
  (kind === "image" ? IMAGE_BAND : VIDEO_BAND) * s,
  Math.max(0, bodyRoom - (hasCaption ? CAPTION_ROW * s : 0)),
);

/**
 * The least of the frame the speaker keeps.
 *
 * The band is whatever the card does not need rather than a fixed height: a
 * two-line quote and a bare figure want different amounts, and pinning both
 * ends meant a tall card overflowed upward across the speaker's chin. This
 * floor stops a very long card from squeezing the face out altogether.
 *
 * It was 820 while the captions were free to drop to the bottom edge. They
 * are not: the bottom 520 belongs to the app the clip plays in. The band the
 * captions gave back has to come off this floor, because the alternative is a
 * card written into the part of the frame a viewer never sees.
 */
const SPEAKER_MIN = 680;

/**
 * How far the footage takes to become the card.
 *
 * Long enough to read as a ramp rather than a band, short enough that the
 * shoulder it eats is a shoulder and not a face.
 */
const SEAM = 160;

/**
 * How much room a head needs, given how big the face is.
 *
 * A detected face box is the face, not the head: it stops at the hairline and
 * at the chin, and a band cut to it lands exactly on both. Two and a half
 * times leaves the skull above and the jaw and some shoulder below, which is
 * the framing anyone would choose by hand.
 *
 * Without a measurement this falls back to the flat floor above, which is a
 * guess that happens to suit a mid-shot and is wrong on a close-up. That is
 * the honest state of it: the detector has the number, an older engine does
 * not send it, and a clip rendered by one should not look broken.
 */
const HEAD_TO_FACE = 2.5;

/*
 * The panel runs to the bottom edge, and that is not negotiable.
 *
 * It was ended under the caption for a while, to close the empty strip a
 * caption's own margin leaves below it. What that actually did was put a band
 * of the speaker back under the card: head above, panel across, chin below,
 * so one person appeared twice in a frame with a slab through the middle of
 * their face. Empty surface under a caption reads as a floor. A face cut in
 * half reads as a mistake, and it is the more expensive of the two by far.
 */

/**
 * The line above the payload, when there is something worth saying there.
 *
 * Set in the same white as everything else rather than the accent. An accent
 * on the label and on the figure is the same swatch twice on one card, which
 * is how a colour stops meaning anything.
 */
const Label: React.FC<{ text: string; scale: number; tone: string }> = ({
  text, scale: s, tone,
}) => (
  <div
    style={{
      fontFamily: FONT,
      fontSize: TYPE.label * s,
      fontWeight: 600,
      letterSpacing: 1.5 * s,
      color: tone,
      marginBottom: GAP.tight * s,
    }}
  >
    {text}
  </div>
);

/** One end of a change, as a value with its note under it. */
const Endpoint: React.FC<{
  value: string; note?: string; tone: string; scale: number; brand: Brand;
}> = ({ value, note, tone, scale: s, brand }) => (
  <div style={{ textAlign: "center" }}>
    <div
      style={{
        width: MARK.dot * s, height: MARK.dot * s, borderRadius: "50%",
        backgroundColor: tone, margin: "0 auto",
      }}
    />
    <div
      style={{
        fontFamily: FONT, fontSize: TYPE.item * s, fontWeight: 700,
        color: brand.ink, marginTop: 12 * s, lineHeight: 1,
      }}
    >
      {value}
    </div>
    {note && (
      <div
        style={{
          fontFamily: FONT, fontSize: TYPE.label * s, color: muted(brand.ink),
          marginTop: 6 * s,
        }}
      >
        {note}
      </div>
    )}
  </div>
);

/**
 * A file given the whole frame, the way a cutaway is.
 *
 * Cover rather than contain, because a cutaway that letterboxes itself is a
 * picture of a picture. A screenshot says otherwise by asking to be fitted,
 * and gets the surface behind it rather than bars of nothing.
 *
 * Any caption it carries goes to the top. The bottom of the frame is spoken
 * for twice over down there, by the clip's own captions and by the app's
 * furniture under them, and a source line is not worth fighting either for.
 */
const BleedMedia: React.FC<{
  card: Extract<Card, { kind: "image" | "video" }>;
  scale: number;
  brand: Brand;
  fps: number;
  topInset: number;
}> = ({ card, scale: s, brand, fps, topInset }) => {
  const { height, width } = useVideoConfig();
  const SAFE = safeFor(width, height);
  const src = card.src.startsWith("http") ? card.src : staticFile(card.src);
  const fit = card.fit === "fit" ? "contain" : "cover";
  const box: React.CSSProperties = {
    position: "absolute", inset: 0, width: "100%", height: "100%",
    objectFit: fit, backgroundColor: card.fit === "fit" ? brand.surface : "#000",
  };
  const Frame = getRemotionEnvironment().isRendering ? OffthreadVideo : Video;

  return (
    <>
      {card.kind === "video" ? (
        <Frame
          src={src}
          startFrom={Math.max(0, Math.round((card.startAt ?? 0) * fps))}
          muted
          style={box}
        />
      ) : (
        <Img src={src} style={box} />
      )}
      {card.caption && (
        <div
          style={{
            position: "absolute", top: 0, left: 0, right: 0,
            padding: `${topInset * s}px ${SAFE.right * s}px ${GAP.section * s}px `
              + `${SAFE.left * s}px`,
            background:
              `linear-gradient(to bottom, rgba(0,0,0,0.65), transparent)`,
            fontFamily: FONT,
            fontSize: TYPE.label * s,
            fontWeight: 500,
            lineHeight: 1.3,
            color: "#FFFFFF",
          }}
        >
          {card.caption}
        </div>
      )}
    </>
  );
};

const CardBody: React.FC<{
  card: Card;
  scale: number;
  brand: Brand;
  /**
   * The height the body may take, for the one kind that has to be told.
   *
   * Every other card is as tall as its own words and the layout absorbs it.
   * Footage is given a height, and a height that does not fit is a card
   * drawn over the captions rather than a card that came out short.
   */
  room?: number;
  /**
   * Whether a file takes the whole frame rather than a band of it.
   *
   * Set when the card has already given up the speaker. A photograph nobody
   * kept a face for was still being drawn at a quarter of the height, which
   * left it floating in an empty slab: the layout of a split screen with
   * nothing in the other half. Given the frame, it is a cutaway, which is what
   * dropping the speaker was for.
   */
  bleed?: boolean;
  /** How much of the top the logo and the chip have already taken. */
  topInset?: number;
}> = ({
  card, scale: s, brand, room, bleed = false, topInset,
}) => {
  // Read before any of the branches below, so a card kind that never uses it
  // does not change the order the hooks run in.
  const { fps, height, width } = useVideoConfig();
  const SAFE = safeFor(width, height);
  const accent = card.accent ?? brand.accent;
  const INK = brand.ink;
  const MUTED = muted(brand.ink);
  const CONTEXT = context(brand.ink);

  if (card.kind === "scene") {
    return (
      <Scene
        blocks={card.blocks}
        layout={card.layout}
        gap={card.gap}
        brand={brand}
        accent={accent}
        scale={s}
        room={room ?? Infinity}
      />
    );
  }

  if (card.kind === "stat") {
    return (
      <>
        {card.eyebrow && <Label text={card.eyebrow} scale={s} tone={MUTED} />}
        <div
          style={{
            fontFamily: FONT,
            fontSize: TYPE.figure * s,
            fontWeight: 700,
            lineHeight: 0.95,
            letterSpacing: -4 * s,
            color: accent,
          }}
        >
          {card.value}
        </div>
        {card.caption && (
          <div
            style={{
              fontFamily: FONT,
              fontSize: TYPE.body * s,
              fontWeight: 400,
              lineHeight: 1.3,
              color: INK,
              marginTop: GAP.tight * s,
              maxWidth: "80%",
            }}
          >
            {card.caption}
          </div>
        )}
      </>
    );
  }

  if (card.kind === "headline") {
    return (
      <>
        {card.eyebrow && <Label text={card.eyebrow} scale={s} tone={MUTED} />}
        <div
          style={{
            fontFamily: FONT,
            fontSize: TYPE.lead * s,
            fontWeight: 600,
            lineHeight: 1.12,
            letterSpacing: -1 * s,
            color: INK,
          }}
        >
          {/* The payload words carry the card's one accent. Italic as well
              would be two ways of saying the same thing. */}
          {emphasisRuns(card.lead, card.emphasis).map((run, i) => (
            <span key={i} style={run.mark ? { color: accent } : undefined}>{run.text}</span>
          ))}
        </div>
        {card.sub && (
          <div
            style={{
              fontFamily: FONT,
              fontSize: TYPE.body * s,
              fontWeight: 400,
              lineHeight: 1.3,
              color: MUTED,
              marginTop: GAP.tight * s,
              maxWidth: "80%",
            }}
          >
            {card.sub}
          </div>
        )}
      </>
    );
  }

  if (card.kind === "bullets") {
    return (
      <>
        {card.eyebrow && <Label text={card.eyebrow} scale={s} tone={MUTED} />}
        {card.items.map((item, i) => (
          <div
            key={i}
            style={{
              display: "flex",
              alignItems: "center",
              gap: 20 * s,
              marginTop: i === 0 ? 0 : GAP.tight * s,
            }}
          >
            {/* A rule rather than a bullet glyph or an icon: it is a list
                marker, and a dot in a coloured circle is chrome. */}
            <div
              style={{
                width: 26 * s,
                height: 3 * s,
                backgroundColor: i === 0 ? accent : MUTED,
                flexShrink: 0,
              }}
            />
            <div
              style={{
                fontFamily: FONT,
                fontSize: TYPE.item * s,
                fontWeight: 500,
                lineHeight: 1.25,
                color: INK,
              }}
            >
              {item}
            </div>
          </div>
        ))}
      </>
    );
  }

  if (card.kind === "compare") {
    const top = Math.max(...card.rows.map((r) => r.value)) || 1;
    return (
      <>
        {card.eyebrow && <Label text={card.eyebrow} scale={s} tone={MUTED} />}
        {card.rows.slice(0, 3).map((row, i) => (
          <div key={i} style={{ marginTop: i === 0 ? 0 : MARK.gap * s }}>
            <div
              style={{
                display: "flex",
                justifyContent: "space-between",
                alignItems: "baseline",
                marginBottom: 8 * s,
              }}
            >
              {/* Ink, not the bar's colour: the mark beside it carries identity. */}
              <span style={{ fontFamily: FONT, fontSize: TYPE.label * s, color: MUTED }}>
                {row.label}
              </span>
              <span
                style={{
                  fontFamily: FONT, fontSize: TYPE.body * s, fontWeight: 700,
                  color: row.subject ? INK : MUTED,
                }}
              >
                {row.display ?? row.value}
              </span>
            </div>
            <div
              style={{
                width: `${Math.max(4, (row.value / top) * 100)}%`,
                height: MARK.bar * s,
                backgroundColor: row.subject ? accent : CONTEXT,
                // Rounded at the data end only. A bar rounded at the baseline
                // reads as floating away from the axis it is measured from.
                borderRadius: `0 ${MARK.cap * s}px ${MARK.cap * s}px 0`,
              }}
            />
          </div>
        ))}
      </>
    );
  }

  if (card.kind === "change") {
    return (
      <>
        {card.eyebrow && <Label text={card.eyebrow} scale={s} tone={MUTED} />}
        <div style={{ display: "flex", alignItems: "center", gap: 0 }}>
          <Endpoint value={card.from.value} note={card.from.note} tone={CONTEXT} scale={s} brand={brand} />
          <div
            style={{
              flex: 1,
              height: MARK.rule * s,
              // One hue in two steps: the same measure, later.
              background: `linear-gradient(90deg, ${CONTEXT}, ${accent})`,
              margin: `0 ${18 * s}px`,
              transform: `translateY(${-14 * s}px)`,
            }}
          />
          <Endpoint value={card.to.value} note={card.to.note} tone={accent} scale={s} brand={brand} />
        </div>
      </>
    );
  }

  if (card.kind === "share") {
    const pct = Math.max(0, Math.min(1, card.value));
    return (
      <>
        {card.eyebrow && <Label text={card.eyebrow} scale={s} tone={MUTED} />}
        <div
          style={{
            fontFamily: FONT, fontSize: TYPE.figure * 0.72 * s, fontWeight: 700,
            lineHeight: 1, letterSpacing: -3 * s, color: accent,
            marginBottom: GAP.tight * s,
          }}
        >
          {card.display ?? `${Math.round(pct * 100)}%`}
        </div>
        {/* The unfilled track is a lighter step of the fill's own ramp, so the
            whole bar reads as one measure rather than as bar-on-background. */}
        <div
          style={{
            width: "100%", height: MARK.bar * s,
            backgroundColor: track(accent), borderRadius: MARK.cap * s,
            overflow: "hidden",
          }}
        >
          <div style={{ width: `${pct * 100}%`, height: "100%", backgroundColor: accent }} />
        </div>
        {card.caption && (
          <div
            style={{
              fontFamily: FONT, fontSize: TYPE.body * s, color: INK,
              lineHeight: 1.3, marginTop: GAP.tight * s, maxWidth: "85%",
            }}
          >
            {card.caption}
          </div>
        )}
      </>
    );
  }

  if (card.kind === "entity") {
    return (
      <>
        {card.eyebrow && <Label text={card.eyebrow} scale={s} tone={MUTED} />}
        <div style={{ display: "flex", alignItems: "center", gap: 28 * s }}>
          {card.src && (
            <Img
              src={card.src.startsWith("http") ? card.src : staticFile(card.src)}
              style={{
                width: 132 * s, height: 132 * s, objectFit: "cover",
                borderRadius: 20 * s, flexShrink: 0,
                outline: `${1 * s}px solid rgba(255,255,255,0.1)`,
                outlineOffset: `${-1 * s}px`,
              }}
            />
          )}
          <div>
            <div
              style={{
                fontFamily: FONT, fontSize: TYPE.item * s, fontWeight: 700,
                lineHeight: 1.15, color: INK,
              }}
            >
              {card.name}
            </div>
            {card.note && (
              <div
                style={{
                  fontFamily: FONT, fontSize: TYPE.body * s, color: MUTED,
                  lineHeight: 1.3, marginTop: 6 * s,
                }}
              >
                {card.note}
              </div>
            )}
          </div>
        </div>
      </>
    );
  }

  if (bleed && (card.kind === "image" || card.kind === "video")) {
    return (
      <BleedMedia
        card={card} scale={s} brand={brand} fps={fps}
        topInset={topInset ?? SAFE.top}
      />
    );
  }

  if (card.kind === "image") {
    return (
      <>
        <Img
          src={card.src.startsWith("http") ? card.src : staticFile(card.src)}
          style={{
            /*
             * Shown whole, the element is sized by the picture rather than the
             * other way round.
             *
             * A full-width box with objectFit contain letterboxes inside
             * itself, and the hairline below then traces the box: a tall
             * screenshot came out as a thin picture adrift in a wide empty
             * rectangle. Bounded instead of sized, the element is the picture,
             * so the line lands on its edge.
             */
            ...(card.fit === "fill"
              ? { width: "100%", objectFit: "cover" as const }
              : {
                maxWidth: "100%",
                alignSelf: "center" as const,
                objectFit: "contain" as const,
              }),
            // Capped by the room actually left rather than by a number alone:
            // captions placed high reserve their band from the same frame, and
            // a picture pinned to a fixed height drew over them.
            maxHeight: mediaBand("image", room ?? IMAGE_BAND * s, s, Boolean(card.caption)),
            borderRadius: 16 * s,
            // A screenshot's own edge is often near the surface tone behind it.
            // Pure white at a tenth, never a tone borrowed from the palette.
            outline: `${1 * s}px solid rgba(255,255,255,0.1)`,
            outlineOffset: `${-1 * s}px`,
          }}
        />
        {card.caption && (
          <div
            style={{
              fontFamily: FONT,
              fontSize: TYPE.label * s,
              fontWeight: 400,
              lineHeight: 1.3,
              color: MUTED,
              marginTop: GAP.tight * s,
            }}
          >
            {card.caption}
          </div>
        )}
      </>
    );
  }

  if (card.kind === "video") {
    // The same split as the speaker above it: the player seeks a <video> per
    // frame, and a render has no seekable player to drive.
    const Frame = getRemotionEnvironment().isRendering ? OffthreadVideo : Video;
    return (
      <>
        <Frame
          src={card.src.startsWith("http") ? card.src : staticFile(card.src)}
          startFrom={Math.max(0, Math.round((card.startAt ?? 0) * fps))}
          muted
          style={{
            width: "100%",
            height: mediaBand("video", room ?? VIDEO_BAND * s, s, Boolean(card.caption)),
            // Black rather than the surface tone: letterboxing that matches
            // the card reads as a card drawn short, where black reads as the
            // shape of the footage, which is what it is.
            backgroundColor: "#000",
            objectFit: card.fit === "fit" ? "contain" : "cover",
          }}
        />
        {card.caption && (
          <div
            style={{
              fontFamily: FONT,
              fontSize: TYPE.label * s,
              fontWeight: 400,
              lineHeight: 1.3,
              color: MUTED,
              // Its own inset: the footage above it runs to both edges, and a
              // caption that did the same would sit against the frame.
              padding: `0 ${SAFE.right * s}px 0 ${SAFE.left * s}px`,
              marginTop: GAP.tight * s,
            }}
          >
            {card.caption}
          </div>
        )}
      </>
    );
  }

  if (card.kind !== "quote") return null;

  return (
    <>
      <div
        style={{
          fontFamily: FONT,
          fontSize: TYPE.quote * s,
          fontWeight: 500,
          lineHeight: 1.25,
          letterSpacing: -0.5 * s,
          color: INK,
          // Hung, so the first letter of the line sits on the same edge as
          // everything below it rather than a quote mark's worth to the right.
          textIndent: `${-18 * s}px`,
        }}
      >
        “{card.text}”
      </div>
      {card.attribution && (
        <div
          style={{
            fontFamily: FONT,
            fontSize: TYPE.label * s,
            fontWeight: 600,
            letterSpacing: 1.5 * s,
            color: MUTED,
            marginTop: GAP.group * s,
          }}
        >
          {card.attribution}
        </div>
      )}
    </>
  );
};

/**
 * The speaker, still on screen, above the card.
 *
 * Each environment gets the video component it can actually play. The
 * in-browser player seeks a <video> element per frame, and OffthreadVideo
 * hangs there when the file is served without range requests, which left every
 * preview stuck on the first card. A render is the reverse: it has no seekable
 * player, and a <video> element it has to drive frame by frame stalls until
 * the render times out with nothing drawn.
 */
const SpeakerBand: React.FC<{ src: string; startFrom: number; faceY?: number | null }> = ({
  src, startFrom, faceY,
}) => {
  const Frame = getRemotionEnvironment().isRendering ? OffthreadVideo : Video;
  return (
    <Frame
      src={src}
      startFrom={startFrom}
      muted
      style={{
        width: "100%",
        height: "100%",
        objectFit: "cover",
        objectPosition: `50% ${(faceY ?? 0.4) * 100}%`,
      }}
    />
  );
};

/**
 * Every kind this build draws.
 *
 * Written out rather than derived from the union, because the point is to
 * answer for a card that arrived from somewhere newer, and a type cannot.
 */
const KNOWN_KINDS = new Set([
  "stat", "headline", "bullets", "compare", "change", "share", "entity", "quote",
  "image", "video", "scene",
]);

/**
 * A preset written out as the blocks it is made of, for measuring only.
 *
 * The presets used to be trusted to fit, on the grounds that somebody sized
 * them. Somebody sized the layout; nobody sized the words, and five bullets or
 * a quote that runs to nine lines drew straight through the captions and out
 * the bottom of the frame. A scene has been measured since the day it existed,
 * so rather than write a second estimator the preset says which blocks it is
 * and borrows that one.
 *
 * Nothing here draws. The kinds that already answer a height of their own,
 * which is the two that show a file and the scene itself, return null and keep
 * the arithmetic they have.
 */
const presetBlocks = (card: Card): Block[] | null => {
  const eyebrow: Block[] = "eyebrow" in card && card.eyebrow
    ? [{ type: "text", text: card.eyebrow, size: "xs" }]
    : [];
  switch (card.kind) {
    case "stat":
      return [
        ...eyebrow,
        { type: "text", text: card.value, size: "xxl" },
        ...(card.caption ? [{ type: "text", text: card.caption, size: "sm" } as Block] : []),
      ];
    case "headline":
      return [
        ...eyebrow,
        { type: "text", text: card.lead, emphasis: card.emphasis, size: "xl" },
        ...(card.sub ? [{ type: "text", text: card.sub, size: "sm" } as Block] : []),
      ];
    case "bullets":
      return [...eyebrow, { type: "list", items: card.items, size: "md" }];
    case "compare":
      return [...eyebrow, { type: "bars", rows: card.rows.slice(0, 3) }];
    case "change":
      return [...eyebrow, { type: "steps", points: [card.from, card.to] }];
    case "share":
      return [
        ...eyebrow,
        { type: "meter", value: card.value, display: card.display },
        ...(card.caption ? [{ type: "text", text: card.caption, size: "sm" } as Block] : []),
      ];
    case "entity":
      return [...eyebrow, { type: "chip", name: card.name, note: card.note, src: card.src }];
    case "quote":
      return [{ type: "quote", text: card.text, attribution: card.attribution, size: "lg" }];
    default:
      return null;
  }
};

/** A block that would put something on screen, rather than an empty group. */
const drawsSomething = (block: Block): boolean =>
  (block.type === "group"
    ? block.blocks.some(drawsSomething)
    : block.type !== "media" || Boolean(block.src));

export const Cards: React.FC<{
  cards: Card[];
  videoSrc?: string;
  startFrom?: number;
  style: CaptionStyle;
  faceY?: number | null;
  faceH?: number | null;
  brand?: Brand | null;
  /** How much of the top the logo and the chip have already taken. */
  topInset?: number;
}> = ({
  cards, videoSrc, startFrom = 0, style, faceY, faceH, brand, topInset,
}) => {
  const colours = { ...DEFAULT_BRAND, ...(brand ?? {}) };
  const frame = useCurrentFrame();
  const { fps, height, width } = useVideoConfig();
  const SAFE = safeFor(width, height);
  const s = captionScale(height);

  /*
   * Only the cards this build can actually draw.
   *
   * A picture or a piece of footage whose file went missing is dropped rather
   * than drawn: the card is opaque, so rendering it anyway would cover the
   * clip with nothing. A kind this build has never heard of goes the same
   * way. An older engine handed a newer plan used to fall through to the
   * quote branch and draw an empty slab over the speaker, which is the one
   * failure that looks deliberate.
   */
  const usable = cards.filter((c) => {
    if (!KNOWN_KINDS.has(c.kind)) return false;
    if (c.kind === "image" || c.kind === "video") return Boolean(c.src);
    if (c.kind === "scene") return c.blocks.some(drawsSomething);
    return true;
  });
  const card = cardAt(usable, frame / fps);
  if (!card) return null;

  const { opacity } = motionAt({
    frame, fps,
    start: card.start,
    end: card.end,
    motion: card.motion ?? MOTION.card,
  });
  if (opacity <= 0) return null;

  /*
   * The speaker stays, unless what is left of the frame would cut them in half.
   *
   * A band shorter than this shows a forehead and a mouth, which reads worse
   * than not showing the person at all: a viewer reads a slice of a face as a
   * mistake and a full-frame card as a decision. So it is a whole head or
   * none, never part of one.
   */
  const room = height - (captionZone(style) + GAP.section) * s;
  const headNeeds = faceH ? faceH * HEAD_TO_FACE * height : SPEAKER_MIN * s;
  /*
   * A file keeps the speaker only while both still fit.
   *
   * The speaker's floor is the first claim on the frame and the captions are
   * reserved before either, so on a clip whose captions sit high there can be
   * too little left to draw footage in at all. Rather than overflow the
   * caption band, the card gives up the speaker and takes the whole frame,
   * which is what it would have done for a card too tall to share.
   */
  /*
   * What the speaker actually occupies, which is not always what it needs.
   *
   * A measured face gives the height a head wants, and on a wide shot that is
   * less than the floor the band is drawn with. The layout hands the speaker
   * the larger of the two, so the room left for a file is measured against
   * that rather than against the smaller number, or the band is computed
   * against space the speaker has already taken.
   */
  const speakerTakes = Math.max(headNeeds, SPEAKER_MIN * s);
  const bodyRoom = (available: number) => Math.max(0, available - GAP.section * s);
  // Asked of the union rather than of one kind: only some cards have a caption
  // at all, and the fit arithmetic runs before anything has narrowed to one.
  const hasCaption = "caption" in card && Boolean(card.caption);
  /*
   * A card keeps the speaker while it can still be read beside one.
   *
   * Measured for every kind rather than for the scene alone. A card that would
   * have to be squashed to the floor of the fit takes the whole frame instead
   * of sharing it at a size nobody reads at arm's length, and that call is the
   * same call whether the arrangement was named by somebody or arrived as
   * blocks.
   */
  const shape = presetBlocks(card);
  const wanted = shape ? sceneHeight(shape, "stack", "tight", SCENE_WIDTH) : 0;
  const fitFor = (available: number) =>
    wanted > 0 ? Math.max(MIN_FIT, Math.min(1, available / s / wanted)) : 1;
  /** How far this card would have to shrink to sit in a room of this height. */
  const fitsIn = (available: number) =>
    (card.kind === "scene"
      ? fitScene(card.blocks, {
        layout: card.layout, gap: card.gap, room: available / s,
      }).fit
      : fitFor(available));
  const bodyShares = fitsIn(bodyRoom(room - speakerTakes)) > MIN_FIT;
  /*
   * How far down a card written into the shot has to start.
   *
   * Nothing else stops it landing on the face. A banded card has a speaker
   * band above it and a full-frame one has no face to miss; this one is drawn
   * over the person and would happily print a sentence across their mouth.
   * The measured eye line plus a chin's worth below it is the floor. Generous
   * here costs the card the frame: between a high face and a tall caption
   * style there is little clear frame to begin with, and the fallback fires
   * on cards that would have fitted.
   */
  const HEAD_BELOW_EYES = 0.13;
  const faceFloor = ((faceY ?? 0.4) + HEAD_BELOW_EYES) * height;
  const overRoom = Math.min(
    bodyRoom(room),
    Math.max(0, height - faceFloor - (captionZone(style) + GAP.section) * s),
  );
  /*
   * Written into the shot rather than laid over it, when the shot has the room.
   *
   * The speaker is never drawn for one of these: the composition is an overlay
   * composited onto the clip, so leaving the panel transparent is what shows
   * the person. Drawing the band as well would put the same face on screen
   * twice, once cropped and once not.
   *
   * It falls back to the panel rather than squeezing. Between a close-up's
   * chin and a tall caption there can be sixty pixels of clear frame, and a
   * card drawn at the floor of the fit in sixty pixels is not a quieter
   * treatment, it is an unreadable one. A panel always has room because it
   * makes its own.
   */
  const over = card.place === "over" && fitsIn(overRoom) > MIN_FIT;
  const withSpeaker = !over
    && card.speaker !== null
    && Boolean(videoSrc)
    && room >= headNeeds
    && bodyShares
    && (!SHOWS_A_FILE.has(card.kind)
      || mediaBand(card.kind, bodyRoom(room - speakerTakes), s, hasCaption)
         >= MEDIA_MIN * s);
  /*
   * How far the type has to come down to sit inside the band.
   *
   * The type shrinks and the width is kept, which is the same move a scene
   * makes: a card drawn narrower as well as smaller reads as a card that
   * failed to lay out, where one drawn smaller at the same measure reads as a
   * card with a lot to say. The estimate it comes from over-states height on
   * purpose, so the result is a card with a little room to spare rather than
   * one resting on the caption.
   */
  const bodyAvailable = over
    ? overRoom
    : bodyRoom(withSpeaker ? room - speakerTakes : room);
  const bodyFit = fitFor(bodyAvailable);
  /*
   * A file takes the whole frame when it was asked to, or when a band would
   * be too short to be worth one.
   *
   * Asked, because a cutaway is an edit rather than a consequence: a card
   * that keeps its picture in a band on a surface is a legitimate thing to
   * want, and inferring the cutaway from a dropped speaker took that choice
   * away. The fallback stays, because the alternative to a band nobody can
   * read is not a smaller band.
   *
   * Never with a speaker still up. A cutaway that leaves the person on screen
   * is a smaller picture with extra steps.
   */
  const bandWouldBe = SHOWS_A_FILE.has(card.kind)
    ? mediaBand(card.kind, bodyRoom(withSpeaker ? room - speakerTakes : room), s, hasCaption)
    : 0;
  const bleeds = SHOWS_A_FILE.has(card.kind)
    && !withSpeaker
    && (("bleed" in card && card.bleed === true) || bandWouldBe < MEDIA_MIN * s);

  return (
    <div
      style={{
        position: "absolute",
        inset: 0,
        backgroundColor: over ? "transparent" : (card.background ?? colours.surface),
        opacity,
        display: "flex",
        flexDirection: "column",
        // The captions are not part of this stack; they are drawn over it.
        // Reserving their band as padding is what keeps a card's last line
        // from ending up behind the pill.
        paddingBottom: bleeds ? 0 : captionZone(style) * s,
      }}
    >
      {over && (
        /*
         * What a panel was doing for free.
         *
         * A card written into the shot has to be readable over whatever the
         * shot happens to be, and a kitchen window behind a guest is white.
         * A wash rather than a slab: heavy enough at the bottom that ink and
         * captions hold, gone by the time it reaches the face, so the thing
         * the overlay was for is still the shot.
         */
        <div
          style={{
            position: "absolute", left: 0, right: 0, bottom: 0, height: "62%",
            background: `linear-gradient(to bottom, transparent, `
              + `color-mix(in oklab, ${colours.surface} 78%, transparent))`,
          }}
        />
      )}
      {withSpeaker && videoSrc && (
        <div
          style={{
            flex: 1, minHeight: SPEAKER_MIN * s, overflow: "hidden",
            position: "relative",
          }}
        >
          <SpeakerBand src={videoSrc} startFrom={startFrom} faceY={faceY} />
          {/*
            * Where the footage stops being footage.
            *
            * Without this the two halves meet on a hard line, which on a dark
            * show hides in the dark bottom of the shot and on a light one cuts
            * a shoulder in half with a white slab. Neither is a decision
            * anybody made. A short ramp into the surface reads as the card
            * being laid over the shot on every brand and every room.
            */}
          <div
            style={{
              position: "absolute", left: 0, right: 0, bottom: 0,
              height: SEAM * s,
              background:
                `linear-gradient(to bottom, transparent, ${colours.surface})`,
            }}
          />
        </div>
      )}
      <div
        style={{
          /*
           * Wider on the right than on the left, because the app draws its
           * like, comment and share rail up that side and a bar measured
           * under it is a bar nobody reads. Footage still runs to both edges:
           * an inset would draw it as a picture pasted on a card, where the
           * point is a second half of the frame, and footage losing its outer
           * ninth to a rail costs nothing a number would not.
           */
          padding: bleeds || card.kind === "video"
            ? `${bleeds ? 0 : GAP.section * s}px 0 0`
            : `${GAP.section * s}px ${SAFE.right * s}px 0 ${SAFE.left * s}px`,
          display: "flex",
          flexDirection: "column",
          /*
           * Under the speaker when there is one, centred when there is not.
           *
           * A card that has the whole frame and puts its content against the
           * captions leaves two thirds of the frame empty above it, which
           * reads as a card that failed to load. With a speaker the content
           * belongs low, because the thing above it is a person and the gap
           * between them is what makes the pair read as one shot.
           *
           * A file taking the whole frame does neither: it is the frame, so it
           * stretches rather than sitting anywhere in it.
           */
          ...(bleeds
            ? { position: "absolute", inset: 0 }
            : {
              marginTop: "auto",
              // Written into the shot, a card belongs low for the same reason a
              // banded one does: the person is above it. Centred, it reads as a
              // slide someone forgot to take down.
              marginBottom: withSpeaker || over ? undefined : "auto",
            }),
        }}
      >
        <CardBody
          card={card}
          scale={s * bodyFit}
          brand={colours}
          room={bodyAvailable}
          bleed={bleeds}
          topInset={topInset}
        />
      </div>
    </div>
  );
};
