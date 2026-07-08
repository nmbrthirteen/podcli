import { describe, expect, it } from "vitest";
import { fmt, fmtMs } from "./lib";

describe("fmt", () => {
  it("formats sub-hour timestamps as m:ss", () => {
    expect(fmt(0)).toBe("0:00");
    expect(fmt(9)).toBe("0:09");
    expect(fmt(767)).toBe("12:47");
    expect(fmt(3599)).toBe("59:59");
  });

  // A podcast past the hour mark rendered as "78:31" instead of "1:18:31".
  it("rolls into hours", () => {
    expect(fmt(3600)).toBe("1:00:00");
    expect(fmt(4711)).toBe("1:18:31");
    expect(fmt(7325)).toBe("2:02:05");
  });

  it("clamps negatives", () => {
    expect(fmt(-5)).toBe("0:00");
  });
});

describe("fmtMs", () => {
  it("appends milliseconds", () => {
    expect(fmtMs(12.5)).toBe("0:12.500");
    expect(fmtMs(4711.25)).toBe("1:18:31.250");
  });
});
