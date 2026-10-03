import { describe, it, expect } from "vitest";
import { z } from "zod";
import { handleSuggestClips, hookSchema, suggestClipsInputShape } from "./suggest-clips.handler.js";

const base = {
  title: "Why the seed round cost them pricing",
  start_second: 100,
  end_second: 130,
  payoff: "You learn why raising early cost the founders control of pricing.",
  standalone: "nothing",
  reasoning: "Concrete stakes and a clear turn.",
  preview_text: "We raised a seed round before we had a single paying customer.",
};

describe("suggest_clips hook", () => {
  it("accepts a hook in the tool schema and rejects an unknown mode", () => {
    const schema = z.object(suggestClipsInputShape);
    expect(
      schema.safeParse({ suggestions: [{ ...base, hook: { start: 110, end: 113, mode: "move" } }] }).success,
    ).toBe(true);
    expect(hookSchema.safeParse({ start: 110, end: 113, mode: "loop" }).success).toBe(false);
    expect(hookSchema.nullable().safeParse(null).success).toBe(true);
  });

  it("stores a valid hook and counts it in the duration", async () => {
    const out = JSON.parse(
      await handleSuggestClips({
        suggestions: [{ ...base, hook: { start: 110, end: 113, mode: "repeat" } }],
      }),
    );
    expect(out.clips[0].hook).toEqual({ start: 110, end: 113, mode: "repeat" });
    expect(out.clips[0].duration).toBe(33);
  });

  it("rejects a hook outside the clip", async () => {
    await expect(
      handleSuggestClips({
        suggestions: [{ ...base, hook: { start: 90, end: 95, mode: "repeat" } }],
      }),
    ).rejects.toThrow(/not inside the clip body/);
  });

  it("rejects a hook longer than 15 seconds", async () => {
    await expect(
      handleSuggestClips({
        suggestions: [{ ...base, hook: { start: 105, end: 125, mode: "move" } }],
      }),
    ).rejects.toThrow(/between 1 and 15/);
  });
});
