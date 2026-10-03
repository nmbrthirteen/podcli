/**
 * Opening hook: a short spoken passage from inside a clip, played first.
 * backend/services/opening_hook.py renders it; this mirrors its rules so the
 * MCP tools and the studio reject the same hooks and show the same order.
 *
 * No imports, so the studio client can bundle it as is.
 */

export type HookMode = "repeat" | "move";

export interface ClipHook {
  start: number;
  end: number;
  mode: HookMode;
}

export interface TimeRange {
  start: number;
  end: number;
}

export const MIN_HOOK_SECONDS = 1;
export const MAX_HOOK_SECONDS = 15;

// Matches _EDGE_TOLERANCE and _MIN_REMNANT_SECONDS in opening_hook.py.
const EDGE_TOLERANCE = 0.01;
const MIN_REMNANT_SECONDS = 0.1;

function bodyRanges(start: number, end: number, segments?: TimeRange[] | null): TimeRange[] {
  const body = (segments ?? []).filter((s) => s.end > s.start);
  return body.length ? body : [{ start, end }];
}

function coveringSpans(ranges: TimeRange[]): TimeRange[] {
  const spans: TimeRange[] = [];
  for (const r of [...ranges].sort((a, b) => a.start - b.start)) {
    const last = spans[spans.length - 1];
    if (last && r.start <= last.end + EDGE_TOLERANCE) last.end = Math.max(last.end, r.end);
    else spans.push({ ...r });
  }
  return spans;
}

/** Returns an error message, or null when the hook is renderable (or absent). */
export function validateHook(
  hook: unknown,
  start: number,
  end: number,
  segments?: TimeRange[] | null,
): string | null {
  if (hook === undefined || hook === null) return null;
  if (typeof hook !== "object") return "hook must be an object with start, end and mode";
  const { start: hs, end: he, mode } = hook as Record<string, unknown>;
  if (typeof hs !== "number" || !Number.isFinite(hs) || typeof he !== "number" || !Number.isFinite(he)) {
    return "hook start and end must be numbers of seconds";
  }
  if (mode !== "repeat" && mode !== "move") return 'hook mode must be "repeat" or "move"';
  if (he <= hs) return "hook end must be greater than hook start";
  const length = he - hs;
  if (length < MIN_HOOK_SECONDS || length > MAX_HOOK_SECONDS) {
    return `hook runs ${length.toFixed(1)}s. It must run between ${MIN_HOOK_SECONDS} and ${MAX_HOOK_SECONDS} seconds.`;
  }
  const inside = coveringSpans(bodyRanges(start, end, segments)).some(
    (s) => s.start - EDGE_TOLERANCE <= hs && he <= s.end + EDGE_TOLERANCE,
  );
  if (!inside) {
    return `hook ${hs.toFixed(2)}-${he.toFixed(2)}s is not inside the clip body. Pick a passage the clip already plays.`;
  }
  if (mode === "move" && playbackRanges(start, end, segments, { start: hs, end: he, mode }).length < 2) {
    return "A move hook cannot take the whole clip. Use repeat, or widen the clip.";
  }
  return null;
}

/**
 * The ranges a clip plays, in order. Without a hook that is its body. With
 * one, the hook comes first; repeat keeps the body whole and move cuts the
 * hook out of it. The renderer may still tighten the body; this is the plan.
 */
export function playbackRanges(
  start: number,
  end: number,
  segments?: TimeRange[] | null,
  hook?: ClipHook | null,
): TimeRange[] {
  const body = bodyRanges(start, end, segments).map((s) => ({ start: s.start, end: s.end }));
  if (!hook) return body;
  const head = { start: hook.start, end: hook.end };
  if (hook.mode === "repeat") return [head, ...body];
  const rest: TimeRange[] = [];
  for (const s of body) {
    for (const [a, b] of [
      [s.start, Math.min(s.end, hook.start)],
      [Math.max(s.start, hook.end), s.end],
    ]) {
      if (b - a >= MIN_REMNANT_SECONDS) rest.push({ start: a, end: b });
    }
  }
  return [head, ...rest];
}

export function playbackDuration(
  start: number,
  end: number,
  segments?: TimeRange[] | null,
  hook?: ClipHook | null,
): number {
  return playbackRanges(start, end, segments, hook).reduce((sum, r) => sum + (r.end - r.start), 0);
}
