import { describe, expect, it } from "vitest";
import { DEFAULT_PACK_TEXTURES, withDefaultTextures } from "./textures";

describe("withDefaultTextures", () => {
  it("passes through a null theme", () => {
    expect(withDefaultTextures(null)).toBeNull();
  });

  it("fills the default pack textures when a theme names none", () => {
    const theme = withDefaultTextures({ pack: "collage" });
    expect(theme?.textures).toEqual(DEFAULT_PACK_TEXTURES);
  });

  it("keeps a theme's own textures rather than overwriting them", () => {
    const ownTextures = { paper: "custom/paper.png" };
    const theme = withDefaultTextures({ pack: "collage", textures: ownTextures });
    expect(theme?.textures).toBe(ownTextures);
  });

  it("resolves the baked textures under the style/ static folder", () => {
    expect(DEFAULT_PACK_TEXTURES.paper).toBe("/style/detail-paper.png");
    expect(DEFAULT_PACK_TEXTURES.kraft).toBe("/style/detail-kraft.png");
    expect(DEFAULT_PACK_TEXTURES.tape).toEqual([
      "/style/tape-1.png",
      "/style/tape-2.png",
      "/style/tape-3.png",
    ]);
  });
});
