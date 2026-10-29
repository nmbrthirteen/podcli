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
    // server.registerTool (the non-deprecated API, needed for a strict
    // input schema that server.tool's raw-shape shorthand can't express)
    // takes its name the same way: as the first positional argument.
    for (const m of src.matchAll(/server\.(?:registerTool|tool)\(\s*(?:"([^"]+)"|(\w+)\.name)/g)) {
      registrations += 1;
      if (m[1]) names.push(m[1]);
      else if (defs.has(m[2])) names.push(defs.get(m[2]));
    }
  }
  if (names.length !== registrations) {
    console.error(`resolved ${names.length} tool names for ${registrations} server.tool(/server.registerTool( registrations`);
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

const toolList = mcpToolNames.map((n) => `- ${n}`).join("\n");
const llms = `# podcli

Open-source AI podcast clipper. Turn a long episode into short clips with face tracking and burned-in captions. Drive it from the CLI, a web studio, or your coding agent.

Install: \`curl -fsSL https://podcli.com/install.sh | sh\`
Windows: \`irm https://podcli.com/install.ps1 | iex\`

Version: ${manifest.packageVersion}
License: AGPL-3.0. Commercial licensing: siradze@nikusha.me
Repo: https://github.com/nmbrthirteen/podcli
Site: https://podcli.com

## Platforms

macOS (Apple Silicon), Linux (x64 and arm64), Windows (x64). Intel Macs aren't supported.

## Auth for AI scoring

Clip scoring uses Claude Code or Codex login, or an Anthropic API key; without those it falls back to heuristics. Speaker diarization needs HF_TOKEN. The local engine is free; podcli Pro is an optional cloud editor and account (\`podcli login\`).

## First run

\`podcli process episode.mp4\` (or studio / transcribe / multicam) provisions the engine on first run.
\`podcli mcp\` does not — stdout is JSON-RPC. Run \`podcli setup\` once before pointing an agent at it.
\`--help\` and \`--version\` never download anything.

## Agent CLI

\`\`\`
podcli process episode.mp4 --json --yes
podcli doctor --json
\`\`\`

JSON on stdout; progress and logs on stderr. \`-y/--yes\` skips prompts. \`NO_COLOR=1\` or a non-TTY turns off colour and spinners.

Exit codes: 0 success, 1 runtime error, 2 usage, 130 interrupted.

## MCP

${mcpToolNames.length} tools. \`podcli mcp\` on stdio; \`podcli mcp install\` registers with Claude Code and Codex.

${toolList}

\`rank_clip_candidates\` returns a shortlist so you do not have to read the whole transcript.
\`process_episode\` runs the pipeline as one job; poll it with \`job_status\`.

## Also

Clips (9:16 / 16:9 / 1:1) and full-episode multicam edit. Knowledge base at .podcli/knowledge/. PodStack slash commands in .claude/commands/.
`;
writeFileSync(join(root, "llms.txt"), llms);
console.log("wrote llms.txt");
