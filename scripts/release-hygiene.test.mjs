import { describe, it, expect } from "vitest";
import { checkMediaFiles, checkEnvFiles, matchSecretPatterns } from "./release-hygiene.mjs";

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
