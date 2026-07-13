import { describe, it, expect } from "vitest";
import { stderrTail } from "./python-executor.js";

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
