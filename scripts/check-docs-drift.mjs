import { readFileSync } from "fs";
import { join, dirname } from "path";
import { fileURLToPath } from "url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const read = (p) => readFileSync(join(root, p), "utf8");

let manifest;
try {
  manifest = JSON.parse(read("docs-manifest.json"));
} catch {
  console.error("docs-manifest.json not found. Run: node scripts/gen-docs-manifest.mjs");
  process.exit(1);
}

const readme = read("README.md");
const errors = [];
const warnings = [];

if (/single-page/i.test(readme) && manifest.studioRouteCount > 1) {
  errors.push(`README calls the studio "single-page" but it has ${manifest.studioRouteCount} routes.`);
}

const toolClaims = [...new Set([...readme.matchAll(/(\d+)\s+(?:MCP\s+)?tools/gi)].map((m) => Number(m[1])))];
for (const claim of toolClaims) {
  if (claim !== manifest.mcpToolCount) {
    warnings.push(`README mentions "${claim} tools" but src registers ${manifest.mcpToolCount} (server.tool calls).`);
  }
}

if (manifest.packageVersion && manifest.version && !manifest.version.endsWith(manifest.packageVersion)) {
  warnings.push(`package.json version ${manifest.packageVersion} lags the latest tag ${manifest.version}.`);
}

for (const w of warnings) console.warn("warning:", w);

if (errors.length) {
  console.error("Docs drift detected:");
  for (const e of errors) console.error("  -", e);
  process.exit(1);
}

console.log(`Docs in sync: ${manifest.mcpToolCount} tools, ${manifest.studioRouteCount} studio routes, ${manifest.version}.`);
