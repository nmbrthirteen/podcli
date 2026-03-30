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
  position: "bottom" | "center" | "lower-third";
  marginBottom: number;
}

export interface CaptionProps {
  words: Word[];
  style: CaptionStyle;
  fps: number;
  durationInFrames: number;
  videoSrc: string;
  logoSrc?: string;
}

const FONT = "'DM Sans', sans-serif";

export const STYLES: Record<string, CaptionStyle> = {
  hormozi: {
    name: "hormozi",
    fontSize: 90,
    fontFamily: FONT,
    color: "#FFFFFF",
    activeColor: "#FFFF00",
    uppercase: true,
    wordsPerChunk: 3,
    position: "bottom",
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
    position: "bottom",
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
    position: "bottom",
    marginBottom: 200,
  },
  branded: {
    name: "branded",
    fontSize: 200,
    fontFamily: FONT,
    color: "#FFFFFF",
    activeColor: "#FFFFFF",
    uppercase: false,
    wordsPerChunk: 5,
    position: "lower-third",
    marginBottom: 850,
  },
};
