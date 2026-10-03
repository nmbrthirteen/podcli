import { describe, it, expect, vi, beforeEach } from "vitest";

const executeMock = vi.fn();
const cacheGetMock = vi.fn();
const cacheSetMock = vi.fn();
const cacheGetPackedMock = vi.fn();
const cacheGetHashForEngineMock = vi.fn();

vi.mock("../services/python-executor.js", () => ({
  PythonExecutor: class {
    execute = executeMock;
  },
}));

vi.mock("../services/transcript-cache.js", () => ({
  TranscriptCache: class {
    get = cacheGetMock;
    set = cacheSetMock;
    getPackedMarkdown = cacheGetPackedMock;
    getFileHashForEngine = cacheGetHashForEngineMock;
  },
  hasSpeakerLabels: () => true,
  needsDiarizationRetry: () => false,
}));

vi.mock("../services/engine-resolve.js", () => ({
  resolveTranscribeEngine: vi.fn().mockResolvedValue("whispercpp"),
  engineCanDiarize: () => false,
}));

const { handleTranscribe } = await import("./transcribe.handler.js");

describe("handleTranscribe, sample mode", () => {
  beforeEach(() => {
    executeMock.mockReset();
    cacheGetMock.mockReset();
    cacheSetMock.mockReset();
    cacheGetPackedMock.mockReset();
  });

  it("never reads the main cache when start_seconds/duration_seconds are set", async () => {
    executeMock.mockResolvedValue({
      data: { transcript: "hi", segments: [], words: [], duration: 40, language: "ka", engine: "whispercpp" },
    });

    const result = await handleTranscribe({
      file_path: "/video.mp4",
      start_seconds: 120,
      duration_seconds: 40,
    });

    expect(cacheGetMock).not.toHaveBeenCalled();
    const parsed = JSON.parse(result);
    expect(parsed.cached).toBe(false);
    expect(parsed.packed_ready).toBe(false);
  });

  it("never writes the main cache for a sample result", async () => {
    executeMock.mockResolvedValue({
      data: {
        transcript: "hi",
        segments: [],
        words: [],
        duration: 40,
        language: "ka",
        engine: "whispercpp",
        complete: false,
        sample_offset_seconds: 120,
      },
    });

    const result = await handleTranscribe({
      file_path: "/video.mp4",
      start_seconds: 120,
      duration_seconds: 40,
    });

    expect(cacheSetMock).not.toHaveBeenCalled();
    const parsed = JSON.parse(result);
    expect(parsed.complete).toBe(false);
    expect(parsed.sample_offset_seconds).toBe(120);
  });

  it("passes start_seconds/duration_seconds through to the transcribe task", async () => {
    executeMock.mockResolvedValue({
      data: { transcript: "hi", segments: [], words: [], duration: 40, language: "ka", engine: "whispercpp" },
    });

    await handleTranscribe({ file_path: "/video.mp4", start_seconds: 10, duration_seconds: 30 });

    expect(executeMock).toHaveBeenCalledWith(
      "transcribe",
      expect.objectContaining({ start_seconds: 10, duration_seconds: 30 }),
    );
  });

  it("treats duration_seconds: 0 as a normal (non-sample) request", async () => {
    cacheGetMock.mockResolvedValue(null);
    executeMock.mockResolvedValue({
      data: { transcript: "hi", segments: [], words: [], duration: 400, language: "en", engine: "whispercpp" },
    });
    cacheGetPackedMock.mockResolvedValue("# packed");

    const result = await handleTranscribe({ file_path: "/video.mp4", duration_seconds: 0 });

    expect(cacheGetMock).toHaveBeenCalled();
    expect(cacheSetMock).toHaveBeenCalled();
    const parsed = JSON.parse(result);
    expect(parsed.complete).toBeUndefined();
  });

  it("still uses the main cache for a normal (non-sample) request", async () => {
    cacheGetMock.mockResolvedValue(null);
    executeMock.mockResolvedValue({
      data: { transcript: "hi", segments: [], words: [], duration: 400, language: "en", engine: "whispercpp" },
    });
    cacheGetPackedMock.mockResolvedValue("# packed");

    await handleTranscribe({ file_path: "/video.mp4" });

    expect(cacheGetMock).toHaveBeenCalled();
    expect(cacheSetMock).toHaveBeenCalled();
  });
});
