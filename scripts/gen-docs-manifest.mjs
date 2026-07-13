import { readFileSync, writeFileSync, readdirSync } from "fs";
import { execSync } from "child_process";
import { join, dirname } from "path";
import { fileURLToPath } from "url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const read = (p) => readFileSync(join(root, p), "utf8");

function tsSources(dir, acc = []) {
  for (const entry of readdirSync(join(root, dir), { withFileTypes: true })) {
    const rel = join(dir, entry.name);
    if (entry.isDirectory()) tsSources(rel, acc);
    else if (entry.name.endsWith(".ts")) acc.push(read(rel));
  }
  return acc;
}

function collectToolNames(sources) {
  const defs = new Map();
  const names = [];
  let registrations = 0;
  for (const src of sources) {
    for (const m of src.matchAll(/const\s+(\w+)\s*=\s*\{\s*name:\s*"([^"]+)"/g)) {
      defs.set(m[1], m[2]);
    }
  }
  for (const src of sources) {
    for (const m of src.matchAll(/server\.tool\(\s*(?:"([^"]+)"|(\w+)\.name)/g)) {
      registrations += 1;
      if (m[1]) names.push(m[1]);
      else if (defs.has(m[2])) names.push(defs.get(m[2]));
    }
  }
  if (names.length !== registrations) {
    console.error(`resolved ${names.length} tool names for ${registrations} server.tool( registrations`);
    process.exit(1);
  }
  return names.sort();
}

const mainTsx = read("src/ui/client/main.tsx");
const studioRoutes = [...mainTsx.matchAll(/<Route\s+path="([^"]+)"\s+element=\{<(\w+)/g)]
  .filter((m) => !/Navigate/.test(m[0]))
  .map((m) => ({ path: m[1], component: m[2] }));

let version = "";
try {
  version = execSync("git describe --tags --abbrev=0", { cwd: root }).toString().trim();
} catch {
  version = JSON.parse(read("package.json")).version || "";
}

const mcpToolNames = collectToolNames(tsSources("src"));

const manifest = {
  version,
  packageVersion: JSON.parse(read("package.json")).version || "",
  mcpToolCount: mcpToolNames.length,
  mcpToolNames,
  studioRouteCount: studioRoutes.length,
  studioRoutes,
};

writeFileSync(join(root, "docs-manifest.json"), JSON.stringify(manifest, null, 2) + "\n");
console.log("wrote docs-manifest.json:", JSON.stringify(manifest));
