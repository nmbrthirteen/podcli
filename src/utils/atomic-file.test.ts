import { describe, it, expect } from "vitest";
import { mkdtempSync, readFileSync, readdirSync, existsSync } from "fs";
import { tmpdir } from "os";
import { join } from "path";
import { writeFileAtomic, writeFileAtomicSync } from "./atomic-file.js";

const dir = mkdtempSync(join(tmpdir(), "podcli-atomic-"));

describe("atomic-file", () => {
  it("writes a new file synchronously", () => {
    const p = join(dir, "sync.json");
    writeFileAtomicSync(p, '{"a":1}');
    expect(readFileSync(p, "utf-8")).toBe('{"a":1}');
  });

  it("replaces an existing file synchronously", () => {
    const p = join(dir, "replace.json");
    writeFileAtomicSync(p, "old");
    writeFileAtomicSync(p, "new");
    expect(readFileSync(p, "utf-8")).toBe("new");
  });

  it("writes asynchronously", async () => {
    const p = join(dir, "async.json");
    await writeFileAtomic(p, "async-data");
    expect(readFileSync(p, "utf-8")).toBe("async-data");
  });

  it("leaves no temp files behind", async () => {
    const p = join(dir, "clean.json");
    writeFileAtomicSync(p, "1");
    await writeFileAtomic(p, "2");
    expect(readdirSync(dir).filter((f) => f.endsWith(".tmp"))).toEqual([]);
  });

  it("cleans up the temp file when the rename target is invalid", async () => {
    const target = join(dir, "missing-dir", "x.json");
    await expect(writeFileAtomic(target, "data")).rejects.toThrow();
    expect(existsSync(target)).toBe(false);
    expect(readdirSync(dir).filter((f) => f.endsWith(".tmp"))).toEqual([]);
  });
});
