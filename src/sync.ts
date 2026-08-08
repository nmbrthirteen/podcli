import { ClipsHistory } from "./services/clips-history.js";
import * as assetSync from "./services/asset-sync.js";
import * as knowledgeSync from "./services/knowledge-sync.js";
import * as cloud from "./services/podcli-cloud.js";

/**
 * `podcli sync` — reconcile this machine with the workspace.
 *
 * Clips, assets, and knowledge each sync automatically at the moments that
 * matter (render, login), so this is the manual catch-up: after working
 * offline, after a teammate changes the brand guide, or on a new machine.
 *
 * Every step is independent and none can fail another — a knowledge conflict
 * must not stop assets from arriving.
 */
async function main(): Promise<number> {
  if (!(await cloud.signedIn())) {
    console.log("Not signed in to podcli Pro. Run `podcli login` first.");
    return 1;
  }

  let problems = 0;

  console.log("Syncing clips...");
  try {
    const { synced, failed } = await new ClipsHistory().backfillCloud();
    console.log(
      synced || failed
        ? `  ${synced} synced${failed ? `, ${failed} failed` : ""}`
        : "  already up to date",
    );
    problems += failed;
  } catch (err) {
    console.log(`  failed: ${err instanceof Error ? err.message : String(err)}`);
    problems++;
  }

  console.log("Syncing assets...");
  try {
    const report = await assetSync.sync();
    const parts = [
      report.uploaded.length && `${report.uploaded.length} uploaded`,
      report.downloaded.length && `${report.downloaded.length} downloaded`,
    ].filter(Boolean);
    console.log(parts.length ? `  ${parts.join(", ")}` : "  already up to date");
    for (const f of report.failed) console.log(`  ${f.name}: ${f.reason}`);
    problems += report.failed.length;
  } catch (err) {
    console.log(`  failed: ${err instanceof Error ? err.message : String(err)}`);
    problems++;
  }

  console.log("Syncing knowledge base...");
  try {
    const report = await knowledgeSync.sync();
    const parts = [
      report.pushed.length && `${report.pushed.length} pushed`,
      report.pulled.length && `${report.pulled.length} pulled`,
    ].filter(Boolean);
    console.log(parts.length ? `  ${parts.join(", ")}` : "  already up to date");

    for (const conflict of report.conflicts) {
      console.log(
        `  conflict: ${conflict.path} — the workspace copy was saved as ` +
        `${conflict.path}.workspace-${conflict.theirVersion}. Merge it, then sync again.`,
      );
    }
    for (const f of report.failed) console.log(`  ${f.path}: ${f.reason}`);
    problems += report.failed.length;
  } catch (err) {
    console.log(`  failed: ${err instanceof Error ? err.message : String(err)}`);
    problems++;
  }

  // Conflicts are not counted as problems: they are a normal outcome that
  // needs a human, not a failure that needs a retry.
  return problems > 0 ? 1 : 0;
}

main().then(
  (code) => process.exit(code),
  (err) => {
    console.error("sync failed:", err instanceof Error ? err.message : String(err));
    process.exit(1);
  },
);
