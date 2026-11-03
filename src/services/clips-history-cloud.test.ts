import { describe, it, expect, beforeEach, vi } from "vitest";
import { mkdtempSync, writeFileSync, rmSync, mkdirSync } from "fs";
import { tmpdir } from "os";
import { join } from "path";
import type { HistoryCloud } from "./clips-history.js";

const tmp = mkdtempSync(join(tmpdir(), "podcli-clipcloud-test-"));

process.env.PODCLI_HOME = tmp;

process.env.PODCLI_DATA = tmp;

process.env.PODCLI_CLOUD_SYNC = "1";

const cloud = {
  signedIn: vi.fn<HistoryCloud["signedIn"]>(),
  sourceHash: vi.fn<HistoryCloud["sourceHash"]>(),
  registerClip: vi.fn<HistoryCloud["registerClip"]>(),
  uploadClipVideo: vi.fn<HistoryCloud["uploadClipVideo"]>(),
  logClipEvent: vi.fn<HistoryCloud["logClipEvent"]>(),
} satisfies HistoryCloud;

const { ClipsHistory } = await import("./clips-history.js");

const source = join(tmp, "episode.mp4");
const output = join(tmp, "clip.mp4");

/** record() fires its cloud sync in the background; let it settle before asserting. */
const settle = () => new Promise((resolve) => setTimeout(resolve, 10));

async function seed(history: InstanceType<typeof ClipsHistory>) {
  return history.record({
    title: "A clip",
    source_video: source,
    output_path: output,
    duration: 42,
    start_second: 10,
    end_second: 52,
    format: "vertical",
    caption_style: "hormozi",
    crop_strategy: "face",
    file_size_mb: 3,
  });
}

describe("clip cloud sync", () => {
  let history: InstanceType<typeof ClipsHistory>;

  beforeEach(() => {
    rmSync(join(tmp, "history"), { recursive: true, force: true });
    mkdirSync(join(tmp, "history"), { recursive: true });
    writeFileSync(source, "source bytes");
    writeFileSync(output, "rendered bytes");
    vi.clearAllMocks();
    cloud.signedIn.mockResolvedValue(false);
    cloud.sourceHash.mockResolvedValue("abc123");
    cloud.registerClip.mockResolvedValue({ id: "cloud-clip-1" });
    cloud.uploadClipVideo.mockResolvedValue(true);
    cloud.logClipEvent.mockResolvedValue(undefined);
    history = new ClipsHistory(cloud);
  });

  it("makes no network call when sync is off, even when signed in", async () => {
    cloud.signedIn.mockResolvedValue(true);
    process.env.PODCLI_CLOUD_SYNC = "0";

    try {
      await seed(history);
      await settle();
      const result = await history.backfillCloud();

      expect(result).toEqual({ synced: 0, failed: 0 });
      expect(cloud.registerClip).not.toHaveBeenCalled();
      expect(cloud.uploadClipVideo).not.toHaveBeenCalled();
    } finally {
      process.env.PODCLI_CLOUD_SYNC = "1";
    }
  });

  it("makes no network call when signed out", async () => {
    await seed(history);
    const result = await history.backfillCloud();

    expect(result).toEqual({ synced: 0, failed: 0 });
    expect(cloud.registerClip).not.toHaveBeenCalled();
    expect(cloud.uploadClipVideo).not.toHaveBeenCalled();
  });

  it("uploads the rendered clip after registering it", async () => {
    const entry = await seed(history);
    await settle();
    cloud.signedIn.mockResolvedValue(true);

    await history.backfillCloud();

    expect(cloud.registerClip).toHaveBeenCalledTimes(1);
    expect(cloud.uploadClipVideo).toHaveBeenCalledWith("cloud-clip-1", output);
    const after = await history.findById(entry.id);
    expect(after?.cloud_id).toBe("cloud-clip-1");
    expect(after?.cloud_video_uploaded).toBe(true);
  });

  it("registers a clip once when two syncs overlap", async () => {
    await seed(history);
    await settle();
    cloud.signedIn.mockResolvedValue(true);

    await Promise.all([history.backfillCloud(), history.backfillCloud()]);

    expect(cloud.registerClip).toHaveBeenCalledTimes(1);
    expect(cloud.uploadClipVideo).toHaveBeenCalledTimes(1);
  });

  it("does not re-upload a clip whose video the workspace already has", async () => {
    const entry = await seed(history);
    await settle();
    cloud.signedIn.mockResolvedValue(true);
    await history.backfillCloud();
    vi.clearAllMocks();
    cloud.signedIn.mockResolvedValue(true);

    await history.backfillCloud();

    expect(cloud.uploadClipVideo).not.toHaveBeenCalled();
    expect(cloud.registerClip).not.toHaveBeenCalled();
    expect((await history.findById(entry.id))?.cloud_video_uploaded).toBe(true);
  });
});
