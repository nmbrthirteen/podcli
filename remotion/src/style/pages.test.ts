import { describe, expect, it } from "vitest";
import { buildFrames, paced, tempo } from "./pages";
import { entranceFrames } from "./reveal";
import { PACKS } from "./theme";
import type { Card } from "../cards";

const quote: Card = {
  kind: "quote", start: 3.5, end: 4.8, text: "The most high leverage technology thing you can do",
};

describe("paced", () => {
  it("speeds typing up until it finishes in under half the card", () => {
    const length = 39;
    const theme = paced(PACKS.collage, quote, length);
    expect(entranceFrames(theme.motion, "type", quote.text)).toBeLessThanOrEqual(length * 0.45 + theme.motion.holdEvery);
  });

  it("leaves typing alone when the card has room for it", () => {
    expect(paced(PACKS.collage, quote, 600)).toBe(PACKS.collage);
  });
});

describe("tempo", () => {
  const stat: Card = { kind: "stat", start: 3.5, end: 4.8, eyebrow: "Stanford research", value: "1 year", caption: "matched by Claude in three weeks" };

  it("speeds the whole build up when the card is short", () => {
    expect(tempo(PACKS.collage, stat, 39)).toBeGreaterThan(1);
  });

  it("leaves a long card at its own pace", () => {
    expect(tempo(PACKS.collage, stat, 600)).toBe(1);
  });

  it("fits the sped-up build inside the first half of the card", () => {
    const length = 60;
    expect(buildFrames(PACKS.collage, stat) / tempo(PACKS.collage, stat, length)).toBeLessThanOrEqual(length * 0.55 + 0.001);
  });
});
