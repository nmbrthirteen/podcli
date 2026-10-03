import { describe, expect, it } from "vitest";
import {
  buildYtDlpArgs,
  buildChannelListArgs,
  buildVideoInfoArgs,
  extractYouTubeVideoId,
  isCookieBrowser,
  isHttpUrl,
  normalizeChannelUrl,
  ytDlpHint,
} from "./ytdlp-args.js";

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

  it("puts -- right before the url so a dash-prefixed value can't be read as a flag", () => {
    const args = buildYtDlpArgs(base);
    expect(args.at(-2)).toBe("--");
    expect(args.at(-1)).toBe(base.url);
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

describe("buildChannelListArgs", () => {
  const channelUrl = "https://www.youtube.com/@example/videos";

  it("never resolves each video's own page: flat-playlist only", () => {
    const args = buildChannelListArgs({ channelUrl });
    expect(args).toContain("--flat-playlist");
    expect(args).not.toContain("--format");
  });

  it("caps the listing with --playlist-end when a limit is given", () => {
    const args = buildChannelListArgs({ channelUrl, limit: 25 });
    const i = args.indexOf("--playlist-end");
    expect(args[i + 1]).toBe("25");
  });

  it("omits the limit flag entirely when none is given", () => {
    expect(buildChannelListArgs({ channelUrl })).not.toContain("--playlist-end");
  });

  it("puts the channel url last", () => {
    expect(buildChannelListArgs({ channelUrl }).at(-1)).toBe(channelUrl);
  });

  it("puts -- right before the channel url", () => {
    const args = buildChannelListArgs({ channelUrl });
    expect(args.at(-2)).toBe("--");
    expect(args.at(-1)).toBe(channelUrl);
  });
});

describe("buildVideoInfoArgs", () => {
  const videoUrl = "https://www.youtube.com/watch?v=abc123";

  it("skips downloading the video itself", () => {
    expect(buildVideoInfoArgs({ videoUrl })).toContain("--skip-download");
  });

  it("dumps JSON rather than a human-readable report", () => {
    const args = buildVideoInfoArgs({ videoUrl });
    expect(args).toContain("--dump-json");
  });

  it("puts -- right before the video url", () => {
    const args = buildVideoInfoArgs({ videoUrl });
    expect(args.at(-2)).toBe("--");
    expect(args.at(-1)).toBe(videoUrl);
  });

  it("refuses to follow a playlist/mix to its first entry", () => {
    expect(buildVideoInfoArgs({ videoUrl })).toContain("--no-playlist");
  });
});

describe("extractYouTubeVideoId", () => {
  it("reads the v= query param off a watch url", () => {
    expect(extractYouTubeVideoId("https://www.youtube.com/watch?v=abc123")).toBe("abc123");
  });

  it("reads the id off a youtu.be short url", () => {
    expect(extractYouTubeVideoId("https://youtu.be/abc123")).toBe("abc123");
  });

  it("reads the id off shorts/embed/live path shapes", () => {
    expect(extractYouTubeVideoId("https://www.youtube.com/shorts/abc123")).toBe("abc123");
    expect(extractYouTubeVideoId("https://www.youtube.com/embed/abc123")).toBe("abc123");
    expect(extractYouTubeVideoId("https://www.youtube.com/live/abc123")).toBe("abc123");
  });

  it("returns null when no video id is present, e.g. a channel url", () => {
    expect(extractYouTubeVideoId("https://www.youtube.com/@example/videos")).toBeNull();
  });
});

describe("normalizeChannelUrl", () => {
  it("pins a handle channel root to the videos tab", () => {
    expect(normalizeChannelUrl("https://www.youtube.com/@deeptechdecodedai")).toBe(
      "https://www.youtube.com/@deeptechdecodedai/videos",
    );
  });

  it("pins a trailing-slash handle channel root to the videos tab", () => {
    expect(normalizeChannelUrl("https://www.youtube.com/@deeptechdecodedai/")).toBe(
      "https://www.youtube.com/@deeptechdecodedai/videos",
    );
  });

  it("pins a /channel/<id> root to the videos tab", () => {
    expect(normalizeChannelUrl("https://www.youtube.com/channel/UC12345")).toBe(
      "https://www.youtube.com/channel/UC12345/videos",
    );
  });

  it("leaves an already-specific tab url unchanged", () => {
    expect(normalizeChannelUrl("https://www.youtube.com/@example/shorts")).toBe(
      "https://www.youtube.com/@example/shorts",
    );
    expect(normalizeChannelUrl("https://www.youtube.com/@example/videos")).toBe(
      "https://www.youtube.com/@example/videos",
    );
  });

  it("leaves a non-channel url (playlist, watch) unchanged", () => {
    const playlistUrl = "https://www.youtube.com/playlist?list=PL123";
    expect(normalizeChannelUrl(playlistUrl)).toBe(playlistUrl);
  });

  it("returns the input unchanged when it isn't a valid url", () => {
    expect(normalizeChannelUrl("not a url")).toBe("not a url");
  });
});

describe("isHttpUrl", () => {
  it("accepts http and https urls", () => {
    expect(isHttpUrl("https://www.youtube.com/watch?v=abc123")).toBe(true);
    expect(isHttpUrl("http://example.com")).toBe(true);
  });

  it("rejects a dash-prefixed value that would be read as a yt-dlp flag", () => {
    expect(isHttpUrl("--config-locations=/tmp/evil.conf")).toBe(false);
  });

  it("rejects non-http schemes and non-strings", () => {
    expect(isHttpUrl("file:///etc/passwd")).toBe(false);
    expect(isHttpUrl("not a url")).toBe(false);
    expect(isHttpUrl(undefined)).toBe(false);
    expect(isHttpUrl(7)).toBe(false);
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
