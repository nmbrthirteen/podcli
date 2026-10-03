import { describe, it, expect, beforeAll, afterAll } from "vitest";
import { mkdtempSync, writeFileSync, rmSync, utimesSync } from "fs";
import { tmpdir } from "os";
import { join } from "path";
import { computeVideoIdentity, identitiesMatch, transcriptVideoMismatch } from "./video-identity.js";

describe("video-identity", () => {
  let dir: string;
  let filePath: string;

  beforeAll(() => {
    dir = mkdtempSync(join(tmpdir(), "podcli-video-identity-"));
    filePath = join(dir, "video.mp4");
    writeFileSync(filePath, "abc");
  });

  afterAll(() => {
    rmSync(dir, { recursive: true, force: true });
  });

  it("returns null for a missing file", () => {
    expect(computeVideoIdentity(join(dir, "missing.mp4"))).toBeNull();
  });

  it("computes an identity for an existing file", () => {
    const identity = computeVideoIdentity(filePath);
    expect(identity?.path).toBe(filePath);
    expect(identity?.size).toBe(3);
  });

  it("matches two identical identities", () => {
    const a = computeVideoIdentity(filePath);
    const b = computeVideoIdentity(filePath);
    expect(identitiesMatch(a, b)).toBe(true);
  });

  it("does not match once the file is rewritten (size or mtime change)", () => {
    const before = computeVideoIdentity(filePath);
    writeFileSync(filePath, "a longer replacement body");
    utimesSync(filePath, new Date(Date.now() + 5000), new Date(Date.now() + 5000));
    const after = computeVideoIdentity(filePath);
    expect(identitiesMatch(before, after)).toBe(false);
  });

  it("transcriptVideoMismatch is null when there's no recorded identity", () => {
    expect(transcriptVideoMismatch(null, filePath)).toBeNull();
    expect(transcriptVideoMismatch(undefined, filePath)).toBeNull();
  });

  it("transcriptVideoMismatch is null when the current video can't be statted", () => {
    const stale = computeVideoIdentity(filePath);
    expect(transcriptVideoMismatch(stale, join(dir, "gone.mp4"))).toBeNull();
  });

  it("transcriptVideoMismatch names the mismatch when the file changed", () => {
    const stale = computeVideoIdentity(filePath);
    writeFileSync(filePath, "totally different content, different length");
    const msg = transcriptVideoMismatch(stale, filePath);
    expect(msg).toMatch(/different video/);
    expect(msg).toContain(filePath);
  });

  it("transcriptVideoMismatch is null when the file is unchanged", () => {
    const identity = computeVideoIdentity(filePath);
    expect(transcriptVideoMismatch(identity, filePath)).toBeNull();
  });
});
