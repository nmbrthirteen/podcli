import { describe, expect, it } from "vitest";
import { fullEpisodeOutputStem, parseFullEpisodeProgress } from "./full-episode-export.js";

describe("full episode export helpers", () => {
  it("builds a safe, recognizable output name", () => {
    expect(fullEpisodeOutputStem("/shows/My CEO Episode (final).mov"))
      .toBe("My-CEO-Episode--final_full_captioned");
    expect(fullEpisodeOutputStem("/shows/💬.mp4"))
      .toBe("episode_full_captioned");
  });

  it("parses renderer progress and clamps bad percentages", () => {
    expect(parseFullEpisodeProgress('PODCLI_PROGRESS={"percent":41.7,"message":"Rendering captions 3/8"}'))
      .toEqual({ percent: 42, message: "Rendering captions 3/8" });
    expect(parseFullEpisodeProgress('prefix PODCLI_PROGRESS={"percent":120,"message":"Finishing"}'))
      .toEqual({ percent: 100, message: "Finishing" });
    expect(parseFullEpisodeProgress("ordinary renderer output")).toBeNull();
    expect(parseFullEpisodeProgress("PODCLI_PROGRESS=not-json")).toBeNull();
  });
});
