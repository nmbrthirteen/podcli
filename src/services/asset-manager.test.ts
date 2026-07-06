import { describe, it, expect, beforeEach } from "vitest";
import { mkdtempSync, writeFileSync, rmSync, mkdirSync } from "fs";
import { tmpdir } from "os";
import { join } from "path";

const tmp = mkdtempSync(join(tmpdir(), "podcli-assets-test-"));
process.env.PODCLI_HOME = tmp;
process.env.PODCLI_DATA = tmp;

const { AssetManager } = await import("./asset-manager.js");

function makeFile(name: string): string {
  const p = join(tmp, name);
  writeFileSync(p, "stub");
  return p;
}

describe("AssetManager", () => {
  let manager: InstanceType<typeof AssetManager>;

  beforeEach(() => {
    rmSync(join(tmp, "assets"), { recursive: true, force: true });
    mkdirSync(join(tmp, "assets"), { recursive: true });
    manager = new AssetManager();
  });

  it("registers an asset and lists it", async () => {
    const file = makeFile("logo.png");
    await manager.register("main-logo", file, "logo");
    const list = await manager.list();
    expect(list).toHaveLength(1);
    expect(list[0].name).toBe("main-logo");
    expect(list[0].type).toBe("logo");
  });

  it("rejects registration of missing files", async () => {
    await expect(
      manager.register("ghost", "/nonexistent/file.png", "logo"),
    ).rejects.toThrow(/File not found/);
  });

  it("upserts on repeat registration with same name", async () => {
    const a = makeFile("a.png");
    const b = makeFile("b.png");
    await manager.register("same", a, "logo");
    await manager.register("same", b, "logo");
    const list = await manager.list();
    expect(list).toHaveLength(1);
    expect(list[0].path).toBe(b);
  });

  it("filters list by type", async () => {
    await manager.register("logo1", makeFile("l1.png"), "logo");
    await manager.register("out1", makeFile("o1.mp4"), "outro");
    expect(await manager.list("logo")).toHaveLength(1);
    expect(await manager.list("outro")).toHaveLength(1);
  });

  it("normalizes legacy 'video' type to 'outro' on load", async () => {
    await manager.register("legacy", makeFile("legacy.mp4"), "video");
    const list = await manager.list();
    expect(list[0].type).toBe("outro");
    expect(await manager.list("outro")).toHaveLength(1);
    expect(await manager.list("video")).toHaveLength(0);
  });

  it("marks one default per type and falls back to first-existing", async () => {
    const l1 = makeFile("d1.png");
    const l2 = makeFile("d2.png");
    await manager.register("d1", l1, "logo");
    await manager.register("d2", l2, "logo");
    expect(await manager.getDefault("logo")).toBe(l1);
    await manager.setDefault("d2");
    expect(await manager.getDefault("logo")).toBe(l2);
    const flagged = (await manager.list("logo")).filter((a) => a.default);
    expect(flagged).toHaveLength(1);
    expect(flagged[0].name).toBe("d2");
  });

  it("outro and legacy video share one default group", async () => {
    const o = makeFile("share.mp4");
    await manager.register("shared", o, "outro");
    await manager.setDefault("shared");
    expect(await manager.getDefault("outro")).toBe(o);
    expect(await manager.getDefault("video")).toBe(o);
  });

  it("importFile copies into the assets dir", async () => {
    const src = makeFile("src-logo.png");
    const asset = await manager.importFile(src, "copied", "logo");
    expect(asset.path).toContain(join(tmp, "assets"));
    expect(asset.path).not.toBe(src);
  });

  it("resolve returns path for registered name, direct path, or null", async () => {
    const file = makeFile("resolvable.png");
    await manager.register("friendly", file, "logo");
    expect(await manager.resolve("friendly")).toBe(file);
    expect(await manager.resolve(file)).toBe(file);
    expect(await manager.resolve("nope")).toBeNull();
  });

  it("unregister removes the asset", async () => {
    await manager.register("gone", makeFile("g.png"), "logo");
    await manager.unregister("gone");
    expect(await manager.list()).toHaveLength(0);
  });
});
