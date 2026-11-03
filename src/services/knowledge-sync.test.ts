import { describe, it, expect, beforeEach, vi } from "vitest";
import { mkdtempSync, rmSync, mkdirSync, existsSync, readdirSync, writeFileSync, readFileSync } from "fs";
import { tmpdir } from "os";
import { join } from "path";
import type { KnowledgeCloud } from "./knowledge-sync.js";

const tmp = mkdtempSync(join(tmpdir(), "podcli-ksync-test-"));

process.env.PODCLI_HOME = tmp;

process.env.PODCLI_DATA = tmp;

const cloud = {
  signedIn: vi.fn<KnowledgeCloud["signedIn"]>(),
  listKnowledge: vi.fn<KnowledgeCloud["listKnowledge"]>(),
  getKnowledge: vi.fn<KnowledgeCloud["getKnowledge"]>(),
  putKnowledge: vi.fn<KnowledgeCloud["putKnowledge"]>(),
} satisfies KnowledgeCloud;

const { sync } = await import("./knowledge-sync.js");

describe("knowledge sync", () => {
  beforeEach(() => {
    rmSync(join(tmp, "knowledge"), { recursive: true, force: true });
    // The sync state lives beside the folder, not in it. Leaving it behind made
    // these tests order-dependent: the pull phase skips any path already in the
    // map, so a later test passed only because an earlier one had not recorded
    // a version for the same filename.
    rmSync(join(tmp, "knowledge-sync.json"), { force: true });
    mkdirSync(join(tmp, "knowledge"), { recursive: true });
    vi.clearAllMocks();
    cloud.signedIn.mockResolvedValue(true);
    cloud.listKnowledge.mockResolvedValue([]);
    cloud.getKnowledge.mockResolvedValue({ content: "owned", version: 1 });
    cloud.putKnowledge.mockResolvedValue({ conflict: false, version: 1, unchanged: true });
  });

  it("refuses a workspace path that escapes the knowledge folder", async () => {
    cloud.listKnowledge.mockResolvedValue([
      { path: "../../pwned.md", version: 1, updated_at: "" },
    ]);

    const report = await sync(cloud);

    expect(existsSync(join(tmp, "..", "pwned.md"))).toBe(false);
    expect(report.pulled).toEqual([]);
    expect(report.failed[0]?.path).toBe("../../pwned.md");
    // Rejected before the content is ever requested.
    expect(cloud.getKnowledge).not.toHaveBeenCalled();
  });

  it("never pushes shipped defaults over the workspace copy on a first sync", async () => {
    writeFileSync(join(tmp, "knowledge", "02-voice-and-tone.md"), "# shipped default");
    cloud.listKnowledge.mockResolvedValue([
      { path: "02-voice-and-tone.md", version: 7, updated_at: "" },
    ]);
    cloud.getKnowledge.mockResolvedValue({
      content: "# the team's real voice guide", version: 7,
    });

    const report = await sync(cloud);

    expect(cloud.putKnowledge).not.toHaveBeenCalled();
    expect(report.conflicts).toEqual([{ path: "02-voice-and-tone.md", theirVersion: 7 }]);
    // Neither copy is lost.
    expect(readFileSync(join(tmp, "knowledge", "02-voice-and-tone.md"), "utf-8"))
      .toBe("# shipped default");
    expect(readFileSync(join(tmp, "knowledge", "02-voice-and-tone.md.workspace-7"), "utf-8"))
      .toBe("# the team's real voice guide");
  });

  it("adopts the workspace version when both copies already match", async () => {
    writeFileSync(join(tmp, "knowledge", "05-title-formulas.md"), "# same bytes");
    cloud.listKnowledge.mockResolvedValue([
      { path: "05-title-formulas.md", version: 4, updated_at: "" },
    ]);
    cloud.getKnowledge.mockResolvedValue({ content: "# same bytes", version: 4 });

    const report = await sync(cloud);

    expect(cloud.putKnowledge).not.toHaveBeenCalled();
    expect(report.unchanged).toEqual(["05-title-formulas.md"]);
    expect(report.conflicts).toEqual([]);
    expect(existsSync(join(tmp, "knowledge", "05-title-formulas.md.workspace-4"))).toBe(false);
  });

  it("pushes a local file the workspace does not have", async () => {
    writeFileSync(join(tmp, "knowledge", "99-mine.md"), "# only here");
    cloud.listKnowledge.mockResolvedValue([]);
    cloud.putKnowledge.mockResolvedValue({
      conflict: false, version: 1, unchanged: false,
    });

    const report = await sync(cloud);

    expect(cloud.putKnowledge).toHaveBeenCalledWith("99-mine.md", "# only here", undefined);
    expect(report.pushed).toEqual(["99-mine.md"]);
  });

  it("pulls a file the workspace has and this machine does not", async () => {
    cloud.listKnowledge.mockResolvedValue([
      { path: "02-voice-and-tone.md", version: 3, updated_at: "" },
    ]);

    const report = await sync(cloud);

    expect(report.pulled).toEqual(["02-voice-and-tone.md"]);
    expect(readdirSync(join(tmp, "knowledge"))).toContain("02-voice-and-tone.md");
  });
});
