import { describe, expect, it } from "vitest";
import { buildYtDlpArgs, isCookieBrowser, ytDlpHint } from "./ytdlp-args.js";

const base = {
  url: "https://example.com/watch?v=abc",
  outputDir: "/tmp/out",
  outputTemplate: "%(title)s.%(ext)s",
};

describe("buildYtDlpArgs", () => {
  it("always isolates from the user's own yt-dlp config", () => {
    const args = buildYtDlpArgs(base);
    expect(args).toContain("--ignore-config");
    expect(args).toContain("--no-config-locations");
    expect(args).toContain("--no-plugin-dirs");
  });

  it("keeps the 1080p merge selector that stops a 360p muxed stream", () => {
    const args = buildYtDlpArgs(base);
    const i = args.indexOf("--format");
    expect(args[i + 1]).toBe("bv*[height<=1080]+ba/b[height<=1080]/bv*+ba/b");
    expect(args).toContain("--merge-output-format");
  });

  it("puts the url last so it cannot be read as a flag value", () => {
    expect(buildYtDlpArgs(base).at(-1)).toBe(base.url);
  });

  it("omits cookies and extractor args when unset", () => {
    const args = buildYtDlpArgs(base);
    expect(args).not.toContain("--cookies-from-browser");
    expect(args).not.toContain("--extractor-args");
  });

  it("passes cookies and extractor args through when set", () => {
    const args = buildYtDlpArgs({
      ...base,
      cookiesFromBrowser: "firefox",
      extractorArgs: "youtube:player_client=android",
    });
    expect(args[args.indexOf("--cookies-from-browser") + 1]).toBe("firefox");
    expect(args[args.indexOf("--extractor-args") + 1]).toBe("youtube:player_client=android");
  });

  it("only asks for progress when a template is given", () => {
    expect(buildYtDlpArgs(base)).not.toContain("--progress-template");
    expect(buildYtDlpArgs({ ...base, progressTemplate: "download:x" })).toContain(
      "--progress-template",
    );
  });
});

describe("isCookieBrowser", () => {
  it("accepts yt-dlp's own list", () => {
    expect(isCookieBrowser("safari")).toBe(true);
    expect(isCookieBrowser("vivaldi")).toBe(true);
  });

  it("rejects anything else, including injection attempts", () => {
    expect(isCookieBrowser("netscape")).toBe(false);
    expect(isCookieBrowser("chrome; rm -rf /")).toBe(false);
    expect(isCookieBrowser(undefined)).toBe(false);
    expect(isCookieBrowser(7)).toBe(false);
  });
});

describe("ytDlpHint", () => {
  it("names the setting when the site wants a signed-in session", () => {
    expect(ytDlpHint("ERROR: Sign in to confirm you're not a bot")).toContain(
      "PODCLI_YTDLP_BROWSER",
    );
  });

  it("covers members-only and private videos", () => {
    expect(ytDlpHint("This video is available to this channel's members-only")).toContain(
      "PODCLI_YTDLP_BROWSER",
    );
    expect(ytDlpHint("ERROR: Private video. Sign in if you've been granted access")).toBeTruthy();
  });

  it("stays quiet on an unrelated failure", () => {
    expect(ytDlpHint("ERROR: unable to write to disk")).toBeNull();
  });
});
