import { describe, it, expect } from "vitest";
import { resolveWebServerPort } from "./server.js";

describe("resolveWebServerPort", () => {
  it("defaults to 3847", () => {
    expect(resolveWebServerPort({})).toBe(3847);
  });

  it("reads PODCLI_PORT first", () => {
    expect(resolveWebServerPort({ PODCLI_PORT: "4000", PORT: "5000" })).toBe(4000);
  });

  it("falls back to PORT", () => {
    expect(resolveWebServerPort({ PORT: "5000" })).toBe(5000);
  });

  it("rejects garbage and out-of-range values", () => {
    expect(resolveWebServerPort({ PORT: "banana" })).toBe(3847);
    expect(resolveWebServerPort({ PORT: "0" })).toBe(3847);
    expect(resolveWebServerPort({ PORT: "70000" })).toBe(3847);
    expect(resolveWebServerPort({ PORT: "-1" })).toBe(3847);
  });
});
