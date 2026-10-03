import { describe, it, expect } from "vitest";
import { execSync } from "child_process";
import { mkdtempSync, writeFileSync, rmSync } from "fs";
import { tmpdir } from "os";
import { join } from "path";
import { checkMediaFiles, checkEnvFiles, matchSecretPatterns, trackedFiles } from "./release-hygiene.mjs";

describe("release-hygiene: tracked media files", () => {
  it("flags audio/video extensions", () => {
    expect(checkMediaFiles(["episode.mp4", "raw.wav"])).toHaveLength(2);
  });

  it("leaves still-image assets alone (handled by the size check instead)", () => {
    expect(checkMediaFiles(["logo.png", "icon.svg"])).toEqual([]);
  });
});

describe("release-hygiene: tracked .env files", () => {
  it("flags .env and dotted variants", () => {
    expect(checkEnvFiles([".env", ".env.local", "backend/.env"])).toHaveLength(3);
  });

  it("allows .env.example", () => {
    expect(checkEnvFiles([".env.example"])).toEqual([]);
  });
});

describe("release-hygiene: trackedFiles", () => {
  function initRepo() {
    const dir = mkdtempSync(join(tmpdir(), "release-hygiene-"));
    execSync("git init -q", { cwd: dir });
    execSync('git config user.email "test@example.com"', { cwd: dir });
    execSync('git config user.name "Test"', { cwd: dir });
    return dir;
  }

  it("lists a file whose name has a non-ASCII character", () => {
    const dir = initRepo();
    try {
      const name = "文字.txt"; // CJK characters, non-ASCII bytes in the path
      writeFileSync(join(dir, name), "content");
      execSync("git add -A", { cwd: dir });
      execSync('git commit -q -m "add file"', { cwd: dir });

      const files = trackedFiles(dir);
      expect(files).toContain(name);
    } finally {
      rmSync(dir, { recursive: true, force: true });
    }
  });

  it("lists plain-ASCII filenames alongside the non-ASCII one", () => {
    const dir = initRepo();
    try {
      writeFileSync(join(dir, "école.txt"), "content"); // accented e
      writeFileSync(join(dir, "plain.txt"), "content");
      execSync("git add -A", { cwd: dir });
      execSync('git commit -q -m "add files"', { cwd: dir });

      const files = trackedFiles(dir);
      expect(files).toEqual(expect.arrayContaining(["école.txt", "plain.txt"]));
    } finally {
      rmSync(dir, { recursive: true, force: true });
    }
  });
});

describe("release-hygiene: secret patterns", () => {
  it("flags an AWS access key", () => {
    expect(matchSecretPatterns("key = AKIAABCDEFGHIJKLMNOP")).toContain("AWS access key");
  });

  it("flags a private key block", () => {
    expect(matchSecretPatterns("-----BEGIN RSA PRIVATE KEY-----\nabc\n-----END RSA PRIVATE KEY-----")).toContain(
      "private key block",
    );
  });

  it("leaves ordinary source text alone", () => {
    expect(matchSecretPatterns("const apiKey = process.env.API_KEY;")).toEqual([]);
  });
});
