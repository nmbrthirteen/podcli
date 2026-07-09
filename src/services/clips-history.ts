import { readFile, writeFile, mkdir, rm, rename } from "fs/promises";
import { existsSync } from "fs";
import { basename, join } from "path";
import { v4 as uuidv4 } from "uuid";
import { paths } from "../config/paths.js";
import { sliceTranscript, sliceWords } from "../utils/transcript.js";
import { isDemoMode, demoClips } from "../ui/demo-fixtures.js";
import type { BatchClipsResult, ClipHistoryEntry, Format, WordTimestamp } from "../models/index.js";

type BatchResultRow = BatchClipsResult["results"][number];

interface BatchRecordContext {
  sourceVideo: string;
  transcriptWords?: WordTimestamp[] | null;
  defaultCaptionStyle?: string;
  defaultCropStrategy?: string;
  defaultFormat?: Format;
  contentTypeFor?: (start: number, end: number) => string | undefined;
}

export interface BatchClipSpec {
  start_second: number;
  end_second: number;
  keep_segments?: Array<{ start: number; end: number }>;
}

export interface BatchRecipeContext {
  transcriptWords?: WordTimestamp[] | null;
  logoPath?: string | null;
  outroPath?: string | null;
  introPath?: string | null;
  cleanFillers?: boolean;
  clipSpecs?: BatchClipSpec[];
}

export class ClipsHistory {
  private historyPath = paths.clipsHistory;
  // Serializes this process's own read-modify-write cycles so concurrent HTTP
  // requests can't lose each other's edits. Cross-process safety (vs the Python
  // CLI) rests on the atomic temp-file rename in save().
  private writeChain: Promise<unknown> = Promise.resolve();

  private async ensureDir() {
    if (!existsSync(paths.history)) {
      await mkdir(paths.history, { recursive: true });
    }
  }

  async load(): Promise<ClipHistoryEntry[]> {
    if (isDemoMode()) return demoClips();
    try {
      if (!existsSync(this.historyPath)) return [];
      const raw = await readFile(this.historyPath, "utf-8");
      return JSON.parse(raw) as ClipHistoryEntry[];
    } catch {
      return [];
    }
  }

  private async save(entries: ClipHistoryEntry[]): Promise<void> {
    if (isDemoMode()) return; // demo fixtures are read-only; never persist to clips.json
    await this.ensureDir();
    // Write to a temp file and atomically rename so a crash or a concurrent
    // reader never sees a half-written clips.json.
    const tmp = `${this.historyPath}.${process.pid}.${uuidv4().slice(0, 8)}.tmp`;
    try {
      await writeFile(tmp, JSON.stringify(entries, null, 2), "utf-8");
      await rename(tmp, this.historyPath);
    } catch (err) {
      await rm(tmp, { force: true }).catch(() => {});
      throw err;
    }
  }

  // Run load → mutate → save as one critical section, queued behind any
  // in-flight mutation. The callback returns the value the caller wants back.
  private mutate<T>(fn: (entries: ClipHistoryEntry[]) => T | Promise<T>): Promise<T> {
    const run = this.writeChain.then(async () => {
      const entries = await this.load();
      const result = await fn(entries);
      await this.save(entries);
      return result;
    });
    this.writeChain = run.then(() => undefined, () => undefined);
    return run;
  }

