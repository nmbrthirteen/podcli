// Port of remotion/src/chunks.ts, the chunker the renderer burns into the MP4.
// The studio's phone preview is only worth showing if it groups and holds words
// exactly like the render, so the two must be changed together.

export interface PreviewWord {
  text: string;
  start: number;
  end: number;
  speaker?: string | null;
}

export interface PreviewChunk {
  words: PreviewWord[];
  start: number;
  end: number;
  displayEnd: number;
}

export interface PreviewChunkOptions {
  wordsPerChunk: number;
  maxCharsPerChunk?: number;
  absorbTail?: number;
  splitTail?: boolean;
}

const BREAK_GAP_SECONDS = 0.8;
const GAP_FILL_MAX_SECONDS = 0.4;
const LAST_CHUNK_HOLD_SECONDS = 1.2;
const TERMINAL_PUNCTUATION = /[.!?…]["')\]]?$/;

function breaksAfter(prev: PreviewWord, next: PreviewWord): boolean {
  return (
    TERMINAL_PUNCTUATION.test(prev.text.trim()) ||
    (prev.speaker != null && next.speaker != null && prev.speaker !== next.speaker) ||
    next.start - prev.end > BREAK_GAP_SECONDS
  );
}

function hasBreakBetween(words: PreviewWord[], from: number, to: number): boolean {
  for (let i = from; i < to; i++) {
    if (breaksAfter(words[i], words[i + 1])) return true;
  }
  return false;
}

export function buildPreviewChunks(
  words: PreviewWord[],
  cfg: PreviewChunkOptions,
  clipEnd?: number,
): PreviewChunk[] {
  const chunks: PreviewChunk[] = [];
  let i = 0;

  while (i < words.length) {
    let end = i + 1;
    let charCount = words[i].text.trim().length;

    while (end < words.length && end - i < cfg.wordsPerChunk) {
      if (breaksAfter(words[end - 1], words[end])) break;
      if (cfg.maxCharsPerChunk != null) {
        const nextChars = charCount + 1 + words[end].text.trim().length;
        if (nextChars > cfg.maxCharsPerChunk) break;
        charCount = nextChars;
      }
      end += 1;
    }

    if (
      cfg.absorbTail != null &&
      end < words.length &&
      words.length - end <= cfg.absorbTail &&
      !hasBreakBetween(words, end - 1, words.length - 1)
    ) {
      end = words.length;
    }
    if (cfg.splitTail && words.length - end === 1 && end - i > 2) {
      end -= 1;
    }

    const slice = words.slice(i, end);
    const speechEnd = slice[slice.length - 1].end;
    chunks.push({ words: slice, start: slice[0].start, end: speechEnd, displayEnd: speechEnd });
    i = end;
  }

  for (let k = 0; k < chunks.length; k++) {
    const next = chunks[k + 1];
    const hold = next
      ? Math.min(next.start, chunks[k].end + GAP_FILL_MAX_SECONDS)
      : chunks[k].end + LAST_CHUNK_HOLD_SECONDS;
    const capped = clipEnd != null ? Math.min(hold, clipEnd) : hold;
    chunks[k].displayEnd = Math.max(chunks[k].end, capped);
  }

  return chunks;
}

export function activePreviewChunk(
  chunks: PreviewChunk[],
  time: number,
): PreviewChunk | undefined {
  return chunks.find((c) => time >= c.start && time < c.displayEnd);
}
