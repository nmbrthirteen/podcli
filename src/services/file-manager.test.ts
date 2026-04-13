import { describe, it, expect, beforeEach } from "vitest";
import { mkdtempSync, existsSync, writeFileSync, utimesSync, mkdirSync, rmSync } from "fs";
import { tmpdir } from "os";
import { join } from "path";

const tmp = mkdtempSync(join(tmpdir(), "podcli-fm-test-"));
process.env.PODCLI_HOME = tmp;
process.env.PODCLI_DATA = tmp;

const { FileManager } = await import("./file-manager.js");

describe("FileManager", () => {
  let fm: InstanceType<typeof FileManager>;

  beforeEach(() => {
    // Reset the working/output dirs per test
    rmSync(join(tmp, "working"), { recursive: true, force: true });
    rmSync(join(tmp, "output"), { recursive: true, force: true });
    fm = new FileManager();
  });

  it("ensureDirectories creates all required directories", async () => {
    await fm.ensureDirectories();
    for (const name of ["cache", "working", "output", "history"]) {
      expect(existsSync(join(tmp, name))).toBe(true);
    }
  });

  it("ensureDirectories is idempotent", async () => {
    await fm.ensureDirectories();
    await expect(fm.ensureDirectories()).resolves.toBeUndefined();
  });

  it("createTaskDir returns a unique taskId and a path under working/", () => {
    const a = fm.createTaskDir();
    const b = fm.createTaskDir();
    expect(a.taskId).not.toBe(b.taskId);
    expect(a.taskDir.startsWith(join(tmp, "working"))).toBe(true);
  });

  it("ensureTaskDir creates the directory if missing", async () => {
    await fm.ensureDirectories();
    const taskId = "test-task-abc";
    const dir = await fm.ensureTaskDir(taskId);
    expect(existsSync(dir)).toBe(true);
    expect(dir).toBe(join(tmp, "working", taskId));
  });

  it("ensureTaskDir is idempotent for an existing directory", async () => {
    await fm.ensureDirectories();
    const taskId = "repeat-task";
    const first = await fm.ensureTaskDir(taskId);
    const second = await fm.ensureTaskDir(taskId);
    expect(first).toBe(second);
    expect(existsSync(first)).toBe(true);
  });

  it("cleanupTask removes the task directory", async () => {
    await fm.ensureDirectories();
    const taskId = "goner-task";
    const dir = await fm.ensureTaskDir(taskId);
    writeFileSync(join(dir, "payload.txt"), "hi");
    await fm.cleanupTask(taskId);
    expect(existsSync(dir)).toBe(false);
  });

  it("cleanupTask silently skips missing directories", async () => {
    await expect(fm.cleanupTask("never-created")).resolves.toBeUndefined();
  });

  it("cleanupOldTasks returns 0 when working dir missing", async () => {
    rmSync(join(tmp, "working"), { recursive: true, force: true });
    const cleaned = await fm.cleanupOldTasks(48);
    expect(cleaned).toBe(0);
  });

  it("cleanupOldTasks deletes directories older than threshold", async () => {
    await fm.ensureDirectories();
    const working = join(tmp, "working");

    // Fresh dir — should survive
    const fresh = join(working, "fresh");
    mkdirSync(fresh);

    // Old dir — backdated to 5 days ago
    const old = join(working, "old");
    mkdirSync(old);
    const ago = new Date(Date.now() - 5 * 24 * 3600 * 1000);
    utimesSync(old, ago, ago);

    const cleaned = await fm.cleanupOldTasks(48); // 2 days
    expect(cleaned).toBe(1);
    expect(existsSync(fresh)).toBe(true);
    expect(existsSync(old)).toBe(false);
  });

  it("cleanupOldTasks does not touch fresh directories", async () => {
    await fm.ensureDirectories();
    const working = join(tmp, "working");
    const fresh = join(working, "recent-" + Date.now());
    mkdirSync(fresh);
    const cleaned = await fm.cleanupOldTasks(48);
    expect(cleaned).toBe(0);
    expect(existsSync(fresh)).toBe(true);
  });

  it("moveToOutput moves a file and returns the destination path", async () => {
    await fm.ensureDirectories();
    const src = join(tmp, "source.mp4");
    writeFileSync(src, "stub");
    const dest = await fm.moveToOutput(src, "final.mp4");
    expect(dest).toBe(join(tmp, "output", "final.mp4"));
    expect(existsSync(dest)).toBe(true);
    expect(existsSync(src)).toBe(false);
  });
});
