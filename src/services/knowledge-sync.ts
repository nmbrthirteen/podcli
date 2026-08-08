import { existsSync } from "fs";
import { mkdir, readFile, readdir, writeFile } from "fs/promises";
import { dirname, join, relative, resolve, sep } from "path";
import { paths } from "../config/paths.js";
import * as cloud from "./podcli-cloud.js";

/**
 * Sync .podcli/knowledge/ with the workspace.
 *
 * This is the shared brand brain: voice, banned words, title formulas,
 * thumbnail rules. A new editor joining a team should inherit all of it by
 * signing in, rather than being sent a folder over Slack.
 *
 * Free podcli keeps these files local and fully effective, as it always will.
 */

const STATE_FILE = "knowledge-sync.json";

/**
 * Version of each file as of the last successful sync.
 *
 * Without this there is no way to tell "I edited this" from "they edited this"
 * — both just look like a difference — and every sync would either clobber
 * someone or refuse to do anything.
 */
type SyncState = Record<string, number>;

async function loadState(): Promise<SyncState> {
  try {
    return JSON.parse(await readFile(join(paths.home, STATE_FILE), "utf-8"));
  } catch {
    return {};
  }
}

async function saveState(state: SyncState): Promise<void> {
  await mkdir(paths.home, { recursive: true });
  await writeFile(join(paths.home, STATE_FILE), JSON.stringify(state, null, 2), "utf-8");
}

/**
 * The workspace decides these filenames, so a server that returned
 * `../../.zshrc` would otherwise have this write anywhere the user can.
 */
function insideKnowledge(path: string): string | null {
  const root = resolve(paths.knowledge);
  const target = resolve(root, path);
  return target.startsWith(root + sep) ? target : null;
}

async function localFiles(): Promise<string[]> {
  if (!existsSync(paths.knowledge)) return [];
  // Recursive because a pulled file may live in a subdirectory: the workspace
  // accepts nested paths, and a flat listing would pull `brand/voice.md` once
  // and then never push a local edit to it again.
  const entries = await readdir(paths.knowledge, { recursive: true, withFileTypes: true });
  return entries
    .filter((e) => e.isFile() && e.name.endsWith(".md"))
    .map((e) => relative(paths.knowledge, join(e.parentPath ?? e.path, e.name)))
    .map((p) => p.split(sep).join("/"))
    .sort();
}

export type KnowledgeSyncReport = {
  pushed: string[];
  pulled: string[];
  unchanged: string[];
  conflicts: Array<{ path: string; theirVersion: number }>;
  failed: Array<{ path: string; reason: string }>;
};

export async function sync(): Promise<KnowledgeSyncReport> {
  const report: KnowledgeSyncReport = {
    pushed: [], pulled: [], unchanged: [], conflicts: [], failed: [],
  };
  if (!(await cloud.signedIn())) return report;

  const state = await loadState();
  const remote = new Map((await cloud.listKnowledge()).map((f) => [f.path, f]));
  const local = await localFiles();
  const localSet = new Set(local);

  // Reconciled before anything is pushed. podcli ships default knowledge files,
  // so a machine that has never synced has a full set of boilerplate that would
  // otherwise be pushed straight over the workspace's real one — the server
  // skips its conflict check when no expectedVersion is sent.
  const unresolved = new Set<string>();
  for (const [path, meta] of remote) {
    const target = insideKnowledge(path);
    if (!target) {
      report.failed.push({ path, reason: "path escapes the knowledge folder" });
      unresolved.add(path);
      continue;
    }
    if (state[path] !== undefined) continue;

    try {
      const file = await cloud.getKnowledge(path);
      const version = file.version ?? meta.version;

      if (!localSet.has(path)) {
        await mkdir(dirname(target), { recursive: true });
        await writeFile(target, file.content, "utf-8");
        state[path] = version;
        report.pulled.push(path);
        continue;
      }

      // Both sides have this file and nothing records which came first. Equal
      // content is simply adopted; otherwise the workspace copy lands beside
      // the local one and a human decides.
      const mine = await readFile(target, "utf-8");
      if (mine === file.content) {
        state[path] = version;
        report.unchanged.push(path);
      } else {
        await writeFile(join(paths.knowledge, `${path}.workspace-${version}`),
          file.content, "utf-8");
        report.conflicts.push({ path, theirVersion: version });
      }
      unresolved.add(path);
    } catch (err) {
      report.failed.push({ path, reason: err instanceof Error ? err.message : String(err) });
      unresolved.add(path);
    }
  }

  for (const path of local) {
    if (unresolved.has(path)) continue;
    const content = await readFile(join(paths.knowledge, path), "utf-8");
    const known = state[path];
    try {
      const result = await cloud.putKnowledge(path, content, known);
      if (result.conflict) {
        // Neither copy is discarded. The workspace version is written beside
        // the local one so a human can compare and merge; nobody's work is
        // thrown away by a sync running in the background.
        const version = Number(result.version);
        const suffix = Number.isFinite(version) ? version : "remote";
        const theirs = join(paths.knowledge, `${path}.workspace-${suffix}`);
        await writeFile(theirs, result.content, "utf-8");
        report.conflicts.push({ path, theirVersion: version });
        continue;
      }
      state[path] = result.version;
      if (result.unchanged) report.unchanged.push(path);
      else report.pushed.push(path);
    } catch (err) {
      report.failed.push({ path, reason: err instanceof Error ? err.message : String(err) });
    }
  }


  await saveState(state);
  return report;
}
