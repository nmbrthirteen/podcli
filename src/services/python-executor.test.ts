import { EventEmitter } from "events";
import type { ChildProcess } from "child_process";
import { describe, it, expect, vi, afterEach } from "vitest";
import { stderrTail, terminateProcessTree } from "./python-executor.js";

describe("stderrTail", () => {
  it("returns short stderr unchanged", () => {
    expect(stderrTail("boom")).toBe("boom");
  });

  it("keeps the tail of long stderr", () => {
    const noise = "x".repeat(10_000) + "END";
    const tail = stderrTail(noise);
    expect(tail.length).toBe(4000);
    expect(tail.endsWith("END")).toBe(true);
  });

  it("prefers the last traceback block", () => {
    const stderr = [
      "progress line",
      "Traceback (most recent call last):",
      '  File "old.py", line 1',
      "OldError: first failure",
      "retrying...",
      "Traceback (most recent call last):",
      '  File "new.py", line 9',
      "ValueError: actual failure",
    ].join("\n");
    const tail = stderrTail(stderr);
    expect(tail.startsWith("Traceback (most recent call last):")).toBe(true);
    expect(tail).toContain("ValueError: actual failure");
    expect(tail).not.toContain("OldError");
  });

  it("truncates a huge traceback to the tail", () => {
    const stderr = "Traceback (most recent call last):\n" + "y".repeat(9000) + "\nValueError: end";
    const tail = stderrTail(stderr);
    expect(tail.length).toBe(4000);
    expect(tail.endsWith("ValueError: end")).toBe(true);
  });
});

describe("terminateProcessTree", () => {
  const fakeProc = () => {
    const proc = new EventEmitter() as unknown as ChildProcess;
    Object.assign(proc, { pid: 4242, exitCode: null, signalCode: null, kill: vi.fn() });
    return proc;
  };

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("escalates a POSIX process group to SIGKILL", () => {
    vi.useFakeTimers();
    const proc = fakeProc();
    const kill = vi.spyOn(process, "kill").mockReturnValue(true);
    terminateProcessTree(proc, 2000, { platform: "linux" });

    expect(kill).toHaveBeenCalledWith(-4242, "SIGTERM");
    vi.advanceTimersByTime(2000);
    expect(kill).toHaveBeenCalledWith(-4242, "SIGKILL");
  });

  it("cancels POSIX escalation once the process exits", () => {
    vi.useFakeTimers();
    const proc = fakeProc();
    const kill = vi.spyOn(process, "kill").mockReturnValue(true);
    terminateProcessTree(proc, 2000, { platform: "linux" });

    proc.emit("exit", null, "SIGTERM");
    vi.advanceTimersByTime(10_000);

    expect(kill).toHaveBeenCalledTimes(1);
  });

  it("does not signal an already-exited POSIX process group", () => {
    vi.useFakeTimers();
    const proc = fakeProc();
    Object.assign(proc, { exitCode: 0 });
    const kill = vi.spyOn(process, "kill").mockReturnValue(true);
    terminateProcessTree(proc, 2000, { platform: "linux" });

    vi.advanceTimersByTime(10_000);
    expect(kill).not.toHaveBeenCalled();
  });

  it("terminates the full Windows process tree", () => {
    vi.useFakeTimers();
    const proc = fakeProc();
    const taskkillProc = new EventEmitter();
    const spawnProcess = vi.fn().mockReturnValue(taskkillProc);
    const kill = vi.spyOn(process, "kill").mockReturnValue(true);

    terminateProcessTree(proc, 2000, {
      platform: "win32",
      spawnProcess: spawnProcess as never,
    });

    expect(spawnProcess).toHaveBeenCalledWith(
      "taskkill",
      ["/pid", "4242", "/T", "/F"],
      { stdio: "ignore", windowsHide: true }
    );
    expect(proc.kill).not.toHaveBeenCalled();
    expect(kill).not.toHaveBeenCalled();
    expect(vi.getTimerCount()).toBe(0);
  });
});
