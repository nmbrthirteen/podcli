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

try {
  const llms = read("llms.txt");
  const llmsCount = [...llms.matchAll(/(\d+) tools/g)].map((m) => Number(m[1]));
  for (const claim of llmsCount) {
    if (claim !== manifest.mcpToolCount) {
      errors.push(`llms.txt mentions "${claim} tools" but src registers ${manifest.mcpToolCount}. Re-run: node scripts/gen-docs-manifest.mjs`);
    }
  }
  const listed = [...llms.matchAll(/^- ([a-z][a-z0-9_]+)$/gm)].map((m) => m[1]).sort();
  if (Array.isArray(manifest.mcpToolNames) && listed.length) {
    const registered = [...manifest.mcpToolNames].sort();
    const missing = registered.filter((t) => !listed.includes(t));
    const stale = listed.filter((t) => !registered.includes(t));
    if (missing.length) errors.push(`llms.txt is missing tools: ${missing.join(", ")}`);
    if (stale.length) errors.push(`llms.txt lists unregistered tools: ${stale.join(", ")}`);
  }
} catch {
  errors.push("llms.txt not found. Run: node scripts/gen-docs-manifest.mjs");
}

if (/single-page/i.test(readme) && manifest.studioRouteCount > 1) {
  errors.push(`README calls the studio "single-page" but it has ${manifest.studioRouteCount} routes.`);
}

const toolClaims = [...new Set([...readme.matchAll(/(\d+)\s+(?:MCP\s+)?tools/gi)].map((m) => Number(m[1])))];
for (const claim of toolClaims) {
  if (claim !== manifest.mcpToolCount) {
    warnings.push(`README mentions "${claim} tools" but src registers ${manifest.mcpToolCount} (server.tool calls).`);
  }
}

const claudeMd = read("CLAUDE.md");
const toolSection = claudeMd.split(/^## MCP tools/m)[1]?.split(/^## /m)[0] ?? "";
const documentedTools = [...toolSection.matchAll(/^\| `([a-z0-9_]+)` \|/gm)].map((m) => m[1]).sort();
if (Array.isArray(manifest.mcpToolNames)) {
  const registered = [...manifest.mcpToolNames].sort();
  const missing = registered.filter((t) => !documentedTools.includes(t));
  const stale = documentedTools.filter((t) => !registered.includes(t));
  if (missing.length) errors.push(`CLAUDE.md MCP tool table is missing: ${missing.join(", ")}`);
  if (stale.length) errors.push(`CLAUDE.md MCP tool table lists unregistered tools: ${stale.join(", ")}`);
  if (documentedTools.length !== registered.length) {
    errors.push(`CLAUDE.md documents ${documentedTools.length} MCP tools but src registers ${registered.length}.`);
  }
  const claudeClaims = [...new Set([...claudeMd.matchAll(/All (\d+) tools/g)].map((m) => Number(m[1])))];
  for (const claim of claudeClaims) {
    if (claim !== registered.length) {
      errors.push(`CLAUDE.md claims "${claim} tools" but src registers ${registered.length}.`);
    }
  }
} else {
  errors.push("docs-manifest.json has no mcpToolNames. Re-run: node scripts/gen-docs-manifest.mjs");
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
