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
  name: "hormozi" | "karaoke" | "subtle" | "branded";
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
};
