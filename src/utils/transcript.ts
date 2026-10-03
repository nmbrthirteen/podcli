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

/**
 * The suggestion's grounding text for the clip whose range best matches
 * [start, end] — the payoff, the question it answers, and its verbatim
 * opening line. Thumbnail copy is written from this, not from the title
 * alone, so it matches what the clip actually says.
 */
export function findGroundingText(
  suggestions:
    | Array<{
        start_second: number;
        end_second: number;
        payoff?: string;
        context_line?: string;
        preview_text?: string;
      }>
    | undefined
    | null,
  start: number,
  end: number,
): { payoff?: string; context_line?: string; preview_text?: string } | undefined {
  if (!suggestions || suggestions.length === 0) return undefined;
  const match = suggestions.find(
    (s) => Math.abs(s.start_second - start) <= 2 && Math.abs(s.end_second - end) <= 2,
  );
  if (!match) return undefined;
  return { payoff: match.payoff, context_line: match.context_line, preview_text: match.preview_text };
}

/**
 * Keep a clip's keep_segments consistent after its start/end range is edited.
 * Without this, a stale segments array (scoped to the old range) overrides
 * the new start_second/end_second at render time, since the generator
 * derives the actual cut points from keep_segments when present.
 *
 * Segments outside the new range are dropped, segments straddling a new
 * boundary are clamped to it, and a single segment that ends up spanning
 * the whole new range is cleared so the generator is free to auto-tighten
 * it instead of carrying forward an edit that no longer means anything.
 */
export function reconcileSegmentsForRange(
  segments: Array<{ start: number; end: number }> | undefined,
  nextStart: number,
  nextEnd: number,
): Array<{ start: number; end: number }> | undefined {
  if (!segments?.length) return segments;
  const clamped = segments
    .map((s) => ({ start: Math.max(s.start, nextStart), end: Math.min(s.end, nextEnd) }))
    .filter((s) => s.end > s.start);
  if (clamped.length === 0) return undefined;
  if (
    clamped.length === 1 &&
    clamped[0].start <= nextStart + 0.01 &&
    clamped[0].end >= nextEnd - 0.01
  ) {
    return undefined;
  }
  return clamped;
}

/** The suggestion whose range matches start/end within half a second. */
export function findSuggestionForRange<T extends { start_second: number; end_second: number }>(
  suggestions: T[] | undefined | null,
  start: number,
  end: number,
): T | undefined {
  return suggestions?.find(
    (s) => Math.abs(s.start_second - start) < 0.5 && Math.abs(s.end_second - end) < 0.5,
  );
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
  return findSuggestionForRange(suggestions, start, end)?.segments;
}
