import type { CSSProperties } from "react";

// Rolls into hours past 3600s: podcast timestamps ran past "78:31" without it.
export const fmt = (s: number) => {
  const t = Math.max(0, s);
  const hours = Math.floor(t / 3600);
  const minutes = Math.floor((t % 3600) / 60);
  const seconds = Math.floor(t % 60);
  const mm = String(minutes).padStart(2, "0");
  const ss = String(seconds).padStart(2, "0");
  return hours > 0 ? `${hours}:${mm}:${ss}` : `${minutes}:${ss}`;
};

export const fmtMs = (s: number) =>
  `${fmt(s)}.${String(Math.floor((s % 1) * 1000)).padStart(3, "0")}`;

// Kept in sync with the .section-label CSS class so section headers look
// identical whether set via this object or the class.
export const labelStyle: CSSProperties = {
  fontSize: 11,
  fontWeight: 700,
  letterSpacing: "0.8px",
  textTransform: "uppercase",
  color: "var(--text2)",
  marginBottom: 10,
  display: "block",
};

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function request<T>(path: string, opts: RequestInit, jsonHeaders: boolean): Promise<T> {
  const res = await fetch(`/api${path}`, {
    ...opts,
    headers: jsonHeaders
      ? { "Content-Type": "application/json", ...(opts.headers || {}) }
      : opts.headers,
  });

  const text = await res.text();
  let body: any = undefined;
  if (text) {
    try {
      body = JSON.parse(text);
    } catch {
      body = text;
    }
  }

  if (!res.ok || (body && typeof body === "object" && body.error)) {
    const msg =
      (typeof body === "string" && body) ||
      (body && (body.error || body.message)) ||
      `HTTP ${res.status}`;
    throw new ApiError(String(msg), res.status);
  }
  return body as T;
}

/** JSON request against /api. Throws ApiError on non-2xx or `{ error }` bodies. */
export function api<T = any>(path: string, opts: RequestInit = {}): Promise<T> {
  return request<T>(path, opts, true);
}

/** Multipart upload (FormData body); skips the JSON content-type header. */
export function upload<T = any>(path: string, form: FormData): Promise<T> {
  return request<T>(path, { method: "POST", body: form }, false);
}

export function timeAgo(iso: string): string {
  const ms = Date.now() - new Date(iso).getTime();
  const h = ms / 3_600_000;
  if (h < 1) return `${Math.max(1, Math.round(h * 60))}m ago`;
  if (h < 24) return `${Math.round(h)}h ago`;
  return new Date(iso).toLocaleDateString();
}

export const basename = (p: string) => (p || "").split(/[/\\]/).pop() || "";

interface AssetReference {
  name: string;
  path: string;
  type?: string;
}

/**
 * Resolves a persisted asset name or absolute path back to its registered name.
 * Preview URLs must use the asset route; the video source route intentionally
 * rejects arbitrary image paths.
 */
export function resolveAssetName(
  assets: AssetReference[],
  reference: string,
  type?: string,
): string | null {
  if (!reference) return null;
  return assets.find(
    (asset) =>
      (!type || asset.type === type) &&
      (asset.name === reference || asset.path === reference),
  )?.name ?? null;
}

interface TranscriptSegmentLike {
  start?: number;
  end?: number;
  text?: string;
  speaker?: string | null;
}

interface TranscriptLike {
  transcript?: string;
  text?: string;
  segments?: TranscriptSegmentLike[];
}

export type TranscriptFormat = "readable" | "timestamped";

interface TranscriptParagraph {
  start: number;
  speaker: string | null;
  text: string;
}

const cleanTranscriptText = (value: unknown): string =>
  typeof value === "string" ? value.replace(/\s+/g, " ").trim() : "";

const endsSentence = (value: string): boolean => /[.!?…][\]})"']?$/.test(value);

function friendlySpeaker(value: string | null | undefined): string | null {
  const speaker = cleanTranscriptText(value);
  if (!speaker) return null;
  const machineLabel = speaker.match(/^speaker[_-]+0*(\d+)$/i);
  if (machineLabel) return `Speaker ${Number(machineLabel[1]) + 1}`;
  return speaker.replace(/_/g, " ");
}

function paragraphsFromPlainText(value: string): TranscriptParagraph[] {
  const text = cleanTranscriptText(value);
  if (!text) return [];
  const sentences = text.split(/(?<=[.!?…])\s+/).filter(Boolean);
  const paragraphs: TranscriptParagraph[] = [];
  let buffer = "";
  for (const sentence of sentences) {
    buffer = buffer ? `${buffer} ${sentence}` : sentence;
    if (buffer.length >= 420 || sentences.length === 1) {
      paragraphs.push({ start: 0, speaker: null, text: buffer });
      buffer = "";
    }
  }
  if (buffer) paragraphs.push({ start: 0, speaker: null, text: buffer });
  return paragraphs;
}

