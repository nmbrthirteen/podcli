import { createHash } from "crypto";
import type { SuggestedClip, WordTimestamp } from "../models/index.js";

/**
 * Fingerprints everything about a clip that changes what actually renders:
 * its range, its editorial segments and hook (if any), caption style, title
 * text, and the transcript words that fall inside its range. Selecting a
 * clip for export stores this; if the clip is edited afterward the stored
 * hash no longer matches, which is the signal that the export no longer
 * describes what was approved.
 */
export function computeSelectionHash(
  clip: Pick<
    SuggestedClip,
    "start_second" | "end_second" | "segments" | "hook" | "suggested_caption_style" | "title"
  >,
  transcriptWords: WordTimestamp[] | undefined | null,
): string {
  const wordsInRange = (transcriptWords || [])
    .filter((w) => w.start >= clip.start_second && w.start < clip.end_second)
    .map((w) => `${w.word}:${w.start}:${w.end}`);

  const payload = JSON.stringify({
    start: clip.start_second,
    end: clip.end_second,
    segments: clip.segments ?? null,
    hook: clip.hook ?? null,
    captionStyle: clip.suggested_caption_style ?? null,
    title: clip.title ?? null,
    words: wordsInRange,
  });

  return createHash("sha1").update(payload).digest("hex");
}
