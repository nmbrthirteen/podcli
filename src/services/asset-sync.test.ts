import { describe, it, expect, beforeEach, vi } from "vitest";
import { createHash } from "crypto";
import { mkdtempSync, rmSync, mkdirSync, writeFileSync } from "fs";
import { tmpdir } from "os";
import { join } from "path";
import type { AssetCloud } from "./asset-sync.js";

const tmp = mkdtempSync(join(tmpdir(), "podcli-assetsync-test-"));

process.env.PODCLI_HOME = tmp;

process.env.PODCLI_DATA = tmp;

const digest = (body: string) =>
  createHash("sha256").update(Buffer.from(body)).digest("hex").slice(0, 32);

const cloud = {
  signedIn: vi.fn<AssetCloud["signedIn"]>(),
  listAssets: vi.fn<AssetCloud["listAssets"]>(),
  uploadAsset: vi.fn<AssetCloud["uploadAsset"]>(),
  downloadAsset: vi.fn<AssetCloud["downloadAsset"]>(),
  checksum: (body: Buffer) => createHash("sha256").update(body).digest("hex").slice(0, 32),
} satisfies AssetCloud;

const { push } = await import("./asset-sync.js");
const { AssetManager } = await import("./asset-manager.js");

const intro = join(tmp, "intro.mp4");
let assetName = "";

describe("asset push", () => {
  beforeEach(async () => {
    rmSync(join(tmp, "assets"), { recursive: true, force: true });
    mkdirSync(join(tmp, "assets"), { recursive: true });
    writeFileSync(intro, "intro bytes");
    vi.clearAllMocks();
    cloud.signedIn.mockResolvedValue(true);
    cloud.listAssets.mockResolvedValue([]);
    cloud.uploadAsset.mockResolvedValue({ id: "new", unchanged: false });
    assetName = (await new AssetManager().register("Show intro", intro, "intro")).name;
  });

  it("does not re-upload an asset the workspace already holds", async () => {
    cloud.listAssets.mockResolvedValue([
      { id: "1", name: assetName, kind: "intro", is_default: false,
        size_bytes: "11", checksum: digest("intro bytes") },
    ]);

    const report = await push(cloud);

    expect(cloud.uploadAsset).not.toHaveBeenCalled();
    expect(report.skipped).toContain(assetName);
    expect(report.uploaded).toEqual([]);
  });

  it("uploads when the local file has changed", async () => {
    cloud.listAssets.mockResolvedValue([
      { id: "1", name: assetName, kind: "intro", is_default: false,
        size_bytes: "11", checksum: digest("something else") },
    ]);

    const report = await push(cloud);

    expect(cloud.uploadAsset).toHaveBeenCalledTimes(1);
    expect(report.uploaded).toContain(assetName);
  });

  it("still uploads when the listing cannot be read", async () => {
    cloud.listAssets.mockRejectedValue(new Error("offline"));

    const report = await push(cloud);

    expect(cloud.uploadAsset).toHaveBeenCalledTimes(1);
    expect(report.failed).toEqual([]);
  });
});
