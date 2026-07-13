import { describe, it, expect, beforeEach } from "vitest";
import { mkdtempSync, rmSync, mkdirSync, existsSync, readFileSync } from "fs";
import { tmpdir } from "os";
import { join } from "path";

const tmp = mkdtempSync(join(tmpdir(), "podcli-kb-test-"));
process.env.PODCLI_HOME = tmp;
process.env.PODCLI_DATA = tmp;

const { KnowledgeBase, isFilledIn } = await import("./knowledge-base.js");

describe("KnowledgeBase", () => {
  let kb: InstanceType<typeof KnowledgeBase>;

  beforeEach(() => {
    rmSync(join(tmp, "knowledge"), { recursive: true, force: true });
    mkdirSync(join(tmp, "knowledge"), { recursive: true });
    kb = new KnowledgeBase();
  });

  it("ensureDir creates an empty directory", async () => {
    rmSync(join(tmp, "knowledge"), { recursive: true, force: true });
    await kb.ensureDir();
    expect(existsSync(join(tmp, "knowledge"))).toBe(true);
    expect(await kb.listFiles()).toEqual([]);
  });

  it("initFromTemplates copies the numbered templates and keeps existing files", async () => {
    const first = await kb.initFromTemplates();
    expect(first.created).toContain("01-brand-identity.md");
    expect(first.created).toContain("02-voice-and-tone.md");
    expect(first.kept).toEqual([]);
    expect(readFileSync(join(tmp, "knowledge", "01-brand-identity.md"), "utf-8")).toContain("[Show name]");

    await kb.writeFile("01-brand-identity.md", "# Brand identity\nMine, edited");
    const second = await kb.initFromTemplates();
    expect(second.created).toEqual([]);
    expect(second.kept).toEqual(first.created);
    expect(readFileSync(join(tmp, "knowledge", "01-brand-identity.md"), "utf-8")).toContain("Mine, edited");
  });

  it("status reports which templates are present and which are filled in", async () => {
    const empty = await kb.status();
    expect(empty.templates.length).toBeGreaterThan(0);
    expect(empty.present).toEqual([]);
    expect(empty.missing).toEqual(empty.templates);

    await kb.initFromTemplates();
    const untouched = await kb.status();
    expect(untouched.present).toEqual(untouched.templates);
    expect(untouched.filled).toEqual([]);

    await kb.writeFile("01-brand-identity.md", "# Brand identity\n\n- Name: Deep Dive\n- Audience: founders");
    const edited = await kb.status();
    expect(edited.filled).toEqual(["01-brand-identity.md"]);
  });

  it("isFilledIn treats a mostly-bracketed file as a template", () => {
    const template = "# Voice\n\n- Tone: [tone]\n- Banned: [words]\n- Hook: [hook]\n- Close: [close]";
    expect(isFilledIn(template, template)).toBe(false);
    expect(isFilledIn("# Voice\n\n- Tone: [tone]\n- Banned: hype\n- Hook: [hook]\n- Close: [close]", template)).toBe(false);
    expect(isFilledIn("# Voice\n\n- Tone: blunt\n- Banned: hype\n- Hook: cold open\n- Close: hard cut", template)).toBe(true);
    expect(isFilledIn("", template)).toBe(false);
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
    await kb.writeFile("README.md", "# ignore me");
    await kb.writeFile("one.md", "first");
    await kb.writeFile("two.md", "second");
    const all = await kb.readAll();
    expect(all).toContain("--- one.md ---");
    expect(all).toContain("first");
    expect(all).toContain("--- two.md ---");
    expect(all).toContain("second");
    expect(all).not.toContain("README");
  });

  it("readAll returns empty string when the directory is empty", async () => {
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
