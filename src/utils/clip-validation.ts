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
  "he", "her", "his", "however", "instead", "it", "meanwhile", "no", "nope",
  "ok", "okay", "otherwise", "plus", "right", "she", "sure", "that", "their",
  "them", "they", "those", "though", "totally", "well", "which", "yeah", "yep",
  "yes",
]);

/**
 * "so" is the one opener that swings both ways. "So actually, did you try that"
 * and "So if you look at the numbers" start a thought; "So that's why we quit"
 * points back. Flag it only when the word after it is itself backward-pointing,
 * rather than rejecting every clip that opens on a speaker gathering themselves.
 */
const SO_POINTS_BACK = new Set([
  "he", "his", "it", "its", "she", "that", "their", "them", "they", "this",
  "those", "we",
]);

/**
 * Two-word starts that read as self-contained despite a flagged first word,
 * e.g. "So many founders quit" or "It takes ten years".
 */
const SELF_CONTAINED_STARTS = new Set([
  "it costs", "it takes", "it turns", "no idea", "no matter", "no one",
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
  if (words[0] === "so") {
    // "that's" and "they're" have to match "that" and "they".
    const next = (words[1] ?? "").replace(/'(s|d|ll|re|ve)$/, "");
    return SO_POINTS_BACK.has(next) ? `so ${next}` : null;
  }
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

  // context_line is not consulted here on purpose. Nothing burns it into the
  // video yet, so treating it as coverage would pass clips whose viewer still
  // has no setup, which is the exact failure these checks exist to catch. It
  // stays on the suggestion as editor metadata until the renderer draws it.
  if (!isSelfContained(standalone)) {
    return (
      `standalone says the viewer needs to know "${standalone}". Move start_second back ` +
      "so the clip itself contains that setup on camera. A context_line does not cover it: " +
      "nothing renders it yet."
    );
  }

  const orphan = findOrphanOpener(s.preview_text);
  if (orphan) {
    return (
      `preview_text opens on "${orphan}", which points at something said before the cut. ` +
      "Move start_second back so the question is inside the clip."
    );
  }

  return null;
}
