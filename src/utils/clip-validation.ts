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

// ---------------------------------------------------------------------------
// Context checks: does this clip make sense to someone who never heard the
// episode? A moment can be perfectly cut and still be unwatchable because it
// opens on an answer whose question stayed behind the cut.
// ---------------------------------------------------------------------------

const MIN_PAYOFF_WORDS = 5;
// Japanese and Chinese do not space-delimit, so every payoff in them counts as
// one "word" however long it runs. A character floor stands in there.
const MIN_PAYOFF_CHARS = 12;

/** Values that mean "the viewer needs to know nothing going in". */
const SELF_CONTAINED = new Set([
  "",
  "-",
  "n/a",
  "na",
  "none",
  "no context",
  "no context needed",
  "nothing",
  "nothing needed",
]);

/**
 * First words that point backwards at something said before the cut. This is a
 * backstop, not the primary gate: the model declares `standalone` itself, and
 * this list only catches the case where it declared "nothing" but cut into the
 * middle of an exchange anyway.
 */
const ORPHAN_OPENERS = new Set([
  "absolutely", "also", "and", "anyway", "because", "but", "cause", "exactly",
  "he", "her", "his", "it", "no", "nope", "ok", "okay", "plus", "right", "she",
  "so", "sure", "that", "their", "them", "they", "those", "totally", "well",
  "which", "yeah", "yep", "yes",
]);

/**
 * Two-word starts that read as self-contained despite a flagged first word,
 * e.g. "So many founders quit" or "It takes ten years".
 */
const SELF_CONTAINED_STARTS = new Set([
  "it costs", "it takes", "it turns", "no idea", "no matter", "no one",
  "so few", "so long", "so many", "so much",
]);

/** Lowercase, punctuation-stripped, single-spaced. Unicode-aware: a Georgian
 * or Japanese payoff has to survive this, not normalize down to nothing. */
function normalize(text: string): string {
  return text
    .toLowerCase()
    .replace(/[^\p{L}\p{N}'\s]/gu, " ")
    .replace(/\s+/gu, " ")
    .trim();
}

function wordCount(text: string): number {
  const n = normalize(text);
  return n ? n.split(" ").length : 0;
}

function isTooThin(payoff: string): boolean {
  const normalized = normalize(payoff);
  return normalized.includes(" ")
    ? wordCount(payoff) < MIN_PAYOFF_WORDS
    : normalized.length < MIN_PAYOFF_CHARS;
}

export function isSelfContained(standalone: string): boolean {
  return SELF_CONTAINED.has(normalize(standalone));
}

/** Returns the backward-pointing opener, or null when the clip starts clean. */
export function findOrphanOpener(previewText: unknown): string | null {
  if (typeof previewText !== "string") return null;
  const words = normalize(previewText).split(" ").filter(Boolean);
  if (!words.length) return null;
  if (SELF_CONTAINED_STARTS.has(words.slice(0, 2).join(" "))) return null;
  // "I mean" is only a stall when the sentence leads with it.
  if (words[0] === "i" && words[1] === "mean") return "i mean";
  return ORPHAN_OPENERS.has(words[0]) ? words[0] : null;
}

export interface SuggestionContext {
  title?: unknown;
  payoff?: unknown;
  standalone?: unknown;
  context_line?: unknown;
  preview_text?: unknown;
}

/** Returns an error message, or null when the clip carries its own context. */
export function validateSuggestionContext(s: SuggestionContext): string | null {
  const payoff = typeof s.payoff === "string" ? s.payoff.trim() : "";
  if (!payoff) {
    return "payoff is required: one sentence, second person, on what the viewer walks away with.";
  }
  if (isTooThin(payoff)) {
    return `payoff is too thin ("${payoff}"). Write a full sentence on what the viewer walks away with.`;
  }
  if (typeof s.title === "string" && normalize(s.title) === normalize(payoff)) {
    return "payoff just restates the title. Say what the viewer gets, not what the clip is called.";
  }

  const standalone = typeof s.standalone === "string" ? s.standalone.trim() : "";
  if (!standalone) {
    return 'standalone is required: what the viewer must already know to follow this clip, or "nothing".';
  }

  const contextLine =
    typeof s.context_line === "string" ? s.context_line.trim() : "";

  if (!isSelfContained(standalone) && !contextLine) {
    return (
      `standalone says the viewer needs to know "${standalone}", but context_line is empty. ` +
      "Either move start_second back to include that setup on camera, or put it in context_line as one line."
    );
  }

  const orphan = findOrphanOpener(s.preview_text);
  if (orphan && !contextLine) {
    return (
      `preview_text opens on "${orphan}", which points at something said before the cut. ` +
      "Either move start_second back to include the question, or set context_line to that setup in one line."
    );
  }

  return null;
}
