export interface Word {
  word: string;
  start: number;
  end: number;
  confidence?: number;
  speaker?: string;
  /**
   * Worth reading even by somebody scrolling past with the sound off: a
   * number, a name, the word the sentence was built to land on.
   *
   * It rides on the word rather than arriving as a list of indices, because
   * every caption style chunks and splits the array before drawing it and an
   * index into the original would be wrong by then.
   */
  emphasis?: boolean;
}

export interface CaptionStyle {
  name: "hormozi" | "karaoke" | "subtle" | "branded" | "outline";
  fontSize: number;
  fontFamily: string;
  color: string;
  activeColor: string;
  /**
   * What an emphasised word is coloured. Falls back to activeColor, so a
   * style that never sets one still marks emphasis rather than dropping it.
   */
  emphasisColor?: string;
  uppercase: boolean;
  wordsPerChunk: number;
  marginBottom: number;
  /**
   * What a pill behind the words is filled with.
   *
   * Black until a show says otherwise, because a caption is read over footage
   * and black is the one fill that works over all of it. A show that has told
   * us what its text sits on gets that instead, so the pill over a card is the
   * card rather than a hole cut in it.
   */
  background?: string;
  /**
   * What the words are outlined in, when they are outlined rather than boxed.
   *
   * A pill guarantees legibility by covering the shot. An outline guarantees
   * it by making every letter carry its own contrast, which leaves the frame
   * whole. Drawn behind the fill, so a heavy stroke thickens the letter rather
   * than eating it.
   */
  stroke?: string;
}

export type CaptionPosition = "auto" | "upper" | "center" | "lower";
export type LogoPosition =
  | "top-left"
  | "top-center"
  | "top-right"
  | "bottom-left"
  | "bottom-center"
  | "bottom-right";

/**
 * The logo's box, in unscaled units.
 *
 * Watermark draws it and the branded captions keep clear of it, and those are
 * two files. Shared here so a logo that moves cannot leave the caption margin
 * guarding the place it used to be.
 */
export const LOGO_INSET = 180;
export const LOGO_EDGE = 108;
export const LOGO_WIDTH = 255;
export const LOGO_HEIGHT = 126;
export const LOGO_CAPTION_GAP = 24;

/**
 * The part of the frame the app it plays in has already spent.
 *
 * A vertical clip is never watched on its own. YouTube draws a title, a handle
 * and a link chip across the bottom of it, an action rail up the right, and a
 * progress bar under both. TikTok and Reels draw the same furniture in the
 * same two places. None of it is ours and all of it is opaque, so anything we
 * put there is not a card that came out badly, it is a card nobody ever saw.
 *
 * Measured off a Shorts screenshot rather than guessed. On a 9:19.5 phone a
 * viewer's chrome starts 1498 down a 1920 frame and the rail runs from x 817
 * to the edge. The bottom leaves 38 clear of that line, which is as low as
 * the words go before the app starts eating them.
 */
export const SAFE = {
  top: 200,
  bottom: 460,
  left: 120,
  right: 280,
} as const;

/**
 * The same reservation for a frame nobody scrolls past.
 *
 * A landscape clip plays in a player, not in a feed. There is no handle, no
 * title and no action rail drawn over it; there is a scrubber that appears on
 * hover and the edges of a screen it might be letterboxed on. Reserving the
 * phone's 460 here would spend a quarter of the frame guarding against
 * furniture that is not there.
 */
export const SAFE_WIDE = {
  top: 90,
  bottom: 150,
  left: 120,
  right: 120,
} as const;

/**
 * Which reservation this frame is owed, read off its own shape.
 *
 * Portrait is the feed and gets the full chrome. Anything square or wider is
 * a player and gets the margin a broadcast would use. The composition knows
 * its size, so nothing has to be told which platform it is for.
 */
export const safeFor = (width: number, height: number) =>
  (width < height ? SAFE : SAFE_WIDE);

/**
 * How much of the width a phone throws away.
 *
 * A 9:16 file on a 9:19.5 screen is filled to the height and cropped at the
 * sides, so the outer ninth of the frame is off-screen before any chrome is
 * drawn. `SAFE.left` and `SAFE.right` already cover it; this is the number
 * they were derived from, kept so the derivation is checkable.
 */
export const DEVICE_SIDE_CROP = 96;

export interface CaptionProps {
  words: Word[];
  style: CaptionStyle;
  fps: number;
  durationInFrames: number;
  videoSrc: string;
  logoSrc?: string;
  faceY?: number | null; // normalized 0-1 (0=top, 1=bottom)
}

/**
 * The caption stack, in falling-back order.
 *
 * DM Sans draws Latin and nothing else, so the Noto families behind it are
 * what a Georgian or Russian show is actually rendered in. The browser picks
 * per glyph, so a Latin clip never leaves DM Sans.
 */
