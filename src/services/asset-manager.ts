import { readFile, writeFile, copyFile, mkdir, rename } from "fs/promises";
import { existsSync, createWriteStream } from "fs";
import { join, extname } from "path";
import { spawn } from "child_process";
import { Readable } from "stream";
import { pipeline } from "stream/promises";
import { paths } from "../config/paths.js";
import { ASSETS_SCHEMA_VERSION } from "../models/index.js";
import type { Asset, AssetType, AssetRegistry } from "../models/index.js";

const VIDEO_EXTS = new Set([".mp4", ".mov", ".mkv", ".webm", ".m4v"]);
const IMAGE_EXTS = new Set([".png", ".jpg", ".jpeg", ".svg", ".webp", ".gif"]);
const AUDIO_EXTS = new Set([".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg"]);

export const DEFAULTABLE_TYPES: AssetType[] = ["logo", "outro", "intro", "music"];

export function inferType(filePath: string): AssetType {
  const ext = extname(filePath).toLowerCase();
  if (ext === ".png" || ext === ".svg") return "logo";
  if (IMAGE_EXTS.has(ext)) return "image";
  if (VIDEO_EXTS.has(ext)) return "video";
  if (AUDIO_EXTS.has(ext)) return "audio";
  return "other";
}

/** Outros were historically registered as "video"; treat both as outros. */
function isOutroType(type: string): boolean {
  return type === "outro" || type === "video";
}

export class AssetManager {
  private registryPath = paths.assetsRegistry;
  private assetsDir = paths.assets;

  private async ensureDir() {
    if (!existsSync(this.assetsDir)) {
      await mkdir(this.assetsDir, { recursive: true });
    }
  }

  async load(): Promise<AssetRegistry> {
    let registry: AssetRegistry;
    try {
      if (!existsSync(this.registryPath)) return { schemaVersion: ASSETS_SCHEMA_VERSION, assets: [] };
      const raw = await readFile(this.registryPath, "utf-8");
      const parsed = JSON.parse(raw);
      const bag = Array.isArray(parsed) ? { assets: parsed } : (parsed ?? {});
      registry = { ...bag, assets: Array.isArray(bag.assets) ? bag.assets : [] } as AssetRegistry;
    } catch {
      return { schemaVersion: ASSETS_SCHEMA_VERSION, assets: [] };
    }
    const { registry: migrated, changed } = migrate(registry);
    if (changed) {
      try {
        await this.save(migrated);
      } catch {
        // Read-only filesystem or a racing writer — serve the migrated view anyway.
      }
    }
    return migrated;
  }

  private async save(registry: AssetRegistry): Promise<void> {
    await this.ensureDir();
    registry.schemaVersion = ASSETS_SCHEMA_VERSION;
    const tmp = `${this.registryPath}.${process.pid}.tmp`;
    await writeFile(tmp, JSON.stringify(registry, null, 2), "utf-8");
    await rename(tmp, this.registryPath);
  }

  async register(name: string, filePath: string, type: AssetType): Promise<Asset> {
    if (!existsSync(filePath)) {
      throw new Error(`File not found: ${filePath}`);
    }
    const registry = await this.load();
    const existing = registry.assets.findIndex((a) => a.name === name);
    const prior = existing >= 0 ? registry.assets[existing] : undefined;
    const asset: Asset = {
      name,
      type,
      path: filePath,
      addedAt: prior?.addedAt ?? new Date().toISOString(),
      ...(prior?.default ? { default: true } : {}),
    };
    if (existing >= 0) {
      registry.assets[existing] = asset;
    } else {
      registry.assets.push(asset);
    }
    await this.save(registry);
    return asset;
  }

  async unregister(name: string): Promise<void> {
    const registry = await this.load();
    registry.assets = registry.assets.filter((a) => a.name !== name);
    await this.save(registry);
  }

  async list(type?: string): Promise<Asset[]> {
    const registry = await this.load();
    if (type) return registry.assets.filter((a) => a.type === type);
    return registry.assets;
  }

  async setDefault(name: string): Promise<Asset> {
    const registry = await this.load();
    const target = registry.assets.find((a) => a.name === name);
    if (!target) throw new Error(`Asset not found: ${name}`);
    for (const a of registry.assets) {
      if (sameDefaultGroup(a.type, target.type)) delete a.default;
    }
    target.default = true;
    await this.save(registry);
    return target;
  }

  async clearDefault(name: string): Promise<void> {
    const registry = await this.load();
    const target = registry.assets.find((a) => a.name === name);
    if (target) {
      delete target.default;
      await this.save(registry);
    }
  }

  async rename(name: string, newName: string): Promise<Asset> {
    const trimmed = newName.trim();
    if (!trimmed) throw new Error("New name is required");
    const registry = await this.load();
    const target = registry.assets.find((a) => a.name === name);
    if (!target) throw new Error(`Asset not found: ${name}`);
    if (trimmed !== name && registry.assets.some((a) => a.name === trimmed)) {
      throw new Error(`An asset named "${trimmed}" already exists`);
    }
    target.name = trimmed;
    await this.save(registry);
    return target;
  }

  async getDefault(type: AssetType): Promise<string | null> {
    const registry = await this.load();
    const group = registry.assets.filter((a) => sameDefaultGroup(a.type, type));
    const flagged = group.find((a) => a.default && existsSync(a.path));
    if (flagged) return flagged.path;
    const firstExisting = group.find((a) => existsSync(a.path));
    return firstExisting ? firstExisting.path : null;
  }

  async resolve(nameOrPath: string): Promise<string | null> {
    if (!nameOrPath) return null;
    const registry = await this.load();
    const asset = registry.assets.find((a) => a.name === nameOrPath);
    if (asset && existsSync(asset.path)) return asset.path;
    if (existsSync(nameOrPath)) return nameOrPath;
    return null;
  }

