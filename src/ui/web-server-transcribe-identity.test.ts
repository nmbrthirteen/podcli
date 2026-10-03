import { describe, it, expect, beforeAll, afterAll, vi } from "vitest";
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "fs";
import { tmpdir } from "os";
import { join } from "path";

// transcribe_start (POST /api/transcribe) used to update uiState.transcript
// and uiState.videoPath without setting transcriptVideoIdentity. The next
// create_clip/batch_create_clips call reads that identity to confirm the
// session's transcript still belongs to the loaded video; left unset, a
// freshly transcribed session falsely looks like it never recorded one, and
// callers relying on it being present (or on it being cleared on a stale
// video) see the wrong state. This test drives the real HTTP route and
// checks the persisted ui-state.json, not just the in-memory handler logic.

const tmp = mkdtempSync(join(tmpdir(), "podcli-transcribe-identity-"));
const savedEnv: Record<string, string | undefined> = {
  PODCLI_HOME: process.env.PODCLI_HOME,
  PODCLI_DATA: process.env.PODCLI_DATA,
  PODCLI_PORT: process.env.PODCLI_PORT,
};

const PORT = 18424;

// The web server spawns the Python backend on startup (manage_config status
// check) and per-transcribe-request (engine resolution). Stub it so the test
// never depends on a real backend or network call, and resolves instantly.
vi.mock("../services/python-executor.js", async (importOriginal) => {
  const original = await importOriginal<typeof import("../services/python-executor.js")>();
  class StubExecutor {
    async execute(taskType: string) {
      if (taskType === "resolve_transcribe_engine") {
        return { data: { engine: "whisper-py" } };
      }
      if (taskType === "transcribe") {
        return { data: { engine: "whisper-py", words: [{ word: "hi", start: 0, end: 1 }] } };
      }
      return { data: {} };
    }
  }
  return { ...original, PythonExecutor: StubExecutor };
});

let videoPath: string;
let uiStatePath: string;

async function waitForServer(): Promise<void> {
  for (let i = 0; i < 50; i++) {
    try {
      const res = await fetch(`http://127.0.0.1:${PORT}/api/ui-state`);
      if (res.ok) return;
    } catch {
      // not up yet
    }
    await new Promise((r) => setTimeout(r, 100));
  }
  throw new Error("web server never came up");
}

beforeAll(async () => {
  process.env.PODCLI_HOME = join(tmp, "home");
  process.env.PODCLI_DATA = join(tmp, "data");
  process.env.PODCLI_PORT = String(PORT);
  mkdirSync(process.env.PODCLI_HOME, { recursive: true });
  uiStatePath = join(process.env.PODCLI_HOME, "ui-state.json");

  videoPath = join(tmp, "episode.mp4");
  writeFileSync(videoPath, "fake video bytes");

  const { TranscriptCache } = await import("../services/transcript-cache.js");
  const cache = new TranscriptCache();
  await cache.set(
    videoPath,
    { words: [{ word: "hi", start: 0, end: 1 }] } as never,
    { engine: "whisper-py", model: "base", language: undefined },
  );

  await import("./web-server.js");
  await waitForServer();
}, 20_000);

afterAll(() => {
  process.env.PODCLI_HOME = savedEnv.PODCLI_HOME;
  process.env.PODCLI_DATA = savedEnv.PODCLI_DATA;
  process.env.PODCLI_PORT = savedEnv.PODCLI_PORT;
  rmSync(tmp, { recursive: true, force: true });
});

describe("POST /api/transcribe", () => {
  it("sets transcriptVideoIdentity for the cached-transcript branch", async () => {
    const res = await fetch(`http://127.0.0.1:${PORT}/api/transcribe`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        file_path: videoPath,
        model_size: "base",
        enable_diarization: false,
      }),
    });
    const body = await res.json();
    expect(body.cached).toBe(true);

    // /api/ui-state doesn't echo transcriptVideoIdentity, and persistState()
    // debounces its write to disk by 500ms, so poll the persisted file.
    let uiState: { transcriptVideoIdentity?: { path?: string } } = {};
    for (let i = 0; i < 20; i++) {
      try {
        uiState = JSON.parse(readFileSync(uiStatePath, "utf-8"));
        if (uiState.transcriptVideoIdentity) break;
      } catch {
        // not written yet
      }
      await new Promise((r) => setTimeout(r, 100));
    }
    expect(uiState.transcriptVideoIdentity).toBeTruthy();
    expect(uiState.transcriptVideoIdentity?.path).toBe(videoPath);
  });

  it("sets transcriptVideoIdentity for the async (uncached) transcription branch", async () => {
    const freshVideoPath = join(tmp, "episode-2.mp4");
    writeFileSync(freshVideoPath, "other fake video bytes");

    const res = await fetch(`http://127.0.0.1:${PORT}/api/transcribe`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        file_path: freshVideoPath,
        model_size: "base",
        enable_diarization: false,
      }),
    });
    const { job_id } = await res.json();

    let job: { status?: string } = {};
    for (let i = 0; i < 50; i++) {
      job = await (await fetch(`http://127.0.0.1:${PORT}/api/job/${job_id}`)).json();
      if (job.status === "done" || job.status === "error") break;
      await new Promise((r) => setTimeout(r, 100));
    }
    expect(job.status).toBe("done");

    let uiState: { transcriptVideoIdentity?: { path?: string } } = {};
    for (let i = 0; i < 20; i++) {
      try {
        uiState = JSON.parse(readFileSync(uiStatePath, "utf-8"));
        if (uiState.transcriptVideoIdentity?.path === freshVideoPath) break;
      } catch {
        // not written yet
      }
      await new Promise((r) => setTimeout(r, 100));
    }
    expect(uiState.transcriptVideoIdentity?.path).toBe(freshVideoPath);
  });
});
