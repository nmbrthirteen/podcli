import { spawn, type ChildProcess } from "child_process";
import { v4 as uuidv4 } from "uuid";
import { paths, pythonEnv } from "../config/paths.js";
import type { TaskRequest, TaskResult, ProgressEvent } from "../models/index.js";

type ProgressCallback = (event: ProgressEvent) => void;

const isWindows = process.platform === "win32";

export function killProcessTree(proc: ChildProcess, signal: NodeJS.Signals): void {
  if (proc.pid === undefined) return;
  try {
    // Negative pid kills the whole group — the child is a detached group
    // leader, so its ffmpeg/whisper children die with it.
    if (isWindows) proc.kill(signal);
    else process.kill(-proc.pid, signal);
  } catch {
    // Already exited.
  }
}

const KILL_GRACE_MS = 2000;

// SIGTERM, then SIGKILL if the tree is still up. The escalation is cancelled on
// exit: the OS can hand the pid to an unrelated process, and killProcessTree
// signals the whole group (-pid), so a late SIGKILL would take that one out.
export function terminateProcessTree(proc: ChildProcess, graceMs = KILL_GRACE_MS): void {
  killProcessTree(proc, "SIGTERM");
  if (proc.exitCode !== null || proc.signalCode !== null) return;

  let exited = false;
  const escalation = setTimeout(() => {
    if (!exited) killProcessTree(proc, "SIGKILL");
  }, graceMs);
  escalation.unref();
  proc.once("exit", () => {
    exited = true;
    clearTimeout(escalation);
  });
}

// Prefer the last traceback block — that's where the actual failure is —
// and keep a tail long enough to include it.
export function stderrTail(stderr: string, maxChars = 4000): string {
  const tb = stderr.lastIndexOf("Traceback (most recent call last):");
  const tail = tb !== -1 ? stderr.slice(tb) : stderr;
  return tail.slice(-maxChars);
}

function parseResultLine<T>(stdout: string): TaskResult<T> | null {
  // The result is the last stdout line that parses to a JSON object with a
  // status field; the backend may also print stray lines.
  const lines = stdout.trim().split("\n");
  for (let i = lines.length - 1; i >= 0; i--) {
    const trimmed = lines[i].trim();
    if (!trimmed.startsWith("{")) continue;
    try {
      const parsed = JSON.parse(trimmed);
      if (parsed && typeof parsed === "object" && "status" in parsed) {
        return parsed as TaskResult<T>;
      }
    } catch {
      continue;
    }
  }
  return null;
}

/**
 * Executes Python backend tasks via subprocess.
 * Communication: JSON over stdin (request) → stdout (result), stderr (progress logs).
 */
export class PythonExecutor {
  private timeoutMs: number;

  constructor(timeoutMs = 3600_000) {
    // Default 1 hour for long podcasts
    this.timeoutMs = timeoutMs;
  }

  async execute<T = Record<string, unknown>>(
    taskType: TaskRequest["task_type"],
    params: Record<string, unknown>,
    onProgress?: ProgressCallback
  ): Promise<TaskResult<T>> {
    const taskId = uuidv4();

    const request: TaskRequest = {
      task_id: taskId,
      task_type: taskType,
      params,
    };

    return new Promise<TaskResult<T>>((resolve, reject) => {
      const proc = spawn(paths.pythonPath, [paths.pythonBackend], {
        stdio: ["pipe", "pipe", "pipe"],
        detached: !isWindows,
        env: pythonEnv({
          PODCLI_HOME: paths.home,
          PODCLI_DATA: paths.dataDir,
        }),
      });

      let stdout = "";
      let stderr = "";
      let settled = false;
      let timer: NodeJS.Timeout | undefined;

      const finish = (action: () => void): void => {
        if (settled) return;
        settled = true;
        if (timer) clearTimeout(timer);
        action();
      };

      proc.stdout.on("data", (chunk: Buffer) => {
        stdout += chunk.toString();
      });

      // Buffer across chunks so a progress event split over a chunk boundary
      // isn't dropped.
      let stderrBuffer = "";
      proc.stderr.on("data", (chunk: Buffer) => {
        const raw = chunk.toString();
        stderr += raw;
        stderrBuffer += raw;

        const lines = stderrBuffer.split("\n");
        stderrBuffer = lines.pop() ?? "";

        for (const line of lines) {
          const trimmed = line.trim();
          if (!trimmed || !trimmed.startsWith("{")) continue;
          try {
            const event = JSON.parse(trimmed) as ProgressEvent;
            if (event.task_id === taskId && onProgress) {
              onProgress(event);
            }
          } catch {
            continue;
          }
        }
      });

      proc.on("error", (err) => {
        finish(() => reject(new Error(`Failed to spawn Python process: ${err.message}`)));
      });

      // Without this, an EPIPE from a fast-crashing child becomes an unhandled
      // error that crashes the server.
      proc.stdin.on("error", () => {});

      proc.on("close", (code) => {
        finish(() => {
          const result = parseResultLine<T>(stdout);
          if (!result) {
            reject(
              new Error(
                `No parseable output from Python task. Exit code: ${code}. ` +
                  `Stdout: ${stdout.slice(-300)}. Stderr: ${stderrTail(stderr)}`
              )
            );
            return;
          }

          if (result.status === "error") {
            reject(new Error(result.error || "Unknown Python error"));
            return;
          }

          resolve(result);
        });
      });

      try {
        proc.stdin.write(JSON.stringify(request) + "\n");
        proc.stdin.end();
      } catch {}

      timer = setTimeout(() => {
        terminateProcessTree(proc);
        finish(() => reject(new Error(`Task timed out after ${this.timeoutMs / 1000}s`)));
      }, this.timeoutMs);
    });
  }
}
