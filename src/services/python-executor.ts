import { spawn, type ChildProcess } from "child_process";
import { v4 as uuidv4 } from "uuid";
import { paths } from "../config/paths.js";
import type { TaskRequest, TaskResult, ProgressEvent } from "../models/index.js";

type ProgressCallback = (event: ProgressEvent) => void;

const isWindows = process.platform === "win32";

function killProcessTree(proc: ChildProcess, signal: NodeJS.Signals): void {
  if (proc.pid === undefined) return;
  try {
    if (isWindows) {
      proc.kill(signal);
    } else {
      // Negative pid targets the whole process group. The child is spawned
      // detached (a group leader), so its ffmpeg/whisper children die too
      // instead of running to completion after the parent gives up.
      process.kill(-proc.pid, signal);
    }
  } catch {
    // Already exited.
  }
}

function parseResultLine<T>(stdout: string): TaskResult<T> | null {
  // The backend may print stray lines to stdout; the result is the last line
  // that parses to a JSON object carrying a status field.
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
      // Not the result line — keep scanning upward.
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
        env: {
          ...process.env,
          PYTHONUNBUFFERED: "1",
          PODCLI_HOME: paths.home,
          PODCLI_DATA: paths.dataDir,
        },
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

      // Progress events arrive on stderr, one JSON object per line. Buffer
      // across chunks so an event split over a chunk boundary isn't dropped.
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
            // Regular log line, ignore
          }
        }
      });

      proc.on("error", (err) => {
        finish(() => reject(new Error(`Failed to spawn Python process: ${err.message}`)));
      });

      // A child that crashes before we finish writing the request makes stdin
      // emit EPIPE; without a handler that unhandled error crashes the server.
      proc.stdin.on("error", () => {
        // The close/error handlers surface the real failure.
      });

      proc.on("close", (code) => {
        finish(() => {
          const result = parseResultLine<T>(stdout);
          if (!result) {
            reject(
              new Error(
                `No parseable output from Python task. Exit code: ${code}. ` +
                  `Stdout: ${stdout.slice(-300)}. Stderr: ${stderr.slice(-300)}`
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

      // Send request and close stdin
      try {
        proc.stdin.write(JSON.stringify(request) + "\n");
        proc.stdin.end();
      } catch {
        // EPIPE — surfaced by the stdin 'error'/'close' handlers above.
      }

      // Timeout guard: SIGTERM the group for a graceful exit, then SIGKILL.
      timer = setTimeout(() => {
        killProcessTree(proc, "SIGTERM");
        setTimeout(() => killProcessTree(proc, "SIGKILL"), 2000).unref();
        finish(() => reject(new Error(`Task timed out after ${this.timeoutMs / 1000}s`)));
      }, this.timeoutMs);
    });
  }
}