  async importFile(sourcePath: string, name: string, type: AssetType): Promise<Asset> {
    await this.ensureDir();
    const destPath = join(this.assetsDir, safeAssetFilename(name, sourcePath));
    await copyFile(sourcePath, destPath);
    return this.register(name, destPath, type);
  }

  async importUrl(url: string, name: string, type?: AssetType): Promise<Asset> {
    assertSafeUrl(url);
    await this.ensureDir();
    const destPath = looksLikeDirectFile(url)
      ? await downloadDirect(url, join(this.assetsDir, safeAssetFilename(name, url)))
      : await downloadWithYtDlp(url, this.assetsDir, name);
    // Infer from the downloaded file, not the URL (query strings break extname).
    return this.register(name, destPath, type ?? inferType(destPath));
  }
}

function sameDefaultGroup(a: string, b: string): boolean {
  if (isOutroType(a) && isOutroType(b)) return true;
  return a === b;
}

export function migrate(registry: AssetRegistry): { registry: AssetRegistry; changed: boolean } {
  let changed = false;
  if (registry.schemaVersion !== ASSETS_SCHEMA_VERSION) changed = true;
  const now = new Date().toISOString();
  for (const asset of registry.assets) {
    if (asset.type === "video") {
      asset.type = "outro";
      changed = true;
    }
    if (!asset.addedAt) {
      asset.addedAt = now;
      changed = true;
    }
  }
  registry.schemaVersion = ASSETS_SCHEMA_VERSION;
  return { registry, changed };
}

function safeAssetFilename(name: string, source: string): string {
  const slug = name.replace(/[^a-zA-Z0-9._-]/g, "_") || "asset";
  let ext = extname(new URL(source, "file://localhost/").pathname || source).toLowerCase();
  if (!ext) ext = extname(source).toLowerCase();
  return slug + ext;
}

function looksLikeDirectFile(url: string): boolean {
  try {
    const ext = extname(new URL(url).pathname).toLowerCase();
    return IMAGE_EXTS.has(ext) || AUDIO_EXTS.has(ext) || VIDEO_EXTS.has(ext);
  } catch {
    return false;
  }
}

const DOWNLOAD_TIMEOUT_MS = 120_000;

/** Basic SSRF guard: only http(s), and reject obvious localhost/private hosts. */
function assertSafeUrl(url: string): void {
  let parsed: URL;
  try {
    parsed = new URL(url);
  } catch {
    throw new Error(`Invalid URL: ${url}`);
  }
  if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
    throw new Error(`Unsupported URL protocol: ${parsed.protocol}`);
  }
  const host = parsed.hostname.toLowerCase();
  if (
    host === "localhost" ||
    host === "0.0.0.0" ||
    host === "::1" ||
    host.endsWith(".localhost") ||
    /^127\./.test(host) ||
    /^10\./.test(host) ||
    /^192\.168\./.test(host) ||
    /^169\.254\./.test(host) ||
    /^172\.(1[6-9]|2\d|3[01])\./.test(host)
  ) {
    throw new Error(`Refusing to download from a private/loopback host: ${host}`);
  }
}

async function downloadDirect(url: string, destPath: string): Promise<string> {
  const ac = new AbortController();
  const timer = setTimeout(() => ac.abort(), DOWNLOAD_TIMEOUT_MS);
  try {
    const res = await fetch(url, { redirect: "follow", signal: ac.signal });
    if (!res.ok || !res.body) {
      throw new Error(`Download failed (${res.status}) for ${url}`);
    }
    // pipeline rejects on source errors too, so a dropped connection can't
    // throw an uncaught 'error' and crash the server.
    // @ts-expect-error - Web ReadableStream is accepted by Node's fromWeb.
    await pipeline(Readable.fromWeb(res.body), createWriteStream(destPath));
    return destPath;
  } finally {
    clearTimeout(timer);
  }
}

function downloadWithYtDlp(url: string, destDir: string, name: string): Promise<string> {
  const slug = name.replace(/[^a-zA-Z0-9._-]/g, "_") || "asset";
  const args = [
    "-m", "yt_dlp",
    "--js-runtimes", `node:${process.execPath}`,
    "--no-playlist",
    "--format", "bv*[height<=1080]+ba/b[height<=1080]/bv*+ba/b",
    "--merge-output-format", "mp4",
    "--ffmpeg-location", paths.ffmpegPath,
    "--restrict-filenames", "--windows-filenames",
    "--paths", destDir,
    "--output", `${slug}.%(ext)s`,
    "--print", "after_move:podcli-filepath:%(filepath)s",
    url,
  ];
  return new Promise((resolveP, rejectP) => {
    const proc = spawn(paths.pythonPath, args, { env: { ...process.env, PYTHONUNBUFFERED: "1" } });
    let stdout = "";
    let stderr = "";
    let filePath = "";
    proc.stdout.on("data", (c: Buffer) => {
      stdout += c.toString();
      for (const line of c.toString().split(/\r?\n/)) {
        const t = line.trim();
        if (t.startsWith("podcli-filepath:")) filePath = t.slice("podcli-filepath:".length);
      }
    });
    proc.stderr.on("data", (c: Buffer) => (stderr += c.toString()));
    proc.on("error", (err) => rejectP(err));
    proc.on("close", (code) => {
      if (code !== 0) return rejectP(new Error(`yt-dlp failed for ${url}: ${stderr.slice(-800)}`));
      if (!filePath || !existsSync(filePath)) {
        return rejectP(new Error(`yt-dlp reported no output file for ${url}. ${stdout.slice(-400)}`));
      }
      resolveP(filePath);
    });
  });
}

export const _test = { migrate, inferType, safeAssetFilename, looksLikeDirectFile };
