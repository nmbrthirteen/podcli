import { describe, it, expect } from "vitest";
import { formatSrtTime, formatVttTime } from "./srt-time.js";

describe("formatSrtTime", () => {
  it("formats a plain value", () => {
    expect(formatSrtTime(65.25)).toBe("00:01:05,250");
  });

  it("carries milliseconds that round up to 1000 into the next second", () => {
    // 1.9996 * 1000 = 1999.6 -> rounds to 2000ms, not "01,1000".
    expect(formatSrtTime(1.9996)).toBe("00:00:02,000");
  });

  it("carries a rounded second into the next minute", () => {
    expect(formatSrtTime(59.9996)).toBe("00:01:00,000");
  });

  it("carries through hours", () => {
    expect(formatSrtTime(3599.9996)).toBe("01:00:00,000");
  });
});

describe("formatVttTime", () => {
  it("uses a period instead of a comma", () => {
    expect(formatVttTime(1.9996)).toBe("00:00:02.000");
  });
});
