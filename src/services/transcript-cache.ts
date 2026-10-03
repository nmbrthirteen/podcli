import { createHash } from "crypto";
import { readFile, writeFile, mkdir, rename } from "fs/promises";
import { existsSync } from "fs";
import { join } from "path";
import { paths } from "../config/paths.js";
import type { TranscriptResult } from "../models/index.js";

/** Cache key parts beyond the file hash. A bare string is shorthand for
 * { engine: string }, the call shape every existing caller already used. */
export interface CacheKeyParts {
  engine?: string;
  model?: string;
  language?: string;
}
export type CacheKey = string | CacheKeyParts;

/**
 * Caches transcripts by file hash so we don't re-transcribe
 * the same podcast when creating multiple clips.
 */
/** Whether a cached transcript actually carries speaker labels.
 *
 * The cache is keyed by file hash and engine, not by diarization, so a request
 * that asks for speaker labels is otherwise answered with the speaker-less
 * transcript that is already on disk. Re-transcribing then looks like a no-op.
 */
export function hasSpeakerLabels(transcript: unknown): boolean {
  const t = transcript as {
    speaker_segments?: Array<{ speaker?: unknown }>;
    words?: Array<{ speaker?: unknown }>;
  } | null;
  if (!t) return false;
  // Read the labels, not the count. A summary can report two speakers while
  // the segments and per-word fields are both empty, and the packer then emits
  // "S?" on every line. Trusting the count would serve that cache back to a
  // request that asked for speakers.
  const labelled = (items?: Array<{ speaker?: unknown }>) =>
    items?.some((i) => typeof i.speaker === "string" && i.speaker.trim().length > 0) ??
    false;
  return labelled(t.speaker_segments) || labelled(t.words);
}

/**
 * Whether a cached transcript is stale with respect to a diarization
 * request: enable_diarization is true, the resolved engine can diarize at
 * all (see engineCanDiarize), and this cache entry never actually attempted
 * it. Re-transcribing a whisper.cpp/omnilingual cache for missing labels
 * would never succeed, they never diarize, so gate on diarization_attempted
 * (backend/services/transcription.py sets it on every branch) rather than
 * hasSpeakerLabels alone, which can't tell "never tried" from "tried and
 * genuinely found one speaker".
 */
export function needsDiarizationRetry(
  cached: unknown,
  enableDiarization: boolean,
  engineCanDiarize: boolean,
): boolean {
  if (!cached || !enableDiarization || !engineCanDiarize) return false;
  const attempted = (cached as { diarization_attempted?: unknown }).diarization_attempted;
  return attempted !== true;
}

export class TranscriptCache {
  private cacheDir: string;

  constructor() {
    this.cacheDir = paths.transcripts;
  }

  private async ensureDir() {
    if (!existsSync(this.cacheDir)) {
      await mkdir(this.cacheDir, { recursive: true });
    }
  }

