import { describe, it, expect } from "vitest";
import { blockHeight, countBlocks, blockDepth, fitScale, hasMedia, sceneHeight } from "./scene";
import type { Block } from "./scene";
import { cardAt } from "./cards";
import type { Card } from "./cards";

const text = (words: string, size: "sm" | "md" | "xl" | "xxl" = "md"): Block =>
  ({ type: "text", text: words, size });

describe("fitting a scene into the room it was given", () => {
  it("leaves a card that already fits alone", () => {
    expect(fitScale([text("Hiring stopped", "xl")], { room: 900 })).toBe(1);
  });

  it("scales a card down rather than letting it run past its room", () => {
    const blocks = Array.from({ length: 6 }, (_, i) => text(`A line of some length ${i}`, "xl"));
    const room = 900;
    const fit = fitScale(blocks, { room });
    expect(fit).toBeLessThan(1);
    expect(sceneHeight(blocks, "stack", "group") * fit).toBeLessThanOrEqual(room + 1);
  });

  /**
   * Past the floor the card is clipped rather than shrunk on, and the renderer
   * answers by giving it the whole frame. Shrinking to fit whatever it is
   * handed is how a card ends up technically inside its room and unreadable on
   * a phone held at arm's length.
   */
  it("stops shrinking at the size below which the card is small, not fitted", () => {
    const blocks = Array.from({ length: 20 }, (_, i) => text(`Another long line here ${i}`, "xxl"));
    expect(fitScale(blocks, { room: 100 })).toBe(0.55);
  });

  it("measures a wrapped line as taller than one that fits across", () => {
    const short = blockHeight(text("Short"), 888);
    const long = blockHeight(
      text("A sentence long enough to wrap across more than one line of this frame"),
      888,
    );
    expect(long).toBeGreaterThan(short);
  });

  it("gives a row the width it actually has rather than the whole frame", () => {
    const words = "A sentence long enough to wrap when it only has half the frame";
    const alone = sceneHeight([text(words)], "stack", "group");
    const beside = sceneHeight(
      [{ type: "group", layout: "row", blocks: [text(words), text(words)] }],
      "stack",
      "group",
    );
    expect(beside).toBeGreaterThan(alone);
  });
});

describe("what a scene is made of", () => {
  const nested: Block[] = [
    text("top"),
    { type: "group", layout: "row", blocks: [text("left"), { type: "media", media: "image", src: "a.png" }] },
  ];

  it("counts every block, groups included", () => {
    expect(countBlocks(nested)).toBe(4);
    expect(blockDepth(nested)).toBe(2);
  });

  it("finds a file however deep it sits", () => {
    expect(hasMedia(nested)).toBe(true);
    expect(hasMedia([text("nothing here")])).toBe(false);
  });
});

describe("the card in front at a moment", () => {
  it("lets a scene take its window like any other kind", () => {
    const cards: Card[] = [
      { kind: "quote", start: 0, end: 3, text: "first" },
      { kind: "scene", start: 3, end: 6, blocks: [text("second")] },
    ];
    expect(cardAt(cards, 1)?.kind).toBe("quote");
    expect(cardAt(cards, 4)?.kind).toBe("scene");
    // Half-open, so a card ending where the next begins is a cut.
    expect(cardAt(cards, 3)?.kind).toBe("scene");
    expect(cardAt(cards, 6)).toBeNull();
  });
});

