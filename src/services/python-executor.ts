import { spawn, type ChildProcess } from "child_process";
import { v4 as uuidv4 } from "uuid";
import { paths, pythonEnv } from "../config/paths.js";
import type { TaskRequest, TaskResult, ProgressEvent } from "../models/index.js";

type ProgressCallback = (event: ProgressEvent) => void;

const isWindows = process.platform === "win32";

type ProcessTreeOptions = {
  platform?: NodeJS.Platform;
  spawnProcess?: typeof spawn;
};

function hasExited(proc: ChildProcess): boolean {
  return proc.exitCode !== null || proc.signalCode !== null;
}

export function killProcessTree(
  proc: ChildProcess,
  signal: NodeJS.Signals,
  options: ProcessTreeOptions = {}
): void {
  if (proc.pid === undefined || hasExited(proc)) return;
  const platform = options.platform ?? process.platform;
  try {
    if (platform === "win32") {
      const taskkill = (options.spawnProcess ?? spawn)(
        "taskkill",
        ["/pid", String(proc.pid), "/T", "/F"],
        { stdio: "ignore", windowsHide: true }
      );
      taskkill.once("error", () => undefined);
    } else process.kill(-proc.pid, signal);
  } catch {}
}

const KILL_GRACE_MS = 2000;

export function terminateProcessTree(
  proc: ChildProcess,
  graceMs = KILL_GRACE_MS,
  options: ProcessTreeOptions = {}
): void {
  if (hasExited(proc)) return;
  const platform = options.platform ?? process.platform;
  if (platform === "win32") {
    killProcessTree(proc, "SIGKILL", options);
    return;
  }

  let exited = false;
  let escalation: NodeJS.Timeout | undefined;
  const onExit = (): void => {
    exited = true;
    if (escalation) clearTimeout(escalation);
  };
  proc.once("exit", onExit);
  if (hasExited(proc)) {
    proc.removeListener("exit", onExit);
    return;
  }

  killProcessTree(proc, "SIGTERM", options);
  if (exited || hasExited(proc)) return;

  escalation = setTimeout(() => {
    if (!exited && !hasExited(proc)) killProcessTree(proc, "SIGKILL", options);
  }, graceMs);
  escalation.unref();
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
