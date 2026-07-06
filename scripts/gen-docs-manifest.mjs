import { readFileSync, writeFileSync, readdirSync } from "fs";
import { execSync } from "child_process";
import { join, dirname } from "path";
import { fileURLToPath } from "url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const read = (p) => readFileSync(join(root, p), "utf8");

function countTools(dir) {
  let n = 0;
  for (const entry of readdirSync(join(root, dir), { withFileTypes: true })) {
    const rel = join(dir, entry.name);
    if (entry.isDirectory()) n += countTools(rel);
    else if (entry.name.endsWith(".ts")) n += (read(rel).match(/server\.tool\(/g) || []).length;
  }
  return n;
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

const manifest = {
  version,
  packageVersion: JSON.parse(read("package.json")).version || "",
  mcpToolCount: countTools("src"),
  studioRouteCount: studioRoutes.length,
  studioRoutes,
};

writeFileSync(join(root, "docs-manifest.json"), JSON.stringify(manifest, null, 2) + "\n");
console.log("wrote docs-manifest.json:", JSON.stringify(manifest));
