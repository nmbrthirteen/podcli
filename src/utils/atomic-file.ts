import { writeFileSync, renameSync, rmSync } from "fs";
import { writeFile, rename, rm } from "fs/promises";
import { randomUUID } from "crypto";

// Write to a temp file and atomically rename so a crash or a concurrent
// reader never sees a half-written file.
function tmpPathFor(filePath: string): string {
  return `${filePath}.${process.pid}.${randomUUID().slice(0, 8)}.tmp`;
}

export function writeFileAtomicSync(filePath: string, data: string): void {
  const tmp = tmpPathFor(filePath);
  try {
    writeFileSync(tmp, data, "utf-8");
    renameSync(tmp, filePath);
  } catch (err) {
    try {
      rmSync(tmp, { force: true });
    } catch {}
    throw err;
  }
}

export async function writeFileAtomic(filePath: string, data: string): Promise<void> {
  const tmp = tmpPathFor(filePath);
  try {
    await writeFile(tmp, data, "utf-8");
    await rename(tmp, filePath);
  } catch (err) {
    await rm(tmp, { force: true }).catch(() => {});
    throw err;
  }
}
