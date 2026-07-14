export interface Word {
  word: string;
  start: number;
  end: number;
  confidence?: number;
  speaker?: string;
}

export interface CaptionStyle {
  name: "hormozi" | "karaoke" | "subtle" | "branded";
  fontSize: number;
  fontFamily: string;
  color: string;
  activeColor: string;
  uppercase: boolean;
  wordsPerChunk: number;
  marginBottom: number;
}

export interface CaptionProps {
  words: Word[];
  style: CaptionStyle;
  fps: number;
  durationInFrames: number;
  videoSrc: string;
  logoSrc?: string;
  faceY?: number | null; // normalized 0-1 (0=top, 1=bottom)
}

const FONT = "'DM Sans', sans-serif";

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
    uppercase: false,
    wordsPerChunk: 3,
    marginBottom: 420,
  },
};
