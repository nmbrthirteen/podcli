import { createHash } from "crypto";
import { readFile, stat, mkdir } from "fs/promises";
import { existsSync } from "fs";
import { dirname, resolve } from "path";
import { paths } from "../config/paths.js";
import { writeFileAtomic } from "../utils/atomic-file.js";
import type { EpisodeDecisions, OpenQuestion } from "../models/index.js";

type DecisionsFile = Record<string, EpisodeDecisions>;

// One fixed question per decision field, in the order a producer would
// actually need the answers. get_ui_state surfaces these as "open
// questions" so the agent asks each one once per episode, not once per run.
const QUESTIONS: Array<{ field: keyof EpisodeDecisions; question: string }> = [
  { field: "clipCount", question: "How many clips should this episode produce?" },
  { field: "clipDurationRange", question: "What's the target clip duration range (seconds)?" },
  { field: "captionsEnabled", question: "Should clips have captions burned in?" },
  { field: "captionStyle", question: "Which caption style — hormozi, karaoke, subtle, or branded?" },
  { field: "language", question: "What language is this episode in?" },
  { field: "thumbnailsWanted", question: "Do you want thumbnails generated for these clips?" },
  { field: "deliveryTarget", question: "Where are these clips headed — YouTube Shorts, TikTok, Instagram, or just export?" },
];

/**
 * Per-episode decisions, keyed by the source video's identity (path + size)
 * rather than a content hash: cheap to compute, and good enough to tell
 * "the same file" from "a different recording" without reading the file.
 *
 * Stored independently of ui-state.json (which is global and gets overwritten
 * by the next episode opened) so these answers outlive the session that
 * recorded them.
 */
export class EpisodeState {
  private filePath: string;

  constructor() {
    this.filePath = paths.episodeDecisions;
  }

  async keyFor(videoPath: string): Promise<string | null> {
    const abs = resolve(videoPath);
    try {
      const info = await stat(abs);
      return createHash("sha256").update(`${abs}:${info.size}`).digest("hex").slice(0, 16);
    } catch {
      return null; // video not found on disk — nothing to key against
    }
  }

  private async readAll(): Promise<DecisionsFile> {
    if (!existsSync(this.filePath)) return {};
    try {
      const raw = await readFile(this.filePath, "utf-8");
      return JSON.parse(raw) as DecisionsFile;
    } catch {
      return {};
    }
  }

  private async writeAll(data: DecisionsFile): Promise<void> {
    await mkdir(dirname(this.filePath), { recursive: true });
    await writeFileAtomic(this.filePath, JSON.stringify(data, null, 2));
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
    const info = await stat(abs); // throws if the video doesn't exist — nothing to key against
    const key = createHash("sha256").update(`${abs}:${info.size}`).digest("hex").slice(0, 16);

    const all = await this.readAll();
    const existing = all[key];
    const merged: EpisodeDecisions = {
      ...existing,
      ...decisions,
      videoPath: abs,
      fileSize: info.size,
      updatedAt: Date.now(),
    };
    all[key] = merged;
    await this.writeAll(all);
    return merged;
  }

  /** Decisions relevant to the next step that have no answer recorded yet. */
  async openQuestions(videoPath: string): Promise<OpenQuestion[]> {
    const current = await this.get(videoPath);
    return QUESTIONS.filter(({ field }) => current?.[field] === undefined);
  }
}
