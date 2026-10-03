import { createHash } from "crypto";
import { readFile, rename, mkdir } from "fs/promises";
import { existsSync } from "fs";
import { dirname, resolve } from "path";
import { paths } from "../config/paths.js";
import { writeFileAtomic } from "../utils/atomic-file.js";
import { computeVideoIdentity, type VideoIdentity } from "../utils/video-identity.js";
import { childLogger } from "../utils/logger.js";
import type { EpisodeDecisions, OpenQuestion } from "../models/index.js";

const log = childLogger("episode-state");

type DecisionsFile = Record<string, EpisodeDecisions>;

// One fixed question per decision field, in the order a producer would
// actually need the answers. get_ui_state surfaces these as "open
// questions" so the agent asks each one once per episode, not once per run.
const QUESTIONS: Array<{ field: keyof EpisodeDecisions; question: string }> = [
  { field: "clipCount", question: "How many clips should this episode produce?" },
  { field: "clipDurationRange", question: "What's the target clip duration range (seconds)?" },
  { field: "captionsEnabled", question: "Should clips have captions burned in?" },
  { field: "captionStyle", question: "Which caption style: hormozi, karaoke, subtle, or branded?" },
  { field: "language", question: "What language is this episode in?" },
  { field: "thumbnailsWanted", question: "Do you want thumbnails generated for these clips?" },
  { field: "deliveryTarget", question: "Where are these clips headed: YouTube Shorts, TikTok, Instagram, or just export?" },
];

/**
 * Per-episode decisions, keyed by the source video's identity (path + size +
 * mtime, the same VideoIdentity transcriptVideoMismatch checks against)
 * rather than a content hash: cheap to compute, and good enough to tell
 * "the same file" from "a different recording" without reading the file.
 *
 * Callers resolve to the original video's path before calling in when the
 * current working video is a silence-removed derivative (a new path, size
 * and mtime), so decisions follow the episode across that rewrite instead of
 * keying on a file that only exists for one session.
 *
 * Stored independently of ui-state.json (which is global and gets overwritten
 * by the next episode opened) so these answers outlive the session that
 * recorded them.
 */
export class EpisodeState {
  private filePath: string;

  // Chains every read-modify-write onto the previous one so concurrent
  // record() calls (e.g. two record_decisions calls firing close together)
  // never both read the same snapshot and clobber each other's write.
  private queue: Promise<unknown> = Promise.resolve();

  constructor() {
    this.filePath = paths.episodeDecisions;
  }

  private keyForIdentity(identity: VideoIdentity): string {
    // Same identity helper transcriptVideoMismatch uses, so "the same video"
    // means the same thing everywhere instead of two notions quietly drifting.
    return createHash("sha256")
      .update(`${identity.path}:${identity.size}:${identity.mtimeMs}`)
      .digest("hex")
      .slice(0, 16);
  }

  async keyFor(videoPath: string): Promise<string | null> {
    const identity = computeVideoIdentity(resolve(videoPath));
    if (!identity) return null; // video not found on disk, nothing to key against
    return this.keyForIdentity(identity);
  }

  private async readAll(): Promise<DecisionsFile> {
    if (!existsSync(this.filePath)) return {};
    try {
      const raw = await readFile(this.filePath, "utf-8");
      return JSON.parse(raw) as DecisionsFile;
    } catch (err) {
      // A corrupt file must never silently wipe every episode's decisions.
      // Move it aside so the data isn't lost and start fresh; the next write
      // produces a clean file without touching the renamed original.
      const quarantined = `${this.filePath}.corrupt-${Date.now()}`;
      await rename(this.filePath, quarantined).catch(() => {});
      log.warn("episode-decisions.json was corrupt; quarantined and starting fresh", {
        err: err instanceof Error ? err.message : String(err),
        quarantined,
      });
      return {};
    }
  }

  private async writeAll(data: DecisionsFile): Promise<void> {
    await mkdir(dirname(this.filePath), { recursive: true });
    await writeFileAtomic(this.filePath, JSON.stringify(data, null, 2));
  }

  /** Serializes a read-modify-write step behind whatever is already queued. */
  private enqueue<T>(step: () => Promise<T>): Promise<T> {
    const result = this.queue.then(step, step);
    this.queue = result.catch(() => {});
    return result;
  }

  async get(videoPath: string): Promise<EpisodeDecisions | null> {
    const key = await this.keyFor(videoPath);
    if (!key) return null;
    const all = await this.readAll();
    return all[key] ?? null;
  }

  /** Merge in whichever fields are given; fields already answered are never cleared implicitly. */
  async record(
    videoPath: string,
    decisions: Partial<Omit<EpisodeDecisions, "videoPath" | "fileSize" | "updatedAt">>,
  ): Promise<EpisodeDecisions> {
    const abs = resolve(videoPath);
    return this.enqueue(async () => {
      const identity = computeVideoIdentity(abs);
      if (!identity) throw new Error(`Video not found: ${abs}`); // nothing to key against
      const key = this.keyForIdentity(identity);

      const all = await this.readAll();
      const existing = all[key];
      const merged: EpisodeDecisions = {
        ...existing,
        ...decisions,
        videoPath: abs,
        fileSize: identity.size,
        updatedAt: Date.now(),
      };
      all[key] = merged;
      await this.writeAll(all);
      return merged;
    });
  }

  /** Decisions relevant to the next step that have no answer recorded yet. */
  async openQuestions(videoPath: string): Promise<OpenQuestion[]> {
    const current = await this.get(videoPath);
    return QUESTIONS.filter(({ field }) => current?.[field] === undefined);
  }
}
