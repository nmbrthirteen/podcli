import { mkdir, rm, readdir, stat, rename } from "fs/promises";
import { existsSync } from "fs";
import { join } from "path";
import { v4 as uuidv4 } from "uuid";
import { paths } from "../config/paths.js";

/**
 * Manages working directories and output files.
 */
export class FileManager {
  async ensureDirectories() {
    const dirs = [
      paths.home,
      paths.cache,
      paths.transcripts,
      paths.working,
      paths.output,
      paths.logs,
      paths.assets,
      paths.history,
      paths.knowledge,
    ];
    for (const dir of dirs) {
      if (!existsSync(dir)) {
        await mkdir(dir, { recursive: true });
      }
    }
  }

  createTaskDir(): { taskId: string; taskDir: string } {
    const taskId = uuidv4();
    const taskDir = join(paths.working, taskId);
    return { taskId, taskDir };
  }

  async ensureTaskDir(taskId: string): Promise<string> {
    const taskDir = join(paths.working, taskId);
    if (!existsSync(taskDir)) {
      await mkdir(taskDir, { recursive: true });
    }
    return taskDir;
  }

  async moveToOutput(sourcePath: string, filename: string): Promise<string> {
    const destPath = join(paths.output, filename);
    await rename(sourcePath, destPath);
    return destPath;
  }

  async cleanupTask(taskId: string): Promise<void> {
    const taskDir = join(paths.working, taskId);
    if (existsSync(taskDir)) {
      await rm(taskDir, { recursive: true, force: true });
    }
  }

  /**
   * Remove working directories older than maxAgeHours.
   */
  async cleanupOldTasks(maxAgeHours = 48): Promise<number> {
    if (!existsSync(paths.working)) return 0;

    const entries = await readdir(paths.working);
    const now = Date.now();
    let cleaned = 0;

    for (const entry of entries) {
      const fullPath = join(paths.working, entry);
      try {
        const s = await stat(fullPath);
        const ageHours = (now - s.mtimeMs) / (1000 * 60 * 60);
        if (ageHours > maxAgeHours) {
          await rm(fullPath, { recursive: true, force: true });
          cleaned++;
        }
      } catch {
        // Skip entries we can't stat
      }
    }

    return cleaned;
  }
}
