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
