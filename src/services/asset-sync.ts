import { existsSync } from "fs";
import { mkdir, readFile, writeFile } from "fs/promises";
import { basename, join } from "path";
import { paths } from "../config/paths.js";
import { AssetManager, inferType } from "./asset-manager.js";
import * as cloud from "./podcli-cloud.js";
import type { Asset, AssetType } from "../models/index.js";

/**
 * Two-way sync between .podcli/assets/ and the workspace asset library.
 *
 * Local assets keep working untouched for everyone; this only runs for
 * signed-in users. The point is that a second machine, or a teammate, gets the
 * show's logo and outro without anyone emailing files around.
 */

const SYNCABLE_KINDS: Record<AssetType, string> = {
  logo: "logo",
  intro: "intro",
  outro: "outro",
  music: "music",
} as Record<AssetType, string>;

function cloudKind(type: AssetType): string {
  return SYNCABLE_KINDS[type] ?? "other";
}

export type SyncReport = {
  uploaded: string[];
  downloaded: string[];
  skipped: string[];
  failed: Array<{ name: string; reason: string }>;
};

const empty = (): SyncReport => ({ uploaded: [], downloaded: [], skipped: [], failed: [] });

/**
 * Push local assets the workspace doesn't have.
 *
 * The server discards an upload whose checksum it already holds, but only after
 * receiving it. Comparing first keeps a 200 MB intro off the wire on every
 * sync; if the listing cannot be fetched, everything is uploaded as before.
 */
export async function push(): Promise<SyncReport> {
  const report = empty();
  if (!(await cloud.signedIn())) return report;

  const manager = new AssetManager();
  const registry = await manager.load();

  let held = new Map<string, string>();
  try {
    held = new Map((await cloud.listAssets()).map((a) => [a.name, a.checksum]));
  } catch {
    // Fall through: an unreadable listing must not stop the push.
  }

  for (const asset of registry.assets) {
    if (!existsSync(asset.path)) {
      report.skipped.push(asset.name);
      continue;
    }
    try {
      const body = await readFile(asset.path);
      if (held.get(asset.name) === cloud.checksum(body)) {
        report.skipped.push(asset.name);
        continue;
      }
      const result = await cloud.uploadAsset(
        asset.name,
        cloudKind(asset.type),
        body,
        Boolean(asset.default),
      );
      if (result?.unchanged) report.skipped.push(asset.name);
      else report.uploaded.push(asset.name);
    } catch (err) {
      report.failed.push({
        name: asset.name,
        reason: err instanceof Error ? err.message : String(err),
      });
    }
  }
  return report;
}

/**
 * Pull workspace assets this machine is missing.
 *
 * Files land in .podcli/assets/ and are registered locally, so every existing
 * code path — rendering, presets, the studio — finds them exactly where it
 * already looks. Nothing downstream needs to know they came from a server.
 */
export async function pull(): Promise<SyncReport> {
  const report = empty();
  if (!(await cloud.signedIn())) return report;

  const manager = new AssetManager();
  const registry = await manager.load();
  const known = new Map(registry.assets.map((a) => [a.name, a]));

  let remote: Awaited<ReturnType<typeof cloud.listAssets>>;
  try {
    remote = await cloud.listAssets();
  } catch (err) {
    report.failed.push({
      name: "(list)",
      reason: err instanceof Error ? err.message : String(err),
    });
    return report;
  }

  const dir = join(paths.assets, "shared");
  for (const entry of remote) {
    const local = known.get(entry.name);
    // A local file that already exists wins: the user's own copy is never
    // silently overwritten by the workspace version.
    if (local && existsSync(local.path)) {
      report.skipped.push(entry.name);
      continue;
    }
    try {
      const body = await cloud.downloadAsset(entry.id);
      await mkdir(dir, { recursive: true });
      // The whole name, flattened: two workspace assets called `intro/logo.png`
      // and `outro/logo.png` both end in `logo.png`, and the second download
      // would land on the first and leave two registry entries pointing at one
      // file.
      const target = join(dir, entry.name.replace(/[\\/]+/g, "-"));
      await writeFile(target, body);
      // The workspace already knows what this is. Re-deriving the type from the
      // extension turns a `music` asset stored as .mp4 into a video.
      await manager.register(entry.name, target, assetType(entry.kind) ?? inferType(target));
      report.downloaded.push(entry.name);
    } catch (err) {
      report.failed.push({
        name: entry.name,
        reason: err instanceof Error ? err.message : String(err),
      });
    }
  }
  return report;
}

const ASSET_TYPES: readonly AssetType[] = [
  "logo", "outro", "intro", "music", "video", "image",
];

/** The workspace's own kind, when it is one this app models. */
function assetType(kind: string | undefined): AssetType | null {
  return kind && (ASSET_TYPES as readonly string[]).includes(kind)
    ? (kind as AssetType)
    : null;
}

export async function sync(): Promise<SyncReport> {
  const up = await push();
  const down = await pull();
  return {
    uploaded: up.uploaded,
    downloaded: down.downloaded,
    skipped: [...up.skipped, ...down.skipped],
    failed: [...up.failed, ...down.failed],
  };
}
