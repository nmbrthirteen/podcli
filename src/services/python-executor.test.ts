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

  it("escalates to SIGKILL when the tree survives SIGTERM", () => {
    vi.useFakeTimers();
    const kill = vi.spyOn(process, "kill").mockReturnValue(true);
    terminateProcessTree(fakeProc(), 2000);

    expect(kill).toHaveBeenCalledWith(-4242, "SIGTERM");
    vi.advanceTimersByTime(2000);
    expect(kill).toHaveBeenCalledWith(-4242, "SIGKILL");
  });

  // The pid can be handed to an unrelated process once the child is reaped, and
  // the kill targets the whole group (-pid), so a late SIGKILL would take it out.
  it("cancels the escalation once the process exits", () => {
    vi.useFakeTimers();
    const kill = vi.spyOn(process, "kill").mockReturnValue(true);
    const proc = fakeProc();
    terminateProcessTree(proc, 2000);

    proc.emit("exit", null, "SIGTERM");
    vi.advanceTimersByTime(10_000);

    expect(kill).toHaveBeenCalledTimes(1);
    expect(kill).not.toHaveBeenCalledWith(-4242, "SIGKILL");
  });

  it("does not schedule a kill for an already-exited process", () => {
    vi.useFakeTimers();
    const kill = vi.spyOn(process, "kill").mockReturnValue(true);
    const proc = fakeProc();
    Object.assign(proc, { exitCode: 0 });
    terminateProcessTree(proc, 2000);

    vi.advanceTimersByTime(10_000);
    expect(kill).toHaveBeenCalledTimes(1);
  });
});
