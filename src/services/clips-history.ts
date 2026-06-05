import { readFile, writeFile, mkdir } from "fs/promises";
import { existsSync } from "fs";
import { basename, join } from "path";
import { v4 as uuidv4 } from "uuid";
import { paths } from "../config/paths.js";
import type { ClipHistoryEntry } from "../models/index.js";

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

  async update(id: string, patch: Partial<ClipHistoryEntry>): Promise<ClipHistoryEntry | null> {
    const entries = await this.load();
    const e = entries.find((x) => x.id === id || x.id.startsWith(id));
    if (!e) return null;
    Object.assign(e, patch);
    await this.save(entries);
    return e;
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
