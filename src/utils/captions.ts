import type { WordTimestamp } from "../models/index.js";

export interface CaptionCue {
  start: number;
  end: number;
  text: string;
}

export interface CaptionTrackRef {
  ext: string;
  url: string;
}

export interface SelectedCaptionTrack {
  lang: string;
  kind: "manual" | "auto";
  ext: string;
  url: string;
}

/**
 * Pick the caption track worth mining: a manual (human-written) track over
 * an auto-generated one, and within either, the track's own original-language
 * variant (yt-dlp exposes it as `<lang>-orig`, e.g. "ka-orig") over a plain
 * language code, which is usually YouTube's auto-translation of some other
 * track and drifts from what was actually said.
 */
export function selectBestCaptionTrack(
  subtitles: Record<string, CaptionTrackRef[]> | undefined | null,
  automaticCaptions: Record<string, CaptionTrackRef[]> | undefined | null,
): SelectedCaptionTrack | null {
  const pick = (
    dict: Record<string, CaptionTrackRef[]> | undefined | null,
    kind: "manual" | "auto",
  ): SelectedCaptionTrack | null => {
    if (!dict) return null;
    const langs = Object.keys(dict);
    if (langs.length === 0) return null;
    const lang = langs.find((l) => l.endsWith("-orig")) ?? langs[0];
    const tracks = dict[lang];
    if (!tracks?.length) return null;
    const vtt = tracks.find((t) => t.ext === "vtt") ?? tracks[0];
    return { lang, kind, ext: vtt.ext, url: vtt.url };
  };
  return pick(subtitles, "manual") ?? pick(automaticCaptions, "auto");
}

/**
 * Parse WebVTT cue text into {start, end, text}. Strips inline tags
 * (<c>, <00:00:01.000>, voice spans) that auto-captions use for
 * word-by-word highlighting — podcli derives its own word timing from the
 * cue span instead, so these would only be noise in the text.
 */
export function parseVtt(vtt: string): CaptionCue[] {
  const timeToSeconds = (t: string): number => {
    const m = t.trim().match(/(?:(\d+):)?(\d+):(\d+)\.(\d+)/);
    if (!m) return 0;
    const [, h, mm, ss, ms] = m;
    return (Number(h) || 0) * 3600 + Number(mm) * 60 + Number(ss) + Number(ms) / 1000;
  };
  const stripTags = (s: string): string => s.replace(/<[^>]*>/g, "").trim();

  const blocks = vtt.replace(/\r\n/g, "\n").split(/\n\n+/);
  const cues: CaptionCue[] = [];
  for (const block of blocks) {
    const lines = block.split("\n").filter((l) => l.trim().length > 0);
    const timeLineIdx = lines.findIndex((l) => l.includes("-->"));
    if (timeLineIdx === -1) continue;
    const [startRaw, endRaw] = lines[timeLineIdx].split("-->");
    const start = timeToSeconds(startRaw);
    const end = timeToSeconds(endRaw.trim().split(/\s+/)[0] ?? endRaw);
    const text = stripTags(lines.slice(timeLineIdx + 1).join(" "));
    if (!text) continue;
    cues.push({ start, end, text });
  }
  return cues;
}

/**
 * One cue's words, evenly spaced across [cue.start, cue.end]. Each word's
 * end is the next word's start; the cue's own final word is capped at
 * cue.end rather than left open, since a caption gives no finer timing
 * than the cue span itself.
 */
function cueToWords(cue: CaptionCue): WordTimestamp[] {
  const words = cue.text.split(/\s+/).filter(Boolean);
  if (words.length === 0) return [];
  const step = (cue.end - cue.start) / words.length;
  return words.map((word, i) => ({
    word,
    start: cue.start + i * step,
    end: i === words.length - 1 ? cue.end : cue.start + (i + 1) * step,
    // Captions carry no ASR confidence score; 1 marks it as "given", not
    // "measured", so nothing downstream mistakes it for a low-confidence word.
    confidence: 1,
  }));
}

/**
 * Convert caption cues into podcli's word-level transcript format.
 *
 * YouTube auto-captions commonly repeat the same line across several
 * overlapping "rolling" cues (each one just appends a word or two to the
 * last); kept verbatim, that reads as the transcript stuttering through
 * the same sentence 3-4 times. Drop a cue whose text is a prefix of the
 * next one, keeping only the fullest version of each rolling line.
 */
export function cuesToWords(cues: CaptionCue[]): WordTimestamp[] {
  const deduped = cues.filter((cue, i) => {
    const next = cues[i + 1];
    return !(next && next.text.startsWith(cue.text));
  });
  return deduped.flatMap(cueToWords);
}