  /**
   * Hash the first 10MB of the file + file size for a fast unique key.
   */
  async getFileHash(filePath: string): Promise<string> {
    const { createReadStream, statSync } = await import("fs");
    const stat = statSync(filePath);
    const hash = createHash("sha256");

    return new Promise((resolve, reject) => {
      let bytesRead = 0;
      let finalized = false;
      const maxBytes = 10 * 1024 * 1024; // 10MB sample

      const finalize = () => {
        if (finalized) return;
        finalized = true;
        hash.update(`size:${stat.size}`);
        resolve(hash.digest("hex").slice(0, 16));
      };

      const stream = createReadStream(filePath);
      stream.on("data", (chunk) => {
        const buf = Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk as string);
        if (bytesRead < maxBytes) {
          const remaining = maxBytes - bytesRead;
          hash.update(buf.subarray(0, Math.min(buf.length, remaining)));
          bytesRead += buf.length;
        }
        if (bytesRead >= maxBytes) {
          stream.destroy();
        }
      });
      stream.on("end", finalize);
      stream.on("close", finalize);
      stream.on("error", (err) => {
        if (finalized) return;
        finalized = true;
        reject(err);
      });
    });
  }

  /**
   * File hash plus the full {engine, model, language} cache key suffix.
   * The packed markdown view is now keyed the same way the raw JSON cache
   * is (see keySuffix), so a caller that knows the exact combo it wants
   * reads the same filename this class' own set()/write_packed wrote it
   * under.
   */
  async getFileHashForEngine(filePath: string, key?: CacheKey): Promise<string> {
    return `${await this.getFileHash(filePath)}${this.keySuffix(key)}`;
  }

  private engineSuffix(engine?: string): string {
    const value = (engine ?? "").trim().toLowerCase();
    if (["whispercpp", "whisper-cpp", "whisper.cpp", "cpp"].includes(value)) {
      return "-whispercpp";
    }
    if (["assemblyai", "assembly-ai", "aai"].includes(value)) {
      return "-assemblyai";
    }
    if (["omnilingual", "omni", "omnilingual-asr"].includes(value)) {
      return "-omnilingual";
    }
    return "";
  }

  /**
   * Normalize a language tag for use in a cache filename: lowercase, and
   * strip anything outside [a-z0-9-]. The tag comes from caller-supplied
   * input (an MCP tool argument), so without this a value like "en/../x" or
   * one carrying path separators would land in the cache path unescaped.
   * Matches backend/services/transcript_packer.sanitize_language exactly, so
   * the two sides land on the same filename for the same language.
   */
  private sanitizeLanguage(language?: string): string {
    return (language ?? "").trim().toLowerCase().replace(/[^a-z0-9-]/g, "");
  }

  /**
   * Full cache key suffix: engine + model + language. A plain string argument
   * (the pre-existing call shape) is treated as engine-only.
   *
   * base model and auto/empty language contribute no suffix, so a cache
   * written before model/language were tracked (always base, whisper-py, or
   * whatever engine was passed, auto-detected language) still reads back
   * under the same key. Any other model or language gets its own key instead
   * of silently colliding with that implicit default.
   */
  private keySuffix(key?: CacheKey): string {
    const parts = typeof key === "string" ? { engine: key } : key ?? {};
    const engineSuffix = this.engineSuffix(parts.engine);
    const model = (parts.model ?? "").trim().toLowerCase();
    const modelSuffix = model && model !== "base" ? `-m${model}` : "";
    const language = this.sanitizeLanguage(parts.language);
    const languageSuffix = language && language !== "auto" ? `-l${language}` : "";
    return `${engineSuffix}${modelSuffix}${languageSuffix}`;
  }

  async get(filePath: string, key?: CacheKey): Promise<TranscriptResult | null> {
    try {
      const hash = await this.getFileHash(filePath);
      const cachePath = join(this.cacheDir, `${hash}${this.keySuffix(key)}.json`);

      if (!existsSync(cachePath)) return null;

      const data = await readFile(cachePath, "utf-8");
      return JSON.parse(data) as TranscriptResult;
    } catch {
      return null;
    }
  }

  async set(filePath: string, transcript: TranscriptResult, key?: CacheKey): Promise<void> {
    await this.ensureDir();
    const hash = await this.getFileHash(filePath);
    const cachePath = join(this.cacheDir, `${hash}${this.keySuffix(key)}.json`);
    // Write-then-rename: a reader never observes a half-written cache file,
    // and a crash mid-write leaves only an orphaned .tmp, not a corrupt entry.
    const tmpPath = `${cachePath}.${process.pid}.${Date.now()}.tmp`;
    await writeFile(tmpPath, JSON.stringify(transcript), "utf-8");
    await rename(tmpPath, cachePath);
  }

  /**
   * Return the packed markdown view (LLM-readable, ~10x smaller than raw JSON)
   * written by backend/services/transcript_packer.py as a side-effect of
   * transcription. Returns null if not yet generated.
   */
  async getPackedMarkdown(filePath: string, key?: CacheKey): Promise<string | null> {
    try {
      const parts = typeof key === "string" ? { engine: key } : key ?? {};
      if (parts.model !== undefined || parts.language !== undefined) {
        // The caller knows exactly which combo it wants: read that key
        // only, never silently substitute a different model/language's view.
        const hash = await this.getFileHashForEngine(filePath, parts);
        return await this.readPackedByHash(hash);
      }
      // The caller only knows the engine (e.g. get_ui_state, which has a
      // cached transcript object but not the model/language that produced
      // it), fall back to the same deterministic scan the Python side uses
      // in find_cached_transcript_path.
      const hash = await this.getFileHash(filePath);
      const found = await this.findPackedPath(hash, parts.engine);
      return found ? await readFile(found, "utf-8") : null;
    } catch {
      return null;
    }
  }

  /**
   * Port of backend/services/transcript_packer.find_cached_transcript_path,
   * applied to the packed .md directory: prefer the engine-only key, else
   * the most recently modified matching file for this hash+engine.
   */
  private async findPackedPath(hash: string, engine?: string): Promise<string | null> {
    const engineSuffix = this.engineSuffix(engine);
    const exact = join(paths.packed, `${hash}${engineSuffix}.md`);
    if (existsSync(exact)) return exact;
    if (!existsSync(paths.packed)) return null;
    const { readdirSync, statSync } = await import("fs");
    const prefix = `${hash}${engineSuffix}`;
    const tailRe = /^(-m[a-z0-9]+)?(-l[a-z0-9-]+)?\.md$/;
    const candidates = readdirSync(paths.packed)
      .filter((name) => name.startsWith(prefix) && tailRe.test(name.slice(prefix.length)))
      .map((name) => join(paths.packed, name));
    if (!candidates.length) return null;
    return candidates.sort((a, b) => statSync(b).mtimeMs - statSync(a).mtimeMs)[0];
  }

  /**
   * Look up the packed view for a pasted transcript (no source file).
   * Mirrors backend/services handle_parse_transcript's content-hash keying:
   * sha256 of UTF-8 raw text, first 16 hex chars.
   */
  async getPackedMarkdownFromText(rawText: string): Promise<string | null> {
    try {
      const hash = createHash("sha256")
        .update(rawText, "utf-8")
        .digest("hex")
        .slice(0, 16);
      return await this.readPackedByHash(hash);
    } catch {
      return null;
    }
  }

  private async readPackedByHash(hash: string): Promise<string | null> {
    const packedPath = join(paths.packed, `${hash}.md`);
    if (!existsSync(packedPath)) return null;
    return await readFile(packedPath, "utf-8");
  }
}
