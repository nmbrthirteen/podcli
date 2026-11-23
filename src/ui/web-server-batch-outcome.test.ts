import { describe, it, expect, beforeAll, afterAll } from "vitest";
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "fs";
import { tmpdir } from "os";
import { join } from "path";
import { z } from "zod";
import { stubPythonBackend } from "../test-support/stub-python-backend.js";
import type { BatchClipsResult } from "../models/index.js";

// The Python batch task succeeds as a task even when every clip in it failed.
// The job used to end as "done" / "Export complete!" anyway, so an agent polling
// job_status saw success with no clips on disk.

const tmp = mkdtempSync(join(tmpdir(), "podcli-batch-outcome-"));

const savedEnv = {
  PODCLI_HOME: process.env.PODCLI_HOME,
  PODCLI_DATA: process.env.PODCLI_DATA,
  PODCLI_OUTPUT: process.env.PODCLI_OUTPUT,
  PODCLI_PORT: process.env.PODCLI_PORT,
};

const PORT = 18430;
const BASE = `http://127.0.0.1:${PORT}`;
const REMOTION_ERROR = "Remotion caption render failed. ASS fallback is disabled";

let batchReply: BatchClipsResult = { total_clips: 0, successful_clips: 0, results: [] };
let videoPath: string;

const jobSchema = z.object({
  status: z.enum(["pending", "running", "done", "error"]),
  message: z.string(),
  error: z.string().optional(),
  result: z.object({ results: z.array(z.object({ status: z.string(), error: z.string().optional() })) }).optional(),
});

async function waitForServer(): Promise<void> {
  for (let i = 0; i < 50; i++) {
    try {
      const res = await fetch(`${BASE}/api/ui-state`);

      if (res.ok) return;
    } catch {
      // not up yet
    }

    await new Promise((r) => setTimeout(r, 100));
  }

  throw new Error("web server never came up");
}

async function finishedJob(path: string, body: ExportRequest): Promise<z.infer<typeof jobSchema>> {
  const start = await fetch(`${BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });

  expect(start.status).toBe(200);

  const { job_id } = z.object({ job_id: z.string() }).parse(await start.json());

  for (let i = 0; i < 100; i++) {
    const job = jobSchema.parse(await (await fetch(`${BASE}/api/job/${job_id}`)).json());

    if (job.status === "done" || job.status === "error") return job;
    await new Promise((r) => setTimeout(r, 50));
  }

  throw new Error("batch job never finished");
}

const clips = [
  { start_second: 10, end_second: 40, title: "one" },
  { start_second: 60, end_second: 90, title: "two" },
  { start_second: 120, end_second: 150, title: "three" },
];

interface ExportRequest {
  video_path: string;
  clips: typeof clips;
}

function rows(statuses: Array<"success" | "error">): BatchClipsResult {
  return {
    total_clips: statuses.length,
    successful_clips: statuses.filter((s) => s === "success").length,
    results: statuses.map((status, i) =>
      status === "success"
        ? { clip_index: i, status: "success" as const, output_path: join(tmp, `clip-${i}.mp4`) }
        : { clip_index: i, status: "error" as const, error: `${REMOTION_ERROR} (clip ${i})` },
    ),
  };
}

beforeAll(async () => {
  process.env.PODCLI_HOME = join(tmp, "home");
  process.env.PODCLI_DATA = join(tmp, "data");
  process.env.PODCLI_OUTPUT = join(tmp, "data", "output");
  process.env.PODCLI_PORT = String(PORT);
  mkdirSync(process.env.PODCLI_HOME, { recursive: true });
  videoPath = join(tmp, "episode.mp4");
  writeFileSync(videoPath, "not really a video");

  await stubPythonBackend((taskType) => (taskType === "batch_clips" ? batchReply : {}));
  await import("./web-server.js");
  await waitForServer();
}, 20_000);

afterAll(() => {
  for (const [key, value] of Object.entries(savedEnv)) {
    if (value === undefined) delete process.env[key];
    else process.env[key] = value;
  }

  rmSync(tmp, { recursive: true, force: true });
});

describe.each([
  ["/api/batch-clips", "Batch complete!"],
  ["/api/mcp/export", "Export complete!"],
])("POST %s", (path, successMessage) => {
  it("ends as an error with the first clip's error when no clip rendered", async () => {
    batchReply = rows(["error", "error", "error"]);
    const job = await finishedJob(path, { video_path: videoPath, clips });

    expect(job.status).toBe("error");
    expect(job.error).toContain("All 3 clips failed");
    expect(job.error).toContain(`${REMOTION_ERROR} (clip 0)`);
    expect(job.message).toContain(REMOTION_ERROR);
    // The per-clip errors stay readable on the job.
    expect(job.result?.results.map((r) => r.error)).toHaveLength(3);
  });

  it("says how many rendered and lists the failures when some clips failed", async () => {
    batchReply = rows(["success", "error", "success"]);
    const job = await finishedJob(path, { video_path: videoPath, clips });

    expect(job.status).toBe("done");
    expect(job.error).toBeUndefined();
    expect(job.message).toContain("2 of 3 clips rendered");
    expect(job.message).toContain(`clip 2: ${REMOTION_ERROR} (clip 1)`);
  });

  it("keeps the plain success message when every clip rendered", async () => {
    batchReply = rows(["success", "success", "success"]);
    const job = await finishedJob(path, { video_path: videoPath, clips });

    expect(job.status).toBe("done");
    expect(job.message).toBe(successMessage);
  });
});
