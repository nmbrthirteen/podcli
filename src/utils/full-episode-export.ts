import { basename, extname } from "path";

export const FULL_EPISODE_CAPTION_STYLES = [
  "branded",
  "hormozi",
  "karaoke",
  "subtle",
] as const;

export type FullEpisodeCaptionStyle = (typeof FULL_EPISODE_CAPTION_STYLES)[number];

export interface FullEpisodeProgress {
  percent: number;
  message: string;
}

export function fullEpisodeOutputStem(videoPath: string): string {
  const filename = basename(videoPath, extname(videoPath));
  const safe = filename
    .trim()
    .replace(/[^a-zA-Z0-9._-]/g, "-")
    .replace(/^-+|-+$/g, "");
  return `${safe || "episode"}_full_captioned`;
}

export function parseFullEpisodeProgress(line: string): FullEpisodeProgress | null {
  const prefix = "PODCLI_PROGRESS=";
  const marker = line.indexOf(prefix);
  if (marker < 0) return null;
  try {
    const parsed = JSON.parse(line.slice(marker + prefix.length));
    const percent = Number(parsed.percent);
    if (!Number.isFinite(percent) || typeof parsed.message !== "string") return null;
    return {
      percent: Math.max(0, Math.min(100, Math.round(percent))),
      message: parsed.message,
    };
  } catch {
    return null;
  }
}
