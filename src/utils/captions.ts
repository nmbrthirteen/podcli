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
    // json3 carries a start time per word; vtt only per cue.
    const best = tracks.find((t) => t.ext === "json3") ?? tracks.find((t) => t.ext === "vtt") ?? tracks[0];
    return { lang, kind, ext: best.ext, url: best.url };
  };
  return pick(subtitles, "manual") ?? pick(automaticCaptions, "auto");
}

/**
 * Parse WebVTT cue text into {start, end, text}. Strips inline tags
 * (<c>, <00:00:01.000>, voice spans) that auto-captions use for
 * word-by-word highlighting. podcli derives its own word timing from the
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
  const words = captionTokens(cue.text);
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
 * YouTube auto-captions roll: each cue repeats the previous cue's last line
 * above the new one, and a ~10 ms cue between them repeats it once more.
 * Kept verbatim, every line appears three times. Drop the carry cues, then
 * strip from each cue the words it repeats from the end of the text so far.
 * Manual tracks have no such overlap and pass through unchanged.
 */
export function cuesToWords(cues: CaptionCue[]): WordTimestamp[] {
  const kept: CaptionCue[] = [];
  let tail: string[] = [];
  for (const cue of cues) {
    if (cue.end - cue.start < 0.05) continue;
    const words = captionTokens(cue.text);
    let overlap = Math.min(words.length, tail.length);
    while (overlap > 0 && tail.slice(-overlap).join(" ") !== words.slice(0, overlap).join(" ")) overlap--;
    const fresh = words.slice(overlap);
    tail = [...tail, ...fresh].slice(-64);
    if (fresh.length) kept.push({ ...cue, text: fresh.join(" ") });
  }
  return kept.flatMap(cueToWords);
}

interface Json3Event {
  tStartMs?: number;
  dDurationMs?: number;
  segs?: Array<{ utf8?: string; tOffsetMs?: number }>;
}

/**
 * Caption text as spoken words: HTML entities decoded and YouTube's ">>"
 * speaker-change marks dropped, since they are layout, not speech.
 */
export function captionTokens(text: string): string[] {
  return text
    .replace(/&gt;/g, ">")
    .replace(/&lt;/g, "<")
    .replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'")
    .replace(/&amp;/g, "&")
    .split(/\s+/)
    .filter((t) => t && !/^>+$/.test(t));
}

/**
 * Words and segments from YouTube's json3 caption format. Auto-captions give
 * each word its own offset, so timing comes from the source rather than being
 * spread evenly across a cue. Events can overlap (a speaker change opens a new
 * event before the old one closes), so words are ordered by start and each
 * ends where the next begins.
 */
export function parseJson3(raw: string): { words: WordTimestamp[]; segments: CaptionCue[] } {
  const events = (JSON.parse(raw) as { events?: Json3Event[] }).events ?? [];
  const words: WordTimestamp[] = [];
  const segments: CaptionCue[] = [];
  for (const ev of events) {
    const evStart = (ev.tStartMs ?? 0) / 1000;
    const evEnd = evStart + (ev.dDurationMs ?? 0) / 1000;
    const segs = (ev.segs ?? []).map((seg) => ({
      tokens: captionTokens(seg.utf8 ?? ""),
      start: evStart + (seg.tOffsetMs ?? 0) / 1000,
    }));
    const evWords: WordTimestamp[] = [];
    segs.forEach((seg, i) => {
      const segEnd = Math.max(seg.start, Math.min(segs[i + 1]?.start ?? evEnd, evEnd));
      const step = (segEnd - seg.start) / Math.max(seg.tokens.length, 1);
      seg.tokens.forEach((word, k) =>
        evWords.push({ word, start: seg.start + k * step, end: seg.start + (k + 1) * step, confidence: 1 }),
      );
    });
    if (!evWords.length) continue;
    words.push(...evWords);
    segments.push({ start: evStart, end: evEnd, text: evWords.map((w) => w.word).join(" ") });
  }
  words.sort((a, b) => a.start - b.start);
  words.forEach((w, i) => {
    const next = words[i + 1];
    if (next && w.end > next.start) w.end = next.start;
  });
  segments.sort((a, b) => a.start - b.start);
  return { words, segments };
}