  async record(entry: Omit<ClipHistoryEntry, "id" | "created_at">): Promise<ClipHistoryEntry> {
    const full: ClipHistoryEntry = {
      ...entry,
      id: uuidv4(),
      created_at: new Date().toISOString(),
    };
    await this.mutate((entries) => {
      entries.push(full);
    });
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
          format: r.format || ctx.defaultFormat || "vertical",
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

  async persistBatchRecipes(
    rows: BatchResultRow[] | undefined,
    recorded: ClipHistoryEntry[],
    ctx: BatchRecipeContext,
  ): Promise<void> {
    if (!rows?.length || !recorded.length) return;
    let recordedIdx = 0;
    for (const row of rows) {
      if (row.status !== "success" || !row.output_path) continue;
      const rec = recorded[recordedIdx++];
      if (!rec) continue;
      const spec =
        typeof row.clip_index === "number" ? ctx.clipSpecs?.[row.clip_index] : undefined;
      await this.persistClipRecipe(rec, {
        transcriptWords: ctx.transcriptWords,
        logoPath: ctx.logoPath,
        outroPath: ctx.outroPath,
        introPath: ctx.introPath,
        cleanFillers: ctx.cleanFillers,
        keepSegments: spec?.keep_segments,
      });
    }
  }

  async persistClipRecipe(
    rec: ClipHistoryEntry,
    ctx: {
      transcriptWords?: WordTimestamp[] | null;
      logoPath?: string | null;
      outroPath?: string | null;
      introPath?: string | null;
      cleanFillers?: boolean;
      keepSegments?: Array<{ start: number; end: number }>;
    },
  ): Promise<void> {
    const words = sliceWords(ctx.transcriptWords ?? [], rec.start_second, rec.end_second);
    await this.saveWords(rec.id, words);
    await this.saveRecipe(rec.id, {
      caption_style: rec.caption_style,
      crop_strategy: rec.crop_strategy,
      format: rec.format || "vertical",
      logo_path: ctx.logoPath ?? rec.logo_path ?? null,
      outro_path: ctx.outroPath ?? rec.outro_path ?? null,
      intro_path: ctx.introPath ?? rec.intro_path ?? null,
      clean_fillers: ctx.cleanFillers ?? false,
      transcript_words: words,
      ...(ctx.keepSegments?.length && { keep_segments: ctx.keepSegments }),
    });
    if (ctx.keepSegments?.length) {
      await this.update(rec.id, { keep_segments: ctx.keepSegments });
    }
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
    cropStrategy: string,
    format: string = "vertical"
  ): Promise<ClipHistoryEntry | null> {
    const entries = await this.load();
    const srcName = basename(sourceVideo);

    return (
      entries.find((e) => {
        if (basename(e.source_video) !== srcName) return false;
        if (e.caption_style !== captionStyle) return false;
        if (e.crop_strategy !== cropStrategy) return false;
        if ((e.format || "vertical") !== format) return false;
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

  // Exact match only — REST routes feed req.params.id straight in, so a loose
  // prefix could target the wrong clip (and an empty prefix the first one).
  async findById(id: string): Promise<ClipHistoryEntry | undefined> {
    if (!id) return undefined;
    const entries = await this.load();
    return entries.find((e) => e.id === id);
  }

  // Resolve a full id or an unambiguous ≥4-char prefix to a full id. For the
  // human-facing MCP tool, where typing a short prefix is convenient.
  async resolveId(idOrPrefix: string): Promise<string | null> {
    if (!idOrPrefix) return null;
    const entries = await this.load();
    if (entries.some((e) => e.id === idOrPrefix)) return idOrPrefix;
    if (idOrPrefix.length < 4) return null;
    const matches = entries.filter((e) => e.id.startsWith(idOrPrefix));
    return matches.length === 1 ? matches[0].id : null;
  }

  async update(id: string, patch: Partial<ClipHistoryEntry>): Promise<ClipHistoryEntry | null> {
    if (!id) return null;
    return this.mutate((entries) => {
      const e = entries.find((x) => x.id === id);
      if (!e) return null;
      Object.assign(e, patch);
      return e;
    });
  }

  // Remove a clip and the artifacts podcli rendered for it (output video,
  // word/recipe/reframe sidecars, thumbnail dir). The source video is never touched.
  // Accepts a full id or an unambiguous prefix (MCP convenience).
  async remove(idOrPrefix: string): Promise<ClipHistoryEntry | null> {
    const id = await this.resolveId(idOrPrefix);
    if (!id) return null;
    // Demo entries are read-only fixtures — never delete their (shipped) artifacts.
    if (isDemoMode()) return (await this.findById(id)) ?? null;
    const entry = await this.mutate((entries) => {
      const idx = entries.findIndex((e) => e.id === id);
      if (idx < 0) return null;
      return entries.splice(idx, 1)[0];
    });
    if (!entry) return null;

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
