import { createHash } from "node:crypto";
import { createReadStream } from "node:fs";
import { readFile, stat } from "node:fs/promises";
import { join } from "node:path";
import { z } from "zod";
import { paths } from "../config/paths.js";

/**
 * Client for podcli Pro's hosted API.
 *
 * The Python backend has its own copy of this because the two runtimes cannot
 * share one: deliberate duplication of about eighty lines, not an accident.
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
const authFileSchema = z.looseObject({ token: z.string().optional() });

export async function readToken(): Promise<string | null> {
  const fromEnv = (process.env.PODCLI_TOKEN || "").trim();

  if (fromEnv) return fromEnv;

  try {
    const raw = await readFile(join(paths.home, "auth.json"), "utf-8");

    return (authFileSchema.parse(JSON.parse(raw)).token || "").trim() || null;
  } catch {
    return null;
  }
}

export async function signedIn(): Promise<boolean> {
  return (await readToken()) !== null;
}

async function request<S extends z.ZodType, B extends object = object>(
  method: string,
  path: string,
  schema: S,
  body?: B,
  timeoutMs = 30_000,
): Promise<z.infer<S> | null> {
  const token = await readToken();

  if (!token) throw new Error("not signed in");

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);

  try {
    const response = await fetch(`${apiUrl()}${path}`, {
      method,
      headers: {
        authorization: `Bearer ${token}`,
        ...(body !== undefined && { "content-type": "application/json" }),
      },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: controller.signal,
    });

    if (!response.ok) {
      const detail = await response.text().catch(() => "");
      throw new Error(`HTTP ${response.status}${detail ? `: ${detail.slice(0, 200)}` : ""}`);
    }

    const text = await response.text();

    return text ? schema.parse(JSON.parse(text)) : null;
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

  // No encoding is set on the stream, so every chunk is a Buffer.
  for await (const chunk of stream) hash.update(Buffer.from(chunk));

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
  extra?: Record<string, string | number | boolean | null>;
};

export async function registerClip(clip: ClipRegistration): Promise<{ id: string } | null> {
  return request("POST", "/v1/clips", z.object({ id: z.string() }), clip);
}

/** Matches the server's body cap; a larger file is refused before the upload. */
const MAX_CLIP_BYTES = 200 * 1024 * 1024;

/**
 * Send the rendered clip itself, so share links have something to play.
 *
 * Only the rendered clip travels: never the source video. It is the whole
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

const breakdownSchema = z.object({
  key: z.string(),
  clips: z.number(),
  retention: z.number().nullable(),
  ctr: z.number().nullable(),
  views: z.number().nullable(),
});

export type Breakdown = z.infer<typeof breakdownSchema>;

const insightsSchema = z.object({
  sampleSize: z.number(),
  byContentType: z.array(breakdownSchema),
  byCaptionStyle: z.array(breakdownSchema),
  byLength: z.array(breakdownSchema),
  topClips: z.array(
    z.object({ title: z.string(), retention: z.number(), views: z.number(), content_type: z.string() }),
  ),
  guidance: z.array(z.string()),
});

export type Insights = z.infer<typeof insightsSchema>;

const preferencesSchema = z.object({
  titleEdits: z.array(z.object({ before: z.string(), after: z.string() })),
  discardRate: z.number().nullable(),
  observations: z.array(z.string()),
});

export type Preferences = z.infer<typeof preferencesSchema>;

// Nullable because `request` returns null for an empty body, and a caller that
// trusts the declared shape would dereference it.
export async function getInsights(): Promise<Insights | null> {
  return request("GET", "/v1/insights", insightsSchema);
}

export async function getPreferences(): Promise<Preferences | null> {
  return request("GET", "/v1/insights/preferences", preferencesSchema);
}

const whoamiSchema = z.object({
  workspaceId: z.string(),
  role: z.string(),
  plan: z.string(),
  workspace: z.object({ name: z.string(), episodes_used: z.number() }),
});

export type WhoamiResult = z.infer<typeof whoamiSchema>;

export async function whoami(): Promise<WhoamiResult> {
  const me = await request("GET", "/v1/auth/me", whoamiSchema, undefined, 10_000);

  if (!me) throw new Error("empty response from /v1/auth/me");

  return me;
}

const remoteKnowledgeFileSchema = z.object({ path: z.string(), version: z.number(), updated_at: z.string() });

export type RemoteKnowledgeFile = z.infer<typeof remoteKnowledgeFileSchema>;

export async function listKnowledge(): Promise<RemoteKnowledgeFile[]> {
  const payload = await request("GET", "/v1/knowledge", z.object({ files: z.array(remoteKnowledgeFileSchema).optional() }));

  return payload?.files ?? [];
}

export async function getKnowledge(path: string): Promise<{ content: string; version: number }> {
  const file = await request(
    "GET",
    `/v1/knowledge/file?path=${encodeURIComponent(path)}`,
    z.object({ content: z.string(), version: z.number() }),
  );

  if (!file) throw new Error(`empty response for knowledge file ${path}`);

  return file;
}

const knowledgeConflictSchema = z.object({ version: z.number(), content: z.string() });
const knowledgePutSchema = z.object({ version: z.number(), unchanged: z.boolean().optional() });

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
    const body = knowledgeConflictSchema.parse(await response.json());

    return { conflict: true, version: body.version, content: body.content };
  }

  if (!response.ok) {
    throw new Error(`HTTP ${response.status}: ${(await response.text()).slice(0, 200)}`);
  }

  const body = knowledgePutSchema.parse(await response.json());

  return { conflict: false, version: body.version, unchanged: Boolean(body.unchanged) };
}

const remoteAssetSchema = z.object({
  id: z.string(),
  name: z.string(),
  kind: z.string(),
  is_default: z.boolean(),
  size_bytes: z.string(),
  checksum: z.string(),
});

export type RemoteAsset = z.infer<typeof remoteAssetSchema>;

/** Mirrors how the workspace digests an asset, so an upload can be skipped. */
export function checksum(body: Buffer): string {
  return createHash("sha256").update(body).digest("hex").slice(0, 32);
}

export async function listAssets(): Promise<RemoteAsset[]> {
  const payload = await request("GET", "/v1/assets", z.object({ assets: z.array(remoteAssetSchema).optional() }));

  return payload?.assets ?? [];
}

const uploadedAssetSchema = z.object({ id: z.string(), unchanged: z.boolean().optional() });

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

  return uploadedAssetSchema.parse(await response.json());
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
  await request("POST", `/v1/clips/${cloudClipId}/events`, z.unknown(), { kind, before, after });
}
