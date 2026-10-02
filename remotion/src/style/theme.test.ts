import { describe, expect, it } from "vitest";
import { ENTRANCES, FONT_IDS, MOTIONS, PACKS, ROLES, resolveTheme } from "./theme";

describe("resolveTheme", () => {
  it("returns null without a style", () => {
    expect(resolveTheme(null)).toBeNull();
  });

  it("starts from the pack and its own motion", () => {
    const theme = resolveTheme({ pack: "editorial" });
    expect(theme?.color.accent).toBe(PACKS.editorial.color.accent);
    expect(theme?.motion).toEqual(MOTIONS.highlighter);
  });

  it("swaps the motion without touching the look", () => {
    const theme = resolveTheme({ pack: "collage", motion: "kinetic" });
    expect(theme?.motion.camera).toBe("punch");
    expect(theme?.edge.kind).toBe("torn");
  });

  it("applies overrides on top of the motion preset", () => {
    const theme = resolveTheme({ pack: "clean", motion: "calm", overrides: { motion: { frames: 20 } } });
    expect(theme?.motion.frames).toBe(20);
    expect(theme?.motion.roles.title).toBe("fade");
  });

  it("refuses gradients and image urls in overrides", () => {
    const theme = resolveTheme({
      pack: "clean",
      overrides: { color: { ground: "linear-gradient(red, blue)", accent: "url(x.png)", ink: "#FFFFFF" } },
    });
    expect(theme?.color.ground).toBe(PACKS.clean.color.ground);
    expect(theme?.color.accent).toBe(PACKS.clean.color.accent);
    expect(theme?.color.ink).toBe("#FFFFFF");
  });

  it("carries no gradient anywhere in the packs or presets", () => {
    const text = JSON.stringify({ PACKS, MOTIONS });
    expect(text).not.toMatch(/gradient/i);
  });
});

describe("caption choice", () => {
  it("keeps the pack's caption skin by default", () => {
    expect(resolveTheme({ pack: "collage" })?.captions).toBe("pack");
  });

  it("takes any caption preset on its own", () => {
    expect(resolveTheme({ pack: "collage", motion: "kinetic", overrides: { captions: "hormozi" } })?.captions).toBe("hormozi");
  });

  it("closes every motion preset with a fade rather than a cut", () => {
    for (const motion of Object.values(MOTIONS)) expect(motion.exit).toBe("fade");
  });
});

describe("motion presets", () => {
  it("name an entrance for every role and leave by fading", () => {
    for (const [id, motion] of Object.entries(MOTIONS)) {
      expect(Object.keys(motion.roles).sort(), id).toEqual([...ROLES].sort());
      for (const entrance of Object.values(motion.roles)) expect(ENTRANCES, id).toContain(entrance);
      expect(motion.exit, id).toBe("fade");
    }
  });

  it("resolve on every pack", () => {
    for (const pack of Object.keys(PACKS) as (keyof typeof PACKS)[]) {
      for (const motion of Object.keys(MOTIONS) as (keyof typeof MOTIONS)[]) {
        expect(resolveTheme({ pack, motion })?.motion).toEqual(MOTIONS[motion]);
      }
    }
  });
});

describe("fonts picked to match a reference", () => {
  it("swap the display and label faces and leave the rest of the pack", () => {
    const theme = resolveTheme({ pack: "clean", fonts: { display: "condensed", label: "mono" } });
    expect(theme?.type.display.family).toContain("Barlow Condensed");
    expect(theme?.type.label.family).toContain("Courier Prime");
    expect(theme?.color).toEqual(PACKS.clean.color);
  });

  it("keep the pack's faces when none are picked", () => {
    expect(resolveTheme({ pack: "editorial" })?.type).toEqual(PACKS.editorial.type);
  });

  it("offer every font on every pack", () => {
    for (const font of FONT_IDS) {
      expect(resolveTheme({ pack: "riso", fonts: { display: font } })?.type.display.family).toBeTruthy();
    }
  });
});
