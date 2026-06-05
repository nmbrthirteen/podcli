import { readFile, writeFile, mkdir, rm } from "fs/promises";
import { existsSync } from "fs";
import { basename, join } from "path";
import { v4 as uuidv4 } from "uuid";
import { paths } from "../config/paths.js";
import { sliceTranscript } from "../utils/transcript.js";
import type { BatchClipsResult, ClipHistoryEntry, WordTimestamp } from "../models/index.js";

type BatchResultRow = BatchClipsResult["results"][number];

interface BatchRecordContext {
  sourceVideo: string;
  transcriptWords?: WordTimestamp[] | null;
  defaultCaptionStyle?: string;
  defaultCropStrategy?: string;
  contentTypeFor?: (start: number, end: number) => string | undefined;
}

export class ClipsHistory {
  private historyPath = paths.clipsHistory;

  private async ensureDir() {
    if (!existsSync(paths.history)) {
      await mkdir(paths.history, { recursive: true });
    }
  }

  async load(): Promise<ClipHistoryEntry[]> {
    try {
      if (!existsSync(this.historyPath)) return [];
      const raw = await readFile(this.historyPath, "utf-8");
      return JSON.parse(raw) as ClipHistoryEntry[];
    } catch {
      return [];
    }
  }

  private async save(entries: ClipHistoryEntry[]): Promise<void> {
    await this.ensureDir();
    await writeFile(this.historyPath, JSON.stringify(entries, null, 2), "utf-8");
  }

  async record(entry: Omit<ClipHistoryEntry, "id" | "created_at">): Promise<ClipHistoryEntry> {
    const entries = await this.load();
    const full: ClipHistoryEntry = {
      ...entry,
      id: uuidv4(),
      created_at: new Date().toISOString(),
    };
    entries.push(full);
    await this.save(entries);
    return full;
  }

  // Persist every successful row of a batch render. Single source of truth for
  // turning backend batch results into history entries — callers used to inline
  // this loop, drifting on defaults and on which fields got recorded.
  async recordBatchResults(
    results: BatchResultRow[] | undefined,
    ctx: BatchRecordContext,
  ): Promise<ClipHistoryEntry[]> {
    if (!results) return [];
    const recorded: ClipHistoryEntry[] = [];
    for (const r of results) {
      if (r.status !== "success" || !r.output_path) continue;
      const start = r.start_second || 0;
      const end = r.end_second || 0;
      recorded.push(
        await this.record({
          source_video: ctx.sourceVideo,
          start_second: start,
          end_second: end,
          caption_style: r.caption_style || ctx.defaultCaptionStyle || "hormozi",
          crop_strategy: r.crop_strategy || ctx.defaultCropStrategy || "speaker",
          title: r.title || "clip",
          output_path: r.output_path,
          file_size_mb: r.file_size_mb || 0,
          duration: r.duration || 0,
          content_type: ctx.contentTypeFor?.(start, end),
          transcript_slice: sliceTranscript(ctx.transcriptWords, start, end),
        }),
      );
    }
    return recorded;
  }

  /**
   * Check if a clip with the same source, time range, and style already exists.
   * Uses basename matching for source video and ±2s tolerance on time range.
   */
  async findDuplicate(
    sourceVideo: string,
    startSecond: number,
    endSecond: number,
    captionStyle: string,
    cropStrategy: string
  ): Promise<ClipHistoryEntry | null> {
    const entries = await this.load();
    const srcName = basename(sourceVideo);

    return (
      entries.find((e) => {
        if (basename(e.source_video) !== srcName) return false;
        if (e.caption_style !== captionStyle) return false;
        if (e.crop_strategy !== cropStrategy) return false;
        if (Math.abs(e.start_second - startSecond) > 2) return false;
        if (Math.abs(e.end_second - endSecond) > 2) return false;
        // Check output still exists
        return existsSync(e.output_path);
      }) || null
    );
  }

  async list(limit = 50): Promise<ClipHistoryEntry[]> {
    const entries = await this.load();
    return entries.slice(-limit).reverse();
  }

  async findById(idOrPrefix: string): Promise<ClipHistoryEntry | undefined> {
    const entries = await this.load();
    return entries.find((e) => e.id === idOrPrefix || e.id.startsWith(idOrPrefix));
  }

  async update(id: string, patch: Partial<ClipHistoryEntry>): Promise<ClipHistoryEntry | null> {
    const entries = await this.load();
    const e = entries.find((x) => x.id === id || x.id.startsWith(id));
    if (!e) return null;
    Object.assign(e, patch);
    await this.save(entries);
    return e;
  }

  // Remove a clip and the artifacts podcli rendered for it (output video,
  // word/recipe/reframe sidecars, thumbnail dir). The source video is never touched.
  async remove(idOrPrefix: string): Promise<ClipHistoryEntry | null> {
    const entries = await this.load();
    const entry = entries.find((e) => e.id === idOrPrefix || e.id.startsWith(idOrPrefix));
    if (!entry) return null;
    await this.save(entries.filter((e) => e.id !== entry.id));

    const artifacts = [
      this.wordsPath(entry.id),
      this.recipePath(entry.id),
      this.reframePath(entry.id),
      entry.output_path,
    ];
    await Promise.all(
      artifacts.map((p) => (p ? rm(p, { force: true }) : Promise.resolve())),
    );
    await rm(join(paths.output, "thumbnails", entry.id), { recursive: true, force: true });
    return entry;
  }

  async getBySource(videoPath: string): Promise<ClipHistoryEntry[]> {
    const entries = await this.load();
    const srcName = basename(videoPath);
    return entries.filter((e) => basename(e.source_video) === srcName).reverse();
  }

  // Word timings are kept in a sidecar (not in clips.json) so re-rendering a
  // clip can re-burn captions without bloating the history file.
  private wordsPath(id: string): string {
    return join(paths.history, "words", `${id}.json`);
  }

  async saveWords(id: string, words: unknown[]): Promise<void> {
    if (!words || words.length === 0) return;
    await mkdir(join(paths.history, "words"), { recursive: true });
    await writeFile(this.wordsPath(id), JSON.stringify(words), "utf-8");
  }

  async loadWords(id: string): Promise<unknown[]> {
    try {
      return JSON.parse(await readFile(this.wordsPath(id), "utf-8"));
    } catch {
      return [];
    }
  }

  // Full render recipe (logo/outro/captions/fillers/segments/words) so a clip
  // can be re-rendered faithfully — e.g. after a manual reframe.
  private recipePath(id: string): string {
    return join(paths.history, "recipes", `${id}.json`);
  }

  async saveRecipe(id: string, recipe: Record<string, unknown>): Promise<void> {
    await mkdir(join(paths.history, "recipes"), { recursive: true });
    await writeFile(this.recipePath(id), JSON.stringify(recipe), "utf-8");
  }

  async loadRecipe(id: string): Promise<Record<string, unknown> | null> {
    try {
      return JSON.parse(await readFile(this.recipePath(id), "utf-8"));
    } catch {
      return null;
    }
  }

  // Reframe editor state (keyframes + trim) so reopening shows prior edits.
  private reframePath(id: string): string {
    return join(paths.history, "reframe", `${id}.json`);
  }

  async saveReframe(id: string, state: Record<string, unknown>): Promise<void> {
    await mkdir(join(paths.history, "reframe"), { recursive: true });
    await writeFile(this.reframePath(id), JSON.stringify(state), "utf-8");
  }

  async loadReframe(id: string): Promise<Record<string, unknown> | null> {
    try {
      return JSON.parse(await readFile(this.reframePath(id), "utf-8"));
    } catch {
      return null;
    }
  }
}
