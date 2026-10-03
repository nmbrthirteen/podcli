import { describe, expect, it } from "vitest";
import { needsFrame, parseThumbnailRender, thumbnailRenderArgs } from "./thumbnail-layout.js";

const clip = { source_video: "/eps/ep1.mp4", start_second: 12.5, end_second: 48 };

describe("thumbnailRenderArgs", () => {
  it("takes both people from the clip's own range for the pair layout", () => {
    const args = thumbnailRenderArgs({ output: "/out/t.png", layout: "pair", swap: true, clip });
    expect(args).toEqual([
      "thumbnail-render", "--output", "/out/t.png", "--layout", "pair",
      "--video", "/eps/ep1.mp4", "--start", "12.5", "--end", "48", "--swap",
    ]);
  });

  it("keeps a chosen frame as the pair layout's fallback but drops its face metadata", () => {
    const args = thumbnailRenderArgs({
      output: "/out/t.png", frame: "/f.jpg", frameInfo: { face_x_pct: 40 }, layout: "pair", clip,
    });
    expect(args).toContain("--frame");
    expect(args).not.toContain("--frame-info");
    expect(args).not.toContain("--swap");
  });

  it("renders one face from the chosen frame without touching the video", () => {
    const args = thumbnailRenderArgs({
      output: "/out/t.png", frame: "/f.jpg", frameInfo: { face_x_pct: 40 }, line1: "One", line2: "Two", clip,
    });
    expect(args).toEqual([
      "thumbnail-render", "--output", "/out/t.png", "--frame", "/f.jpg",
      "--line1=One", "--line2=Two", "--frame-info", '{"face_x_pct":40}',
    ]);
  });

  it("ignores a layout it does not know", () => {
    expect(thumbnailRenderArgs({ output: "/o.png", frame: "/f.jpg", layout: "grid", clip })).not.toContain("--layout");
  });
});

describe("needsFrame", () => {
  it("lets the pair layout render without a chosen frame", () => {
    expect(needsFrame("pair")).toBe(false);
    expect(needsFrame("single")).toBe(true);
    expect(needsFrame(undefined)).toBe(true);
  });
});

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
