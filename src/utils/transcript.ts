import type { WordTimestamp } from "../models/index.js";

/**
 * Plain-text words spoken within [start, end], for persisting on a clip's
 * history entry. The source transcript is overwritten each session, so this
 * is the only durable record of what the clip actually said.
 */
export function sliceTranscript(
  words: WordTimestamp[] | undefined | null,
  start: number,
  end: number,
): string | undefined {
  if (!words || words.length === 0) return undefined;
  const text = words
    .filter((w) => w.start >= start && w.start < end)
    .map((w) => w.word)
    .join(" ")
    .replace(/\s+/g, " ")
    .trim();
  return text || undefined;
}

/** Word objects spoken within [start, end] — used to re-burn captions on re-render. */
export function sliceWords(
  words: WordTimestamp[] | undefined | null,
  start: number,
  end: number,
): WordTimestamp[] {
  if (!words) return [];
  return words.filter((w) => w.start >= start && w.start < end);
}

/** content_type of the suggestion whose range best matches [start, end]. */
export function findContentType(
  suggestions: Array<{ start_second: number; end_second: number; content_type?: string }> | undefined | null,
  start: number,
  end: number,
): string | undefined {
  if (!suggestions || suggestions.length === 0) return undefined;
  const match = suggestions.find(
    (s) => Math.abs(s.start_second - start) <= 2 && Math.abs(s.end_second - end) <= 2,
  );
  return match?.content_type;
}

export function findSuggestionSegments(
  suggestions: Array<{
    start_second: number;
    end_second: number;
    segments?: Array<{ start: number; end: number }>;
  }> | undefined | null,
  start: number,
  end: number,
): Array<{ start: number; end: number }> | undefined {
  if (!suggestions?.length) return undefined;
  const match = suggestions.find(
    (s) => Math.abs(s.start_second - start) < 0.5 && Math.abs(s.end_second - end) < 0.5,
  );
  return match?.segments;
}
