export function maxClipSeconds(format?: string): number {
  return format === "horizontal" ? 300 : 180;
}

/** Returns an error message, or null when the range is renderable. */
export function validateClipRange(
  start: unknown,
  end: unknown,
  format?: string,
): string | null {
  if (
    typeof start !== "number" ||
    !Number.isFinite(start) ||
    typeof end !== "number" ||
    !Number.isFinite(end)
  ) {
    return "start_second and end_second must be numbers";
  }
  if (start < 0) return "start_second must be >= 0";
  if (end <= start) return "end_second must be greater than start_second";
  const maxDur = maxClipSeconds(format);
  if (end - start > maxDur) {
    return `Clip too long (${Math.round(end - start)}s). Max ${maxDur} seconds.`;
  }
  return null;
}

// Suggestions aren't bound to a format yet, so allow up to the longest
// renderable duration plus trim headroom.
const MAX_SUGGESTION_SECONDS = 600;

export function validateSuggestionRange(start: unknown, end: unknown): string | null {
  if (
    typeof start !== "number" ||
    !Number.isFinite(start) ||
    typeof end !== "number" ||
    !Number.isFinite(end)
  ) {
    return "start_second and end_second must be numbers";
  }
  if (start < 0) return "start_second must be >= 0";
  if (end <= start) return "end_second must be greater than start_second";
  if (end - start > MAX_SUGGESTION_SECONDS) {
    return `Suggested range too long (${Math.round(end - start)}s). Max ${MAX_SUGGESTION_SECONDS} seconds.`;
  }
  return null;
}
