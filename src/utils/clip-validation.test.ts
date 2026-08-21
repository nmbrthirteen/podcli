import { describe, it, expect } from "vitest";
import {
  validateClipRange,
  validateSuggestionRange,
  validateSuggestionContext,
  findOrphanOpener,
  isSelfContained,
  maxClipSeconds,
} from "./clip-validation.js";

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

describe("findOrphanOpener", () => {
  it("flags replies that point back before the cut", () => {
    expect(findOrphanOpener("Yeah, and that's exactly why it failed.")).toBe("yeah");
    expect(findOrphanOpener("That was the moment we knew.")).toBe("that");
    expect(findOrphanOpener("Because nobody wanted to pay for it.")).toBe("because");
    expect(findOrphanOpener("I mean, we were broke.")).toBe("i mean");
  });

  it("lets self-contained openers through", () => {
    expect(findOrphanOpener("Most founders raise too early.")).toBeNull();
    expect(findOrphanOpener("So many founders quit at month six.")).toBeNull();
    expect(findOrphanOpener("It takes ten years to build a brand.")).toBeNull();
  });

  it("ignores leading punctuation and empty input", () => {
    expect(findOrphanOpener('"Exactly what I told them."')).toBe("exactly");
    expect(findOrphanOpener("")).toBeNull();
    expect(findOrphanOpener(undefined)).toBeNull();
  });
});

describe("validateSuggestionContext", () => {
  const good = {
    title: "Raising too early cost them pricing power",
    payoff: "You learn why an early seed round took their pricing control away.",
    standalone: "nothing",
    preview_text: "Most founders raise before they have pricing power.",
  };

  it("accepts a clip that carries its own context", () => {
    expect(validateSuggestionContext(good)).toBeNull();
  });

  it("requires a payoff", () => {
    expect(validateSuggestionContext({ ...good, payoff: "" })).toMatch(/payoff is required/);
    expect(validateSuggestionContext({ ...good, payoff: "Good story" })).toMatch(/too thin/);
  });

  it("rejects a payoff that only restates the title", () => {
    expect(
      validateSuggestionContext({ ...good, payoff: "Raising too early cost them pricing power!" }),
    ).toMatch(/restates the title/);
  });

  it("requires standalone", () => {
    expect(validateSuggestionContext({ ...good, standalone: "" })).toMatch(
      /standalone is required/,
    );
  });

  it("demands a context_line when the viewer needs prior knowledge", () => {
    const needsSetup = { ...good, standalone: "that the company had just been acquired" };
    expect(validateSuggestionContext(needsSetup)).toMatch(/context_line is empty/);
    expect(
      validateSuggestionContext({ ...needsSetup, context_line: "Right after the acquisition:" }),
    ).toBeNull();
  });

  it("catches an orphaned answer even when standalone claims nothing", () => {
    const orphan = { ...good, preview_text: "Yeah, and that's exactly why we shut it down." };
    expect(validateSuggestionContext(orphan)).toMatch(/points at something said before the cut/);
    expect(
      validateSuggestionContext({ ...orphan, context_line: "Why did you kill the product?" }),
    ).toBeNull();
  });

  it("accepts a payoff in a language that does not use the Latin alphabet", () => {
    expect(
      validateSuggestionContext({
        ...good,
        title: "რატომ დაიხურა პროდუქტი",
        payoff: "გაიგებ, რა სიგნალმა აიძულა ისინი პროდუქტი დაეხურათ.",
      }),
    ).toBeNull();
    expect(
      validateSuggestionContext({ ...good, title: "なぜ畳んだのか", payoff: "撤退を決めた合図が分かります。" }),
    ).toBeNull();
  });

  it("isSelfContained treats the empty and 'none' forms as no context needed", () => {
    expect(isSelfContained("nothing")).toBe(true);
    expect(isSelfContained("None.")).toBe(true);
    expect(isSelfContained("who the guest is")).toBe(false);
  });
});
