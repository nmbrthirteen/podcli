import { createHash } from "crypto";
import { readFile, writeFile, mkdir } from "fs/promises";
import { existsSync } from "fs";
import { join } from "path";
import { paths } from "../config/paths.js";
import type { TranscriptResult } from "../models/index.js";

/**
 * Caches transcripts by file hash so we don't re-transcribe
 * the same podcast when creating multiple clips.
 */
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

  async get(filePath: string): Promise<TranscriptResult | null> {
    try {
      const hash = await this.getFileHash(filePath);
      const cachePath = join(this.cacheDir, `${hash}.json`);

      if (!existsSync(cachePath)) return null;

      const data = await readFile(cachePath, "utf-8");
      return JSON.parse(data) as TranscriptResult;
    } catch {
      return null;
    }
  }

  async set(filePath: string, transcript: TranscriptResult): Promise<void> {
    await this.ensureDir();
    const hash = await this.getFileHash(filePath);
    const cachePath = join(this.cacheDir, `${hash}.json`);
    await writeFile(cachePath, JSON.stringify(transcript), "utf-8");
  }

  /**
   * Return the packed markdown view (LLM-readable, ~10x smaller than raw JSON)
   * written by backend/services/transcript_packer.py as a side-effect of
   * transcription. Returns null if not yet generated.
   */
  async getPackedMarkdown(filePath: string): Promise<string | null> {
    try {
      const hash = await this.getFileHash(filePath);
      return await this.readPackedByHash(hash);
    } catch {
      return null;
    }
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
