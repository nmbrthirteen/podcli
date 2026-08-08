import { createHash } from "node:crypto";
import { createReadStream } from "node:fs";
import { readFile, stat } from "node:fs/promises";
import { join } from "node:path";
import { paths } from "../config/paths.js";

/**
 * Client for podcli Pro's hosted API.
 *
 * The Python backend has its own copy of this because the two runtimes cannot
 * share one — deliberate duplication of about eighty lines, not an accident.
 *
 * Nothing here is secret. The server decides entitlement, so a patched client
 * gets an HTTP 401 rather than free Pro.
 */

const DEFAULT_API_URL = "https://api.podcli.com";

export function apiUrl(): string {
  return (process.env.PODCLI_API_URL || DEFAULT_API_URL).replace(/\/+$/, "");
}

/**
 * Read on every call, deliberately not cached.
 *
 * The studio server is long-running, so a cached token survives `podcli logout`
 * in another terminal and the UI keeps claiming the user is signed in. Reading a
 * small file costs microseconds against the HTTP request that follows it, so
 * caching bought nothing and cost correctness.
 */
export async function readToken(): Promise<string | null> {
  const fromEnv = (process.env.PODCLI_TOKEN || "").trim();
  if (fromEnv) return fromEnv;
  try {
    const raw = await readFile(join(paths.home, "auth.json"), "utf-8");
    return ((JSON.parse(raw).token as string | undefined) || "").trim() || null;
  } catch {
    return null;
  }
}

export async function signedIn(): Promise<boolean> {
  return (await readToken()) !== null;
}

async function request(method: string, path: string, body?: unknown, timeoutMs = 30_000) {
  const token = await readToken();
  if (!token) throw new Error("not signed in");

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(`${apiUrl()}${path}`, {
      method,
      headers: {
        authorization: `Bearer ${token}`,
        ...(body === undefined ? {} : { "content-type": "application/json" }),
      },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: controller.signal,
    });
    if (!response.ok) {
      const detail = await response.text().catch(() => "");
      throw new Error(`HTTP ${response.status}${detail ? `: ${detail.slice(0, 200)}` : ""}`);
    }
    const text = await response.text();
    return text ? JSON.parse(text) : null;
  } finally {
    clearTimeout(timer);
  }
}

/**
 * Identifies an episode across machines.
 *
 * Hashing the first 8 MB rather than the whole file: a 2 GB master would take
 * seconds to digest and the head of a video is more than distinctive enough to
 * key on. Two editors working from the same file land on the same episode.
 */
export async function sourceHash(videoPath: string): Promise<string> {
  const hash = createHash("sha256");
  const stream = createReadStream(videoPath, { start: 0, end: 8 * 1024 * 1024 - 1 });
  for await (const chunk of stream) hash.update(chunk as Buffer);
  return hash.digest("hex").slice(0, 32);
}

export type ClipRegistration = {
  sourceHash: string;
  episodeTitle?: string;
  episodeDuration?: number;
  title?: string;
  startSecond?: number;
  endSecond?: number;
  durationSec?: number;
  contentType?: string;
  captionStyle?: string;
  aspectRatio?: string;
  aiEngine?: string;
  score?: number;
  quote?: string;
  reasoning?: string;
  transcriptSlice?: string;
  extra?: Record<string, unknown>;
};

export async function registerClip(clip: ClipRegistration): Promise<{ id: string } | null> {
  return request("POST", "/v1/clips", clip);
}

/** Matches the server's body cap; a larger file is refused before the upload. */
const MAX_CLIP_BYTES = 200 * 1024 * 1024;

/**
 * Send the rendered clip itself, so share links have something to play.
 *
 * Only the rendered clip travels — never the source video. It is the whole
 * reason a share link can exist without the storage cost of the master.
 */
export async function uploadClipVideo(clipId: string, filePath: string): Promise<boolean> {
  const token = await readToken();
  if (!token) return false;

  const { size } = await stat(filePath);
  if (size === 0 || size > MAX_CLIP_BYTES) return false;

  const response = await fetch(`${apiUrl()}/v1/clips/${clipId}/video`, {
    method: "PUT",
    headers: { authorization: `Bearer ${token}`, "content-type": "video/mp4" },
    body: await readFile(filePath),
    signal: AbortSignal.timeout(300_000),
  });
  if (!response.ok) {
    throw new Error(`HTTP ${response.status}: ${(await response.text()).slice(0, 200)}`);
  }
  return true;
}

export type Breakdown = {
  key: string;
  clips: number;
  retention: number | null;
  ctr: number | null;
  views: number | null;
};

