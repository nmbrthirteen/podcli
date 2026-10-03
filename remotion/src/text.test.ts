import { describe, expect, it } from "vitest";
import { safeUpper } from "./text";

describe("safeUpper", () => {
  it("uppercases plain ASCII text", () => {
    expect(safeUpper("hello world")).toBe("HELLO WORLD");
  });

  it("leaves Georgian Mkhedruli text unchanged instead of switching to Mtavruli", () => {
    const georgian = "მიშა";
    expect(safeUpper(georgian)).toBe(georgian);
    // Sanity check the bug this guards against: toUpperCase() alone does
    // remap Mkhedruli to Mtavruli.
    expect(georgian.toUpperCase()).not.toBe(georgian);
  });

  it("uppercases the Latin parts of a mixed-script string and leaves Georgian alone", () => {
    expect(safeUpper("hello მიშა")).toBe("HELLO მიშა");
  });

  it("passes through empty strings", () => {
    expect(safeUpper("")).toBe("");
  });
});
