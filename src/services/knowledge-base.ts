import { readFile, writeFile, readdir, stat, unlink, mkdir, copyFile } from "fs/promises";
import { existsSync } from "fs";
import { join } from "path";
import { paths } from "../config/paths.js";
import type { KnowledgeFile } from "../models/index.js";

const templatesDir = join(paths.backendDir, "templates", "knowledge");

// Template blanks look like [Show name]; a markdown link ([text](url)) is not one.
const PLACEHOLDER = /\[[^\]\n]{1,80}\](?!\()/g;

function placeholders(text: string): number {
  return text.match(PLACEHOLDER)?.length ?? 0;
}

export function isFilledIn(content: string, template: string): boolean {
  const body = content.trim();
  if (!body || body === template.trim()) return false;
  return placeholders(body) <= placeholders(template) * 0.3;
}

export interface KnowledgeStatus {
  templates: string[];
  present: string[];
  filled: string[];
  missing: string[];
}

export class KnowledgeBase {
  private dir = paths.knowledge;

  async ensureDir() {
    await mkdir(this.dir, { recursive: true });
  }

  private async loadTemplates(): Promise<Map<string, string>> {
    const templates = new Map<string, string>();
    let names: string[];
    try {
      names = (await readdir(templatesDir)).filter((f) => f.endsWith(".md")).sort();
    } catch {
      return templates;
    }
    for (const name of names) {
      templates.set(name, await readFile(join(templatesDir, name), "utf-8"));
    }
    return templates;
  }

  /** Copy the starter templates in, never overwriting a file the user already has. */
  async initFromTemplates(): Promise<{ created: string[]; kept: string[] }> {
    const templates = await this.loadTemplates();
    if (templates.size === 0) {
      throw new Error(`No knowledge templates found at ${templatesDir}`);
    }
    await this.ensureDir();

    const created: string[] = [];
    const kept: string[] = [];
    for (const name of templates.keys()) {
      const target = join(this.dir, name);
      if (existsSync(target)) {
        kept.push(name);
        continue;
      }
      await copyFile(join(templatesDir, name), target);
      created.push(name);
    }
    return { created, kept };
  }

  async status(): Promise<KnowledgeStatus> {
    const templates = await this.loadTemplates();
    const byName = new Map((await this.listFiles()).map((f) => [f.filename, f.content]));

    const present: string[] = [];
    const filled: string[] = [];
    const missing: string[] = [];
    for (const [name, template] of templates) {
      const content = byName.get(name);
      if (content === undefined) {
        missing.push(name);
        continue;
      }
      present.push(name);
      if (isFilledIn(content, template)) filled.push(name);
    }
    return { templates: [...templates.keys()], present, filled, missing };
  }

  async listFiles(): Promise<KnowledgeFile[]> {
    await this.ensureDir();
    const entries = await readdir(this.dir);
    const mdFiles = entries.filter((f) => f.endsWith(".md"));

    const files: KnowledgeFile[] = [];
    for (const filename of mdFiles) {
      const filePath = join(this.dir, filename);
      const s = await stat(filePath);
      const content = await readFile(filePath, "utf-8");
      files.push({
        filename,
        content,
        updatedAt: s.mtime.toISOString(),
      });
    }
    return files.sort((a, b) => a.filename.localeCompare(b.filename));
  }

  /** Read all .md files concatenated — the main method used by MCP tools. */
  async readAll(): Promise<string> {
    const files = await this.listFiles();
    // Skip README
    const relevant = files.filter((f) => f.filename !== "README.md");
    if (relevant.length === 0) return "";

    return relevant
      .map((f) => `--- ${f.filename} ---\n${f.content.trim()}`)
      .join("\n\n");
  }

  async readFile(filename: string): Promise<string> {
    const filePath = join(this.dir, filename);
    if (!existsSync(filePath)) throw new Error(`File not found: ${filename}`);
    return readFile(filePath, "utf-8");
  }

  async writeFile(filename: string, content: string): Promise<void> {
    await this.ensureDir();
    if (!filename.endsWith(".md")) filename += ".md";
    await writeFile(join(this.dir, filename), content, "utf-8");
  }

  async deleteFile(filename: string): Promise<void> {
    const filePath = join(this.dir, filename);
    if (existsSync(filePath)) await unlink(filePath);
  }
}
