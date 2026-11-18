import { describe, it, expect, vi } from "vitest";
import type { TaskExecutor } from "./python-executor.js";
import { resolveTranscribeInfo, engineCanDiarize } from "./engine-resolve.js";

function fakeExecutor(response: { data: { engine: string; model_size?: string } }): TaskExecutor {
  return { execute: vi.fn().mockResolvedValue(response) };
}

function failingExecutor(): TaskExecutor {
  return { execute: vi.fn().mockRejectedValue(new Error("boom")) };
}

describe("resolveTranscribeInfo", () => {
  it("returns what the backend predicts it will resolve to", async () => {
    const executor = fakeExecutor({ data: { engine: "whispercpp", model_size: "base" } });
    const info = await resolveTranscribeInfo(executor, undefined, "small");
    expect(info).toEqual({ engine: "whispercpp", model: "base" });
    expect(executor.execute).toHaveBeenCalledWith("resolve_transcribe_engine", {
      engine: undefined,
      model_size: "small",
    });
  });

  it("keeps the requested model when the backend omits model_size", async () => {
    const executor = fakeExecutor({ data: { engine: "whispercpp" } });
    const info = await resolveTranscribeInfo(executor, undefined, "medium");
    expect(info.model).toBe("medium");
  });

  it("falls back to the raw request if resolution itself fails", async () => {
    const executor = failingExecutor();
    const info = await resolveTranscribeInfo(executor, "whisper-py", "small");
    expect(info).toEqual({ engine: "whisper-py", model: "small" });
  });

  it("falls back to whisper-py when both the request and the resolution are empty", async () => {
    const executor = failingExecutor();
    const info = await resolveTranscribeInfo(executor, undefined, "small");
    expect(info.engine).toBe("whisper-py");
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
