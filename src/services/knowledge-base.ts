import { readFile, writeFile, readdir, stat, unlink, mkdir } from "fs/promises";
import { existsSync } from "fs";
import { join } from "path";
import { paths } from "../config/paths.js";
import type { KnowledgeFile } from "../models/index.js";

const DEFAULT_README = `# podcli Knowledge Base

Add \`.md\` files here to give the AI context when creating clips.

## Suggested files

- \`podcast.md\` — Show name, description, format, episode structure
- \`hosts.md\` — Host names, speaking styles, roles
- \`style.md\` — Preferred caption style, logo, crop strategy, colors
- \`audience.md\` — Target audience, platform preferences (TikTok vs Reels vs Shorts)
- \`avoid.md\` — Topics, segments, or time ranges to skip

The MCP server reads all files here before processing requests.
`;

export class KnowledgeBase {
  private dir = paths.knowledge;

  async ensureDir() {
    if (!existsSync(this.dir)) {
      await mkdir(this.dir, { recursive: true });
      // Write default README
      await writeFile(join(this.dir, "README.md"), DEFAULT_README, "utf-8");
    }
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
