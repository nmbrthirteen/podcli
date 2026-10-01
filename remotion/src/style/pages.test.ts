import { describe, expect, it } from "vitest";
import { paced } from "./pages";
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
