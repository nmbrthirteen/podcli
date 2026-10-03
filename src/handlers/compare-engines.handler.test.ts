import { describe, it, expect, vi, beforeEach } from "vitest";

const executeMock = vi.fn();

vi.mock("../services/python-executor.js", () => ({
  PythonExecutor: class {
    execute = executeMock;
  },
}));

const { handleCompareEngines } = await import("./compare-engines.handler.js");

describe("handleCompareEngines", () => {
  beforeEach(() => {
    executeMock.mockReset();
  });

  it("passes all params through to the compare_engines task", async () => {
    executeMock.mockResolvedValue({
      data: { overall_disagreement: 0.2, both_empty_window_count: 1, json_path: "/x/comparison.json", html_path: "/x/comparison.html" },
    });

    await handleCompareEngines({
      file_path: "/video.mp4",
      engine_a: "whispercpp",
      engine_b: "whisper-py",
      start_seconds: 10,
      duration_seconds: 60,
      window_seconds: 20,
      model_size: "base",
      language: "ka",
      output_dir: "/tmp/cmp",
    });

    expect(executeMock).toHaveBeenCalledWith("compare_engines", {
      file_path: "/video.mp4",
      engine_a: "whispercpp",
      engine_b: "whisper-py",
      start_seconds: 10,
      duration_seconds: 60,
      window_seconds: 20,
      model_size: "base",
      language: "ka",
      output_dir: "/tmp/cmp",
    });
  });

  it("returns a summary labeled as disagreement, not accuracy", async () => {
    executeMock.mockResolvedValue({
      data: { overall_disagreement: 0.35, both_empty_window_count: 0, json_path: "/x/comparison.json", html_path: "/x/comparison.html" },
    });

    const text = await handleCompareEngines({
      file_path: "/video.mp4",
      engine_a: "whispercpp",
      engine_b: "whisper-py",
    });
    const parsed = JSON.parse(text);

    expect(parsed.overall_disagreement).toBe(0.35);
    expect(parsed.note).toMatch(/disagreement/i);
    expect(parsed.note).toContain("not accuracy against a transcript");
  });

  it("throws when the backend returns no data", async () => {
    executeMock.mockResolvedValue({ data: undefined });
    await expect(
      handleCompareEngines({ file_path: "/video.mp4", engine_a: "whispercpp", engine_b: "whisper-py" }),
    ).rejects.toThrow(/no data/i);
  });
});
