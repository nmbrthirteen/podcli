import { describe, it, expect } from "vitest";
import { validateClipRange, validateSuggestionRange, maxClipSeconds } from "./clip-validation.js";

describe("validateClipRange", () => {
  it("accepts a normal vertical clip", () => {
    expect(validateClipRange(10, 40)).toBeNull();
  });

  it("rejects non-numbers", () => {
    expect(validateClipRange("10" as unknown, 40)).toMatch(/must be numbers/);
    expect(validateClipRange(10, NaN)).toMatch(/must be numbers/);
    expect(validateClipRange(undefined, undefined)).toMatch(/must be numbers/);
  });

  it("rejects negative start", () => {
    expect(validateClipRange(-1, 20)).toMatch(/>= 0/);
  });

  it("rejects end <= start", () => {
    expect(validateClipRange(30, 30)).toMatch(/greater than start_second/);
    expect(validateClipRange(30, 10)).toMatch(/greater than start_second/);
  });

  it("caps vertical clips at 180s and horizontal at 300s", () => {
    expect(validateClipRange(0, 180)).toBeNull();
    expect(validateClipRange(0, 181)).toMatch(/Max 180 seconds/);
    expect(validateClipRange(0, 300, "horizontal")).toBeNull();
    expect(validateClipRange(0, 301, "horizontal")).toMatch(/Max 300 seconds/);
  });

  it("maxClipSeconds matches the range check", () => {
    expect(maxClipSeconds()).toBe(180);
    expect(maxClipSeconds("vertical")).toBe(180);
    expect(maxClipSeconds("horizontal")).toBe(300);
  });
});

describe("validateSuggestionRange", () => {
  it("accepts ranges longer than render limits", () => {
    expect(validateSuggestionRange(0, 400)).toBeNull();
  });

  it("rejects end <= start", () => {
    expect(validateSuggestionRange(50, 50)).toMatch(/greater than start_second/);
  });

  it("rejects absurdly long ranges", () => {
    expect(validateSuggestionRange(0, 601)).toMatch(/Max 600 seconds/);
  });

  it("rejects non-numbers", () => {
    expect(validateSuggestionRange(null, 10)).toMatch(/must be numbers/);
  });
});
