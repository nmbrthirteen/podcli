import { describe, expect, it } from "vitest";
import { parseThumbnailRender } from "./thumbnail-render-output.js";

describe("parseThumbnailRender", () => {
  it("reads the path and which source second each face came from", () => {
    const people = [
      { side: "left", role: "guest", from: "seats", source_time: 14 },
      { side: "right", role: "host", from: "seats", source_time: 40.5 },
    ];

    const out = `progress line\n${JSON.stringify({ path: "/out/t.png", layout: "pair", people })}\n`;
    expect(parseThumbnailRender(out)).toEqual({ path: "/out/t.png", report: { layout: "pair", people } });
  });

  it("carries the reason a pair render fell back to one face", () => {
    const note = "Only one person is on screen in this clip. The thumbnail uses the single-face layout.";
    const out = JSON.stringify({ path: "/out/t.png", layout: "single", note });
    expect(parseThumbnailRender(out).report).toEqual({ layout: "single", note });
  });

  it("answers an empty path for output it cannot read", () => {
    expect(parseThumbnailRender("not json")).toEqual({ path: "", report: { layout: "single" } });
  });
});
