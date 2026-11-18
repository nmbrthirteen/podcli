import { describe, it, expect } from "vitest";
import { resolveCleanFillers } from "./clean-fillers.js";

describe("resolveCleanFillers", () => {
  it("keeps the session default for the short formats", () => {
    for (const format of [undefined, "vertical", "horizontal", "square"]) {
      expect(resolveCleanFillers(undefined, true, format)).toBe(true);
      expect(resolveCleanFillers(undefined, false, format)).toBe(false);
      expect(resolveCleanFillers(undefined, undefined, format)).toBe(true);
    }
  });

  it("leaves topic clips to the renderer unless asked", () => {
    expect(resolveCleanFillers(undefined, true, "topic")).toBeUndefined();
    expect(resolveCleanFillers(undefined, false, "topic")).toBeUndefined();
    expect(resolveCleanFillers(undefined, undefined, "topic")).toBeUndefined();
  });

  it("honors an explicit request on every format", () => {
    expect(resolveCleanFillers(true, false, "topic")).toBe(true);
    expect(resolveCleanFillers(false, true, "topic")).toBe(false);
    expect(resolveCleanFillers(false, true, "vertical")).toBe(false);
    expect(resolveCleanFillers(true, false, "vertical")).toBe(true);
  });
});
