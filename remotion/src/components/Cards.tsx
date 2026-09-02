import React from "react";
import {
  getRemotionEnvironment, Img, OffthreadVideo, Video, staticFile,
  useCurrentFrame, useVideoConfig,
} from "remotion";
import { captionScale, FONT } from "../types";
import type { CaptionStyle } from "../types";
import { MOTION, motionAt } from "../motion";
import { cardAt } from "../cards";
import type { Card } from "../cards";

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

/**
 * The show's colours, or ours when it has not set any.
 *
 * Three values rather than a palette: the one colour that means "this is the
 * thing", what text is set in, and what it is set on. Everything else on a
 * card is one of those three at a lower opacity, which is what keeps a card
 * looking like the show rather than like a theme.
 */
export type Brand = { accent: string; ink: string; surface: string };

export const DEFAULT_BRAND: Brand = {
  accent: "#4C9DF5",
  ink: "#FFFFFF",
  surface: "#0A0D14",
};

/** Text that is not the point, and marks that are not the subject. */
const muted = (ink: string) => `color-mix(in oklab, ${ink} 62%, transparent)`;
const context = (ink: string) => `color-mix(in oklab, ${ink} 22%, transparent)`;

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

/** A meter's unfilled remainder: the fill's own hue, several steps lighter. */
const track = (fill: string) =>
  `color-mix(in oklab, ${fill} 22%, transparent)`;

/** Authored for the 1920-tall canvas, like every other measurement here. */
const TYPE = { label: 34, body: 46, item: 58, quote: 68, lead: 84, figure: 168 };

/**
 * How much of the frame a video card's footage takes.
 *
 * The arithmetic it has to fit: a 1920 canvas, roughly 470 reserved for the
 * captions, 76 of gap, and the speaker's own floor of 820 above it. What is
 * left is this. Asking for the literal half of the frame would push the band
 * under that floor, and the card would answer by dropping the speaker
 * altogether — the opposite of what a split screen is for.
 *
 * It shrinks before the speaker does, because a caption style with tall words
 * has to take its room from somewhere and a face cut in half is worse than
 * footage cropped a little tighter.
 */
const VIDEO_BAND = 540;

/**
 * The least of the frame the speaker keeps.
 *
 * The band is whatever the card does not need rather than a fixed height: a
 * two-line quote and a bare figure want different amounts, and pinning both
 * ends meant a tall card overflowed upward across the speaker's chin. This
 * floor stops a very long card from squeezing the face out altogether.
 */
const SPEAKER_MIN = 820;

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

/**
 * Where captions sit while a card is up.
 *
 * Their usual margin exists to clear a speaker's chin and hands, and there is
 * no chin down there behind a card. Dropping them toward the bottom edge is
 * what buys the speaker a band tall enough to hold a whole head.
 */
export const CARD_CAPTION_MARGIN = 180;

/**
 * The room the captions need, measured rather than assumed.
 *
 * A fixed number worked until a two-line Georgian caption grew taller than it
 * and the card's last line rendered behind the pill. The caption's own style
 * knows where it sits and how big it is, so the reserved band is read off that:
 * its margin, two lines of it, and a gap.
 */
const captionZone = (style: CaptionStyle) =>
  style.marginBottom + style.fontSize * CAPTION_LINES * 1.2 + GAP.group;

/**
 * How many lines of caption a card gets out of the way of.
 *
 * Two was the common case and wrong at the worst one: a chunk that wrapped to
 * three lines covered the card's last row, which is the row a comparison keeps
 * its second bar in. Reserving for the tallest caption costs a card some
 * height on every clip and costs it nothing on the clip where it matters.
 */
const CAPTION_LINES = 3;

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

const CardBody: React.FC<{ card: Card; scale: number; brand: Brand }> = ({
  card, scale: s, brand,
}) => {
  // Read before any of the branches below, so a card kind that never uses it
  // does not change the order the hooks run in.
  const { fps } = useVideoConfig();
  const accent = card.accent ?? brand.accent;
  const INK = brand.ink;
  const MUTED = muted(brand.ink);
  const CONTEXT = context(brand.ink);

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
          {card.lead}
          {card.emphasis && (
            // The payload word carries the card's one accent. Italic as well
            // would be two ways of saying the same thing.
            <span style={{ color: accent }}> {card.emphasis}</span>
          )}
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

  if (card.kind === "image") {
    return (
      <>
        <Img
          src={card.src.startsWith("http") ? card.src : staticFile(card.src)}
          style={{
            width: "100%",
            maxHeight: 520 * s,
            objectFit: card.fit === "fill" ? "cover" : "contain",
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
            height: VIDEO_BAND * s,
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
              padding: `0 ${96 * s}px`,
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
  "image", "video",
]);

export const Cards: React.FC<{
  cards: Card[];
  videoSrc?: string;
  startFrom?: number;
  style: CaptionStyle;
  faceY?: number | null;
  faceH?: number | null;
  brand?: Brand | null;
}> = ({ cards, videoSrc, startFrom = 0, style, faceY, faceH, brand }) => {
  const colours = { ...DEFAULT_BRAND, ...(brand ?? {}) };
  const frame = useCurrentFrame();
  const { fps, height } = useVideoConfig();
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
  const usable = cards.filter((c) => (
    KNOWN_KINDS.has(c.kind)
    && (c.kind !== "image" && c.kind !== "video" ? true : Boolean(c.src))
  ));
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
  const withSpeaker = card.speaker !== null
    && Boolean(videoSrc)
    && room >= headNeeds;

  return (
    <div
      style={{
        position: "absolute",
        inset: 0,
        backgroundColor: card.background ?? colours.surface,
        opacity,
        display: "flex",
        flexDirection: "column",
        // The captions are not part of this stack; they are drawn over it.
        // Reserving their band as padding is what keeps a card's last line
        // from ending up behind the pill.
        paddingBottom: captionZone(style) * s,
      }}
    >
      {withSpeaker && videoSrc && (
        <div style={{ flex: 1, minHeight: SPEAKER_MIN * s, overflow: "hidden" }}>
          <SpeakerBand src={videoSrc} startFrom={startFrom} faceY={faceY} />
        </div>
      )}
      <div
        style={{
          // Footage runs to both edges. An inset would draw it as a picture
          // pasted on a card, where the point is a second half of the frame.
          padding: `${GAP.section * s}px ${(card.kind === "video" ? 0 : 96) * s}px 0`,
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
           */
          marginTop: "auto",
          marginBottom: withSpeaker ? undefined : "auto",
        }}
      >
        <CardBody card={card} scale={s} brand={colours} />
      </div>
    </div>
  );
};
