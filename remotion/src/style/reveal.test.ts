import { describe, expect, it } from "vitest";
import { countedTo, entranceFrames, settle } from "./reveal";
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


describe("counting a figure up", () => {
  it("keeps the words around the number", () => {
    expect(countedTo("−95%", 1)).toBe("−95%");
    expect(countedTo("−95%", 0.5)).toBe("−48%");
    expect(countedTo("$1.2 trillion", 0.5)).toBe("$0.6 trillion");
  });

  it("keeps thousands separators the speaker's figure had", () => {
    expect(countedTo("10,000x", 0.25)).toBe("2,500x");
    expect(countedTo("10000x", 0.25)).toBe("2500x");
  });

  it("leaves a figure with no number alone", () => {
    expect(countedTo("half", 0.3)).toBe("half");
  });

  it("starts at zero and lands exactly on the figure", () => {
    expect(countedTo("3 weeks", 0)).toBe("0 weeks");
    expect(countedTo("3 weeks", 1)).toBe("3 weeks");
  });
});

describe("counting a figure that is not one number", () => {
  it("leaves a comma that is punctuation alone", () => {
    expect(countedTo("3, 2, 1", 0.5)).toBe("2, 2, 1");
  });

  it("shows the exact figure once the count lands", () => {
    expect(countedTo("9,007,199,254,740,993", 1)).toBe("9,007,199,254,740,993");
  });
});
