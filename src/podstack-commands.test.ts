import { describe, it, expect } from "vitest";
import { readFileSync, readdirSync } from "fs";
import { dirname, join, resolve } from "path";
import { fileURLToPath } from "url";
import { createServer } from "./server.js";

// Catches the class of bug fixed alongside this test: a PodStack command
// frontmatter or body references an MCP tool name that doesn't match what
// the server actually registers (e.g. a renamed tool, or a typo).
const repoRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const commandsDir = join(repoRoot, ".claude", "commands");

function registeredToolNames(): Set<string> {
  const server = createServer() as unknown as { _registeredTools: Record<string, unknown> };
  return new Set(Object.keys(server._registeredTools));
}

function parseFrontmatter(source: string): { allowedTools: string[]; body: string } {
  const match = source.match(/^---\n([\s\S]*?)\n---\n([\s\S]*)$/);
  if (!match) return { allowedTools: [], body: source };
  const [, frontmatter, body] = match;
  const line = frontmatter.split("\n").find((l) => l.startsWith("allowed-tools:"));
  const allowedTools = line
    ? line
        .slice("allowed-tools:".length)
        .split(",")
        .map((t) => t.trim())
        .filter(Boolean)
    : [];
  return { allowedTools, body };
}

const commandFiles = readdirSync(commandsDir).filter((f) => f.endsWith(".md"));

describe("PodStack command frontmatter matches registered MCP tools", () => {
  const toolNames = registeredToolNames();

  it("the server registers at least one tool", () => {
    expect(toolNames.size).toBeGreaterThan(0);
  });

  for (const file of commandFiles) {
    it(`${file}: every mcp__podcli__* in allowed-tools is a registered tool`, () => {
      const source = readFileSync(join(commandsDir, file), "utf-8");
      const { allowedTools } = parseFrontmatter(source);
      const mcpTools = allowedTools.filter((t) => t.startsWith("mcp__podcli__"));
      const unknown = mcpTools
        .map((t) => t.slice("mcp__podcli__".length))
        .filter((name) => !toolNames.has(name));
      expect(unknown, `${file} allows unknown tools: ${unknown.join(", ")}`).toEqual([]);
    });

    it(`${file}: every backtick tool-call reference in the body names a registered tool`, () => {
      const source = readFileSync(join(commandsDir, file), "utf-8");
      const { body } = parseFrontmatter(source);
      // Only call-shaped references, e.g. `set_video(file_path)` or
      // `job_status(job_id, wait_seconds: 30)`. Bare backticked words like
      // `payoff` or `async_mode` are field names, not tool calls.
      const calls = Array.from(body.matchAll(/`([a-z][a-z0-9_]*)\(/g)).map((m) => m[1]);
      const unknown = calls.filter((name) => !toolNames.has(name));
      expect(unknown, `${file} calls unknown tool(s): ${unknown.join(", ")}`).toEqual([]);
    });
  }
});
