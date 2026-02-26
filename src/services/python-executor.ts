import { spawn } from "child_process";
import { v4 as uuidv4 } from "uuid";
import { paths } from "../config/paths.js";
import type { TaskRequest, TaskResult, ProgressEvent } from "../models/index.js";

type ProgressCallback = (event: ProgressEvent) => void;

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

  async execute(
    taskType: TaskRequest["task_type"],
    params: Record<string, unknown>,
    onProgress?: ProgressCallback
  ): Promise<TaskResult> {
    const taskId = uuidv4();

    const request: TaskRequest = {
      task_id: taskId,
      task_type: taskType,
      params,
    };

    return new Promise<TaskResult>((resolve, reject) => {
      const proc = spawn(paths.pythonPath, [paths.pythonBackend], {
        stdio: ["pipe", "pipe", "pipe"],
        env: {
          ...process.env,
          PYTHONUNBUFFERED: "1",
        },
      });

      let stdout = "";
      let stderr = "";

      proc.stdout.on("data", (chunk: Buffer) => {
        stdout += chunk.toString();
      });

      proc.stderr.on("data", (chunk: Buffer) => {
        const raw = chunk.toString();
        stderr += raw;

        // Try to parse progress events (one per line)
        for (const line of raw.split("\n")) {
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
        reject(new Error(`Failed to spawn Python process: ${err.message}`));
      });

      proc.on("close", (code) => {
        try {
          // Find the last complete JSON object in stdout
          const jsonMatch = stdout.trim().split("\n").filter(Boolean).pop();
          if (!jsonMatch) {
            reject(
              new Error(
                `No output from Python task. Exit code: ${code}. Stderr: ${stderr.slice(-500)}`
              )
            );
            return;
          }

          const result = JSON.parse(jsonMatch) as TaskResult;

          if (result.status === "error") {
            reject(new Error(result.error || "Unknown Python error"));
            return;
          }

          resolve(result);
        } catch (parseErr) {
          reject(
            new Error(
              `Failed to parse Python output. Exit code: ${code}. ` +
                `Stdout: ${stdout.slice(-300)}. Stderr: ${stderr.slice(-300)}`
            )
          );
        }
      });

      // Send request and close stdin
      proc.stdin.write(JSON.stringify(request) + "\n");
      proc.stdin.end();

      // Timeout guard
      const timer = setTimeout(() => {
        proc.kill("SIGKILL");
        reject(new Error(`Task timed out after ${this.timeoutMs / 1000}s`));
      }, this.timeoutMs);

      proc.on("close", () => clearTimeout(timer));
    });
  }
}
