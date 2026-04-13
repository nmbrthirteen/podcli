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
    await manager.register("vid1", makeFile("v1.mp4"), "video");
    expect(await manager.list("logo")).toHaveLength(1);
    expect(await manager.list("video")).toHaveLength(1);
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
