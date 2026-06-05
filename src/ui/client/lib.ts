import type { CSSProperties } from "react";

export const fmt = (s: number) =>
  `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;

export const fmtMs = (s: number) =>
  `${fmt(s)}.${String(Math.floor((s % 1) * 1000)).padStart(3, "0")}`;

export const labelStyle: CSSProperties = {
  fontSize: 11,
  fontWeight: 700,
  letterSpacing: "0.5px",
  textTransform: "uppercase",
  color: "var(--text2)",
  marginBottom: 8,
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
    const msg = (body && (body.error || body.message)) || `HTTP ${res.status}`;
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

export const basename = (p: string) => (p || "").split("/").pop() || "";
