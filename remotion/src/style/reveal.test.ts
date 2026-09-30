import { describe, expect, it } from "vitest";
import { entranceFrames, settle } from "./reveal";
import { MOTIONS } from "./theme";

describe("settle", () => {
  it("starts at rest and lands on the mark", () => {
    for (const bounces of [0, 1, 2, 3]) {
      expect(settle(0, bounces)).toBeCloseTo(0);
      expect(settle(1, bounces)).toBeCloseTo(1);
    }
  });

  it("never passes the mark without bounces", () => {
    const peak = Math.max(...Array.from({ length: 101 }, (_, i) => settle(i / 100, 0)));
    expect(peak).toBeLessThanOrEqual(1.0001);
  });

  it("swings past the mark once per bounce asked for", () => {
    const crossings = (bounces: number) => {
      let count = 0;
      let above = false;
      for (let i = 1; i < 200; i++) {
        const now = settle(i / 200, bounces) > 1;
        if (now !== above) count++;
        above = now;
      }
      return count;
    };
    expect(crossings(1)).toBe(1);
    expect(crossings(2)).toBe(2);
  });
});

describe("entranceFrames", () => {
  it("types a line word by word in poses", () => {
    const motion = { ...MOTIONS["stop-motion"], textUnit: "word" as const };
    expect(entranceFrames(motion, "type", "Space is becoming a factory")).toBe(5 * 2);
  });

  it("types a line character by character in poses", () => {
    const motion = MOTIONS["stop-motion"];
    expect(entranceFrames(motion, "type", "Cost")).toBe(4 * 2);
  });

  it("gives a pop one frame and every other entrance the motion length", () => {
    const motion = MOTIONS.smooth;
    expect(entranceFrames(motion, "pop")).toBe(1);
    expect(entranceFrames(motion, "mask-circle")).toBe(motion.frames);
  });
});
