import type { Word } from "./types";

export interface Chunk {
  words: Word[];
  start: number;
  end: number;
  displayEnd: number;
}

export interface ChunkOptions {
  perChunk: number;
  maxChars?: number;
  absorbTail?: number;
  splitTail?: boolean;
  /** Composition duration in seconds; caps the hold on the last chunk. */
  clipEnd?: number;
}

const BREAK_GAP_SECONDS = 0.8;
// Mirrors CAPTION_GAP_FILL_MAX in backend/services/caption_renderer.py: both
// renderers must burn the same caption timings.
const GAP_FILL_MAX_SECONDS = 0.4;
const LAST_CHUNK_HOLD_SECONDS = 1.2;
const TERMINAL_PUNCTUATION = /[.!?…]["')\]]?$/;

function breaksAfter(prev: Word, next: Word): boolean {
  return (
    TERMINAL_PUNCTUATION.test(prev.word.trim()) ||
    (prev.speaker != null && next.speaker != null && prev.speaker !== next.speaker) ||
    next.start - prev.end > BREAK_GAP_SECONDS
  );
}

function hasBreakBetween(words: Word[], from: number, to: number): boolean {
  for (let i = from; i < to; i++) {
    if (breaksAfter(words[i], words[i + 1])) return true;
  }
  return false;
}

export function buildChunks(words: Word[], opts: ChunkOptions): Chunk[] {
  const chunks: Chunk[] = [];
  let i = 0;

  while (i < words.length) {
    let end = i + 1;
    let charCount = words[i].word.trim().length;

    while (end < words.length && end - i < opts.perChunk) {
      if (breaksAfter(words[end - 1], words[end])) break;
      if (opts.maxChars != null) {
        const nextChars = charCount + 1 + words[end].word.trim().length;
        if (nextChars > opts.maxChars) break;
        charCount = nextChars;
      }
      end += 1;
    }

    if (
      opts.absorbTail != null &&
      end < words.length &&
      words.length - end <= opts.absorbTail &&
      !hasBreakBetween(words, end - 1, words.length - 1)
    ) {
      end = words.length;
    }
    if (opts.splitTail && words.length - end === 1 && end - i > 2) {
      end -= 1;
    }

    const slice = words.slice(i, end);
    const speechEnd = slice[slice.length - 1].end;
    chunks.push({
      words: slice,
      start: slice[0].start,
      end: speechEnd,
      displayEnd: speechEnd,
    });
    i = end;
  }

  // Captions vanishing during inter-chunk pauses reads as flicker, so a chunk
  // holds until the next one starts. The hold is capped, or a chunk break on a
  // long silence (a musical sting, dead air) freezes the last sentence on screen
  // for the whole pause.
  for (let k = 0; k < chunks.length; k++) {
    const next = chunks[k + 1];
    const hold = next
      ? Math.min(next.start, chunks[k].end + GAP_FILL_MAX_SECONDS)
      : chunks[k].end + LAST_CHUNK_HOLD_SECONDS;
    const capped = opts.clipEnd != null ? Math.min(hold, opts.clipEnd) : hold;
    chunks[k].displayEnd = Math.max(chunks[k].end, capped);
  }

  return chunks;
}

export function activeChunkAt(chunks: Chunk[], time: number): Chunk | undefined {
  return chunks.find((c) => time >= c.start && time < c.displayEnd);
}