function transcriptParagraphs(transcript: TranscriptLike | null | undefined): TranscriptParagraph[] {
  const segments = Array.isArray(transcript?.segments)
    ? transcript.segments.filter((segment) => cleanTranscriptText(segment?.text))
    : [];
  if (!segments.length) {
    return paragraphsFromPlainText(transcript?.transcript || transcript?.text || "");
  }

  const paragraphs: TranscriptParagraph[] = [];
  let current: TranscriptParagraph | null = null;
  let previousEnd: number | null = null;
  const flush = () => {
    if (current?.text) paragraphs.push(current);
    current = null;
  };

  for (const segment of segments) {
    const text = cleanTranscriptText(segment.text);
    const segmentStart: number = typeof segment.start === "number" && Number.isFinite(segment.start)
      ? segment.start
      : previousEnd ?? 0;
    const segmentEnd: number = typeof segment.end === "number" && Number.isFinite(segment.end)
      ? segment.end
      : segmentStart;
    const speaker = friendlySpeaker(segment.speaker);
    const speakerChanged = current !== null && current.speaker !== speaker;
    const longPause = current !== null && previousEnd !== null && segmentStart - previousEnd >= 2.5;
    if (speakerChanged || longPause) flush();

    if (!current) current = { start: segmentStart, speaker, text };
    else current.text = `${current.text} ${text}`;
    previousEnd = segmentEnd;

    if ((current.text.length >= 420 && endsSentence(current.text)) || current.text.length >= 900) {
      flush();
    }
  }
  flush();
  return paragraphs;
}

/** Produces copy-ready paragraphs from Whisper or imported transcript data. */
export function formatTranscriptText(
  transcript: TranscriptLike | null | undefined,
  format: TranscriptFormat = "readable",
): string {
  return transcriptParagraphs(transcript)
    .map((paragraph) => {
      const heading = format === "timestamped"
        ? `[${fmt(paragraph.start)}]${paragraph.speaker ? ` ${paragraph.speaker}` : ""}`
        : paragraph.speaker;
      return heading ? `${heading}\n${paragraph.text}` : paragraph.text;
    })
    .join("\n\n");
}

// A render result's clip_index counts the clips submitted to the renderer, which
// for an agent-driven export is not the studio's clip order. The server stamps
// every row with the bounds of the clip it rendered; match a result to a clip on
// those instead of on position.
export const clipBoundsKey = (start?: number, end?: number): string | null =>
  typeof start === "number" && typeof end === "number"
    ? `${start.toFixed(2)}:${end.toFixed(2)}`
    : null;

export const resultBoundsKey = (row?: ClipResultRow | null): string | null =>
  row ? clipBoundsKey(row.source_start_second, row.source_end_second) : null;

interface ClipResultRow {
  source_start_second?: number;
  source_end_second?: number;
  [key: string]: unknown;
}

interface ClipIdentity {
  clip_id?: string;
  start_second?: number;
  end_second?: number;
}

// A suggestion's position shifts whenever an agent deletes or inserts a clip, so
// anything the studio computes per clip is keyed by identity instead. Sessions
// persisted before clips carried a clip_id fall back to their bounds, which also
// makes a re-timed clip drop its stale score.
export const clipKey = (clip: ClipIdentity): string =>
  clip.clip_id || clipBoundsKey(clip.start_second, clip.end_second) || "";

type EnergyLevel = "high" | "medium" | "low";
interface EnergyEntry {
  score: number;
  level: EnergyLevel;
}

const energyLevel = (score: number): EnergyLevel =>
  score >= 7 ? "high" : score >= 4 ? "medium" : "low";

/** Maps the backend's positional segment scores onto the clips they were measured for. */
export function buildEnergyMap(
  scores: unknown[],
  clips: ClipIdentity[],
): Record<string, EnergyEntry> {
  const map: Record<string, EnergyEntry> = {};
  scores.forEach((score, i) => {
    const clip = clips[i];
    if (!clip || typeof score !== "number") return;
    const key = clipKey(clip);
    if (key) map[key] = { score, level: energyLevel(score) };
  });
  return map;
}

export function dropEnergy(
  map: Record<string, EnergyEntry>,
  clip: ClipIdentity,
): Record<string, EnergyEntry> {
  const key = clipKey(clip);
  if (!key || !(key in map)) return map;
  const next = { ...map };
  delete next[key];
  return next;
}

/** Keeps the keyboard cursor on a row that exists after the clip list changes. */
export const clampClipIndex = (idx: number | null, length: number): number | null =>
  idx === null || length === 0 ? null : Math.min(idx, length - 1);

/**
 * The result for `clip`, or undefined if it has not been rendered. Rows written
 * before the server stamped bounds (a restored session) carry no key, so they
 * still land by position.
 */
export function findClipResult<T extends ClipResultRow>(
  results: (T | undefined)[],
  clip: { start_second?: number; end_second?: number },
  positionalIdx: number,
): T | undefined {
  const key = clipBoundsKey(clip.start_second, clip.end_second);
  if (key) {
    const keyed = results.find((row) => resultBoundsKey(row) === key);
    if (keyed) return keyed;
  }
  const row = results[positionalIdx];
  return row && !resultBoundsKey(row) ? row : undefined;
}
