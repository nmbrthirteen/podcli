import { describe, expect, it } from "vitest";
import { resolveByteRange } from "./http-range.js";

describe("resolveByteRange", () => {
  it("parses bounded and open-ended ranges", () => {
    expect(resolveByteRange("bytes=10-19", 100)).toEqual({ start: 10, end: 19 });
    expect(resolveByteRange("bytes=90-", 100)).toEqual({ start: 90, end: 99 });
    expect(resolveByteRange("bytes=90-200", 100)).toEqual({ start: 90, end: 99 });
  });

  it("parses browser suffix ranges", () => {
    expect(resolveByteRange("bytes=-20", 100)).toEqual({ start: 80, end: 99 });
    expect(resolveByteRange("bytes=-200", 100)).toEqual({ start: 0, end: 99 });
  });

  it("rejects malformed or unsatisfiable ranges", () => {
    expect(resolveByteRange("bytes=-0", 100)).toBeNull();
    expect(resolveByteRange("bytes=100-", 100)).toBeNull();
    expect(resolveByteRange("bytes=20-10", 100)).toBeNull();
    expect(resolveByteRange("bytes=0-1,5-6", 100)).toBeNull();
  });
});
