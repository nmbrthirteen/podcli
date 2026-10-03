import { describe, it, expect, vi } from "vitest";
import type { PythonExecutor } from "./python-executor.js";
import { resolveTranscribeEngine, engineCanDiarize } from "./engine-resolve.js";

function fakeExecutor(response: unknown): PythonExecutor {
  return { execute: vi.fn().mockResolvedValue(response) } as unknown as PythonExecutor;
}

describe("resolveTranscribeEngine", () => {
  it("returns what the backend predicts it will resolve to", async () => {
    const executor = fakeExecutor({ data: { engine: "whispercpp" } });
    const engine = await resolveTranscribeEngine(executor, undefined, "base");
    expect(engine).toBe("whispercpp");
    expect(executor.execute).toHaveBeenCalledWith("resolve_transcribe_engine", {
      engine: undefined,
      model_size: "base",
    });
  });

  it("passes an explicit engine through for the backend to normalize", async () => {
    const executor = fakeExecutor({ data: { engine: "assemblyai" } });
    const engine = await resolveTranscribeEngine(executor, "assemblyai", "base");
    expect(engine).toBe("assemblyai");
  });

  it("falls back to the raw request if resolution itself fails", async () => {
    const executor = { execute: vi.fn().mockRejectedValue(new Error("boom")) } as unknown as PythonExecutor;
    const engine = await resolveTranscribeEngine(executor, "whisper-py", "base");
    expect(engine).toBe("whisper-py");
  });

  it("falls back to whisper-py when both the request and the resolution are empty", async () => {
    const executor = { execute: vi.fn().mockRejectedValue(new Error("boom")) } as unknown as PythonExecutor;
    const engine = await resolveTranscribeEngine(executor, undefined, "base");
    expect(engine).toBe("whisper-py");
  });
});

describe("engineCanDiarize", () => {
  it("is false for whisper.cpp and omnilingual, which never diarize", () => {
    expect(engineCanDiarize("whispercpp")).toBe(false);
    expect(engineCanDiarize("omnilingual")).toBe(false);
  });

  it("is true for whisper-py and assemblyai", () => {
    expect(engineCanDiarize("whisper-py")).toBe(true);
    expect(engineCanDiarize("assemblyai")).toBe(true);
  });

  it("is true (the safe default) for an unresolved engine", () => {
    expect(engineCanDiarize(undefined)).toBe(true);
  });
});
