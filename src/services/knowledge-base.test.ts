import { describe, it, expect, beforeEach } from "vitest";
import { mkdtempSync, rmSync, mkdirSync, existsSync, readFileSync } from "fs";
import { tmpdir } from "os";
import { join } from "path";

const tmp = mkdtempSync(join(tmpdir(), "podcli-kb-test-"));
process.env.PODCLI_HOME = tmp;
process.env.PODCLI_DATA = tmp;

const { KnowledgeBase } = await import("./knowledge-base.js");

describe("KnowledgeBase", () => {
  let kb: InstanceType<typeof KnowledgeBase>;

  beforeEach(() => {
    rmSync(join(tmp, "knowledge"), { recursive: true, force: true });
    mkdirSync(join(tmp, "knowledge"), { recursive: true });
    kb = new KnowledgeBase();
  });

  it("ensureDir creates the directory and seeds a README", async () => {
    rmSync(join(tmp, "knowledge"), { recursive: true, force: true });
    await kb.ensureDir();
    expect(existsSync(join(tmp, "knowledge", "README.md"))).toBe(true);
    expect(readFileSync(join(tmp, "knowledge", "README.md"), "utf-8")).toMatch(/Knowledge Base/);
  });

  it("writeFile auto-appends .md extension if missing", async () => {
    await kb.writeFile("style", "# Style\nbrand voice here");
    expect(existsSync(join(tmp, "knowledge", "style.md"))).toBe(true);
  });

  it("writeFile preserves explicit .md extension", async () => {
    await kb.writeFile("hosts.md", "# Hosts");
    expect(existsSync(join(tmp, "knowledge", "hosts.md"))).toBe(true);
  });

  it("readFile round-trips content", async () => {
    await kb.writeFile("voice.md", "# Voice\ncasual & punchy");
    const content = await kb.readFile("voice.md");
    expect(content).toContain("casual & punchy");
  });

  it("readFile throws for missing files", async () => {
    await expect(kb.readFile("ghost.md")).rejects.toThrow(/File not found/);
  });

  it("listFiles returns all .md files sorted by filename, excluding nothing", async () => {
    await kb.writeFile("zeta.md", "z");
    await kb.writeFile("alpha.md", "a");
    await kb.writeFile("mid.md", "m");
    const files = await kb.listFiles();
    const names = files.map((f) => f.filename);
    expect(names).toContain("alpha.md");
    expect(names).toContain("mid.md");
    expect(names).toContain("zeta.md");
    // Ensure sorted
    const customSorted = [...names].sort();
    expect(names).toEqual(customSorted);
  });

  it("readAll concatenates all files except README", async () => {
    // ensureDir seeds README
    await kb.ensureDir();
    await kb.writeFile("one.md", "first");
    await kb.writeFile("two.md", "second");
    const all = await kb.readAll();
    expect(all).toContain("--- one.md ---");
    expect(all).toContain("first");
    expect(all).toContain("--- two.md ---");
    expect(all).toContain("second");
    expect(all).not.toContain("README");
  });

  it("readAll returns empty string when only README exists", async () => {
    await kb.ensureDir();
    expect(await kb.readAll()).toBe("");
  });

  it("deleteFile removes the file", async () => {
    await kb.writeFile("temp.md", "temporary");
    await kb.deleteFile("temp.md");
    expect(existsSync(join(tmp, "knowledge", "temp.md"))).toBe(false);
  });

  it("deleteFile is idempotent on missing files", async () => {
    await expect(kb.deleteFile("never.md")).resolves.toBeUndefined();
  });
});
