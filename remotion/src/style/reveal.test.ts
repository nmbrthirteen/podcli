import { describe, expect, it } from "vitest";
import { countedTo } from "./reveal";

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