export const FONT = "'DM Sans', 'Noto Sans', 'Noto Sans Georgian', sans-serif";

/** A show's own font, ahead of the stack that covers what it cannot draw. */
export const fontStack = (family?: string | null): string =>
  family ? `'${family.replace(/'/g, "")}', ${FONT}` : FONT;

// Caption geometry (font sizes, margins, insets) is authored for a 1920-tall
// vertical canvas. Multiply pixel values by this factor so a shorter canvas
// (16:9 = 1080 tall, 1:1 = 1080 tall) gets a proportional lower-third instead
// of vertical-tuned captions floating mid-frame. Vertical → factor 1.0.
export const REFERENCE_HEIGHT = 1920;
export const captionScale = (height: number): number => height / REFERENCE_HEIGHT;

/**
 * How many lines of caption everything else gets out of the way of.
 *
 * Reserving for the tallest caption costs a card some height on every clip
 * and costs it nothing on the clip where it matters.
 */
export const CAPTION_LINES = 3;

/**
 * The band the captions own, in reference units.
 *
 * Read off the style rather than fixed, because a style's size and margin are
 * the only things that decide it. Lives here because four things need to
 * clear it: the card, the name card, the logo and the chip, and a number
 * copied into four files is a number that drifts in three of them.
 */
export const captionZone = (style: CaptionStyle) =>
  style.marginBottom + style.fontSize * CAPTION_LINES * 1.2 + 36;

export const STYLES: Record<string, CaptionStyle> = {
  hormozi: {
    name: "hormozi",
    fontSize: 90,
    fontFamily: FONT,
    color: "#FFFFFF",
    activeColor: "#FFFF00",
    emphasisColor: "#3B9CFF",
    uppercase: true,
    wordsPerChunk: 3,
    marginBottom: 400,
  },
  karaoke: {
    name: "karaoke",
    fontSize: 80,
    fontFamily: FONT,
    color: "rgba(255,255,255,0.4)",
    activeColor: "#FFFFFF",
    emphasisColor: "#3B9CFF",
    uppercase: false,
    wordsPerChunk: 5,
    marginBottom: 400,
  },
  subtle: {
    name: "subtle",
    fontSize: 64,
    fontFamily: FONT,
    color: "#FFFFFF",
    activeColor: "#FFFFFF",
    emphasisColor: "#7FD1FF",
    uppercase: false,
    wordsPerChunk: 6,
    marginBottom: 200,
  },
  branded: {
    name: "branded",
    fontSize: 100,
    fontFamily: FONT,
    color: "#FFFFFF",
    activeColor: "#FFFFFF",
    emphasisColor: "#3B9CFF",
    uppercase: false,
    wordsPerChunk: 3,
    marginBottom: 420,
  },
  /**
   * A whole sentence at a time, outlined, over an uncovered frame.
   *
   * The one every serious interview channel converges on, because it is the
   * only caption that stays readable without taking anything from the shot.
   * More words per chunk than the rest: the point is a line somebody reads,
   * not a word that lands.
   */
  outline: {
    name: "outline",
    fontSize: 96,
    fontFamily: FONT,
    color: "#FFFFFF",
    activeColor: "#FFFFFF",
    emphasisColor: "#3B9CFF",
    stroke: "#000000",
    uppercase: false,
    wordsPerChunk: 7,
    marginBottom: 460,
  },
};

/**
 * The caption styles, in the show's own colours.
 *
 * A style ships with a palette because it has to look like something before
 * anybody has picked anything. Left at that, every clip carried two accents:
 * the show's on the card and the style's in the captions, a yellow sweep under
 * a green figure, and a colour that means two things means neither.
 *
 * Each style keeps its character and loses its palette. Hormozi is the loud
 * one, so the word being spoken takes the accent; the quieter three set the
 * spoken word in ink and spend the accent only on a word worth reading with
 * the sound off. The pill becomes the show's surface, which is what makes a
 * caption over a card read as part of it.
 */
export const brandCaptions = (
  style: CaptionStyle,
  brand?: { accent: string; ink: string; surface: string } | null,
): CaptionStyle => {
  if (!brand) return style;
  return {
    ...style,
    color: style.name === "karaoke"
      ? `color-mix(in oklab, ${brand.ink} 40%, transparent)`
      : brand.ink,
    activeColor: style.name === "hormozi" ? brand.accent : brand.ink,
    emphasisColor: brand.accent,
    background: `color-mix(in oklab, ${brand.surface} 85%, transparent)`,
    // Opaque, unlike the pill: a stroke at 85% is a grey halo, not an edge.
    ...(style.stroke ? { stroke: brand.surface } : {}),
  };
};
