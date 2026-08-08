import { readFile, writeFile, mkdir, rm } from "fs/promises";
import { existsSync } from "fs";
import { basename, join } from "path";
import { v4 as uuidv4 } from "uuid";
import { paths } from "../config/paths.js";
import { writeFileAtomic } from "../utils/atomic-file.js";
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
  private syncing = new Set<string>();

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
    await writeFileAtomic(this.historyPath, JSON.stringify(entries, null, 2));
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
    void this.syncToCloud(full);
    return full;
  }

  /**
   * Mirror a rendered clip to the workspace, for signed-in users.
   *
   * Deliberately not awaited and unable to throw: a clip that rendered
   * successfully must be recorded locally whether or not a server was reachable.
   * The local history file remains the source of truth; this is a copy.
   *
   * Clips that fail to sync are left marked so a later sweep can backfill them —
   * the performance model wants the whole history, not the part that happened to
   * have a working network.
   */
  private async syncToCloud(entry: ClipHistoryEntry): Promise<void> {
    // record() starts this in the background, so `podcli sync` can reach the
    // same entry while it is still in flight and register the clip twice.
    if (this.syncing.has(entry.id)) return;
    this.syncing.add(entry.id);
    try {
      const cloud = await import("./podcli-cloud.js");
      if (!(await cloud.signedIn())) return;

      const source = entry.source_video;
      if (!source) return;

      const clipId = entry.cloud_id ?? (await cloud.registerClip({
        sourceHash: await cloud.sourceHash(source),
        episodeTitle: basename(source),
        title: entry.title,
        startSecond: entry.start_second,
        endSecond: entry.end_second,
        durationSec: entry.duration,
        contentType: entry.content_type,
        captionStyle: entry.caption_style,
        aspectRatio: entry.format,
        transcriptSlice: entry.transcript_slice,
      }))?.id;
      if (!clipId) return;

      await this.update(entry.id, { cloud_id: clipId, cloud_synced: true });

      // Metadata alone leaves a share link with nothing to play, so the
      // rendered file follows it. Uploaded once: the server keeps the first
      // copy and answers `unchanged` after that.
      if (!entry.cloud_video_uploaded && existsSync(entry.output_path)) {
        const uploaded = await cloud.uploadClipVideo(clipId, entry.output_path);
        if (uploaded) await this.update(entry.id, { cloud_video_uploaded: true });
      }
    } catch {
      await this.update(entry.id, { cloud_synced: false }).catch(() => {});
    } finally {
      this.syncing.delete(entry.id);
    }
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
    const changed = await this.mutate((entries) => {
      const e = entries.find((x) => x.id === id);
      if (!e) return null;
      const before = e.title;
      Object.assign(e, patch);
      return { entry: e, previousTitle: before };
    });
    if (!changed) return null;

    // A human rewriting a generated title is the clearest taste signal podcli
    // gets — it says what the model produced and what a person preferred
    // instead. Reported only when the title actually changed, so the sync
    // bookkeeping in syncToCloud can't trigger it.
    if (patch.title !== undefined && patch.title !== changed.previousTitle) {
      void this.reportEvent(changed.entry, "title_edited", changed.previousTitle, patch.title);
    }
    return changed.entry;
  }

  /** Best-effort; never blocks or fails the edit that produced it. */
  private async reportEvent(
    entry: ClipHistoryEntry,
    kind: "title_edited" | "discarded" | "thumbnail_regenerated",
    before?: string,
    after?: string,
  ): Promise<void> {
    if (!entry.cloud_id) return;
    try {
      const cloud = await import("./podcli-cloud.js");
      if (!(await cloud.signedIn())) return;
      await cloud.logClipEvent(entry.cloud_id, kind, before, after);
    } catch {
      // The signal is nice to have, not worth surfacing an error over.
    }
  }

  /**
   * Push clips that never reached the workspace.
   *
   * Covers two cases that both matter: a render that happened while the network
   * was down, and — more importantly — everything rendered *before* the user
   * subscribed. A new Pro user should start with their back catalogue behind the
   * performance model, not an empty history.
   */
  async backfillCloud(limit = 200): Promise<{ synced: number; failed: number }> {
    const cloud = await import("./podcli-cloud.js");
    if (!(await cloud.signedIn())) return { synced: 0, failed: 0 };

    // A clip whose source video has been moved or deleted can never be hashed,
    // so it can never sync. Skipping it keeps `podcli sync` quiet; counting it
    // as a failure would report the same unfixable number on every run until
    // people stopped reading the output.
    const pending = (await this.load())
      .filter((e) => e.source_video && existsSync(e.source_video))
      .filter((e) => !e.cloud_id || !e.cloud_video_uploaded)
      .slice(0, limit);

    let synced = 0;
    let failed = 0;
    for (const entry of pending) {
      try {
        await this.syncToCloud(entry);
        const after = await this.findById(entry.id);
        if (after?.cloud_id) synced++;
        else failed++;
      } catch {
        failed++;
      }
    }
    return { synced, failed };
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