export type Insights = {
  sampleSize: number;
  byContentType: Breakdown[];
  byCaptionStyle: Breakdown[];
  byLength: Breakdown[];
  topClips: Array<{ title: string; retention: number; views: number; content_type: string }>;
  guidance: string[];
};

export type Preferences = {
  titleEdits: Array<{ before: string; after: string }>;
  discardRate: number | null;
  observations: string[];
};

// Nullable because `request` returns null for an empty body, and a caller that
// trusts the declared shape would dereference it.
export async function getInsights(): Promise<Insights | null> {
  return request("GET", "/v1/insights");
}

export async function getPreferences(): Promise<Preferences | null> {
  return request("GET", "/v1/insights/preferences");
}

export async function whoami(): Promise<{
  workspaceId: string;
  role: string;
  plan: string;
  workspace: { name: string; episodes_used: number };
}> {
  return request("GET", "/v1/auth/me", undefined, 10_000);
}

export type RemoteKnowledgeFile = { path: string; version: number; updated_at: string };

export async function listKnowledge(): Promise<RemoteKnowledgeFile[]> {
  const payload = await request("GET", "/v1/knowledge");
  return payload?.files ?? [];
}

export async function getKnowledge(path: string): Promise<{ content: string; version: number }> {
  return request("GET", `/v1/knowledge/file?path=${encodeURIComponent(path)}`);
}

export type PutKnowledgeResult =
  | { conflict: false; version: number; unchanged: boolean }
  | { conflict: true; version: number; content: string };

export async function putKnowledge(
  path: string,
  content: string,
  expectedVersion?: number,
): Promise<PutKnowledgeResult> {
  const token = await readToken();
  if (!token) throw new Error("not signed in");

  const response = await fetch(`${apiUrl()}/v1/knowledge/file`, {
    method: "PUT",
    headers: { authorization: `Bearer ${token}`, "content-type": "application/json" },
    body: JSON.stringify({ path, content, expectedVersion }),
    signal: AbortSignal.timeout(30_000),
  });

  // A 409 is an expected outcome here, not an error: someone else edited the
  // file. The body carries their version so the caller can show both.
  if (response.status === 409) {
    const body = await response.json();
    return { conflict: true, version: body.version, content: body.content };
  }
  if (!response.ok) {
    throw new Error(`HTTP ${response.status}: ${(await response.text()).slice(0, 200)}`);
  }
  const body = await response.json();
  return { conflict: false, version: body.version, unchanged: Boolean(body.unchanged) };
}

export type RemoteAsset = {
  id: string;
  name: string;
  kind: string;
  is_default: boolean;
  size_bytes: string;
  checksum: string;
};

/** Mirrors how the workspace digests an asset, so an upload can be skipped. */
export function checksum(body: Buffer): string {
  return createHash("sha256").update(body).digest("hex").slice(0, 32);
}

export async function listAssets(): Promise<RemoteAsset[]> {
  const payload = await request("GET", "/v1/assets");
  return payload?.assets ?? [];
}

export async function uploadAsset(
  name: string,
  kind: string,
  body: Buffer,
  isDefault = false,
): Promise<{ id: string; unchanged?: boolean }> {
  const token = await readToken();
  if (!token) throw new Error("not signed in");

  const params = new URLSearchParams({ name, kind, isDefault: String(isDefault) });
  const response = await fetch(`${apiUrl()}/v1/assets?${params}`, {
    method: "PUT",
    headers: {
      authorization: `Bearer ${token}`,
      "content-type": "application/octet-stream",
    },
    // Node's fetch wants a view, not the Buffer's whole underlying pool.
    body: new Uint8Array(body),
    signal: AbortSignal.timeout(300_000),
  });
  if (!response.ok) {
    throw new Error(`HTTP ${response.status}: ${(await response.text()).slice(0, 200)}`);
  }
  return response.json();
}

export async function downloadAsset(id: string): Promise<Buffer> {
  const token = await readToken();
  if (!token) throw new Error("not signed in");

  const response = await fetch(`${apiUrl()}/v1/assets/${id}/download`, {
    headers: { authorization: `Bearer ${token}` },
    signal: AbortSignal.timeout(300_000),
  });
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  return Buffer.from(await response.arrayBuffer());
}

export type ClipEventKind =
  | "suggested" | "rendered" | "discarded"
  | "title_edited" | "thumbnail_regenerated"
  | "approved" | "changes_requested" | "published";

export async function logClipEvent(
  cloudClipId: string,
  kind: ClipEventKind,
  before?: string,
  after?: string,
): Promise<void> {
  await request("POST", `/v1/clips/${cloudClipId}/events`, { kind, before, after });
}
