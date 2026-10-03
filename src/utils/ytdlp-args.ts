/**
 * One place to build a yt-dlp argv.
 *
 * The format selector was copy-pasted across three call sites and had already
 * drifted: the Python one lacked --js-runtimes. Whatever is added here reaches
 * every download instead of the one that happened to be edited.
 */

/** yt-dlp's SUPPORTED_BROWSERS, per yt_dlp/cookies.py. */
export const COOKIE_BROWSERS = [
  "brave",
  "chrome",
  "chromium",
  "edge",
  "firefox",
  "opera",
  "safari",
  "vivaldi",
  "whale",
] as const;

export type CookieBrowser = (typeof COOKIE_BROWSERS)[number];

export function isCookieBrowser(value: unknown): value is CookieBrowser {
  return typeof value === "string" && (COOKIE_BROWSERS as readonly string[]).includes(value);
}

// A bare z.string() lets a value like "--config-locations=/tmp/evil.conf" through
// as a channel/video url, and yt-dlp reads it as another flag rather than a
// positional argument, one that can point at a config carrying --exec. Only
// http(s) URLs are legitimate inputs here.
export function isHttpUrl(value: unknown): value is string {
  if (typeof value !== "string") return false;
  try {
    const parsed = new URL(value);
    return parsed.protocol === "http:" || parsed.protocol === "https:";
  } catch {
    return false;
  }
}

export interface YtDlpOptions {
  url: string;
  outputDir: string;
  outputTemplate: string;
  ffmpegLocation?: string;
  jsRuntimeNodePath?: string;
  /** Read cookies from this browser's profile, for members-only or unlisted URLs. */
  cookiesFromBrowser?: string;
  /** Passed through to --extractor-args, e.g. "youtube:player_client=android". */
  extractorArgs?: string;
  progressTemplate?: string;
}

export function buildYtDlpArgs(opts: YtDlpOptions): string[] {
  const args = ["-m", "yt_dlp"];

  if (opts.jsRuntimeNodePath) {
    // Node as a local JS runtime; remote EJS components stay disabled.
    args.push("--js-runtimes", `node:${opts.jsRuntimeNodePath}`);
  }

  // A user's own ~/.config/yt-dlp/config can carry --extract-audio or a narrower
  // --format, which would hand podcli an audio-only or 360p file and call it the
  // episode. Plugin dirs are a separate switch that --ignore-config does not cover.
  args.push("--ignore-config", "--no-config-locations", "--no-plugin-dirs");

  args.push("--no-playlist");
  // Best video+audio up to 1080p merged to mp4. A bare muxed stream (b[ext=mp4])
  // is 360p on YouTube, which then upscales into a terrible-looking reel.
  args.push("--format", "bv*[height<=1080]+ba/b[height<=1080]/bv*+ba/b");
  args.push("--merge-output-format", "mp4");

  if (opts.ffmpegLocation) args.push("--ffmpeg-location", opts.ffmpegLocation);
  if (opts.cookiesFromBrowser) args.push("--cookies-from-browser", opts.cookiesFromBrowser);
  // Free-text rather than an enum: YouTube rotates which clients work every few
  // months, and an enum guarantees shipping a stale list.
  if (opts.extractorArgs) args.push("--extractor-args", opts.extractorArgs);

  args.push("--restrict-filenames", "--windows-filenames");
  args.push("--paths", opts.outputDir);
  args.push("--output", opts.outputTemplate);

  if (opts.progressTemplate) {
    args.push("--newline", "--progress", "--progress-template", opts.progressTemplate);
  }
  args.push("--print", "after_move:podcli-filepath:%(filepath)s");
  // "--" forces everything after it to be read positionally, so a url that
  // starts with a dash can never be parsed as another flag.
  args.push("--", opts.url);
  return args;
}

export interface YtDlpListOptions {
  channelUrl: string;
  limit?: number;
  cookiesFromBrowser?: string;
}

const CHANNEL_TABS = ["videos", "shorts", "streams", "playlists", "live", "podcasts", "releases"];

// A bare channel root (youtube.com/@handle, /channel/ID, /c/name, /user/name)
// lists the channel's main feed, which YouTube mixes long-form uploads and
// Shorts into. Pinning to the /videos tab keeps the listing to uploads only,
// matching what the tool description promises. Already-specific urls
// (a tab, a playlist, a single video) pass through unchanged.
export function normalizeChannelUrl(url: string): string {
  let parsed: URL;
  try {
    parsed = new URL(url);
  } catch {
    return url;
  }
  const segments = parsed.pathname.split("/").filter(Boolean);
  const isChannelRoot = segments.length === 1 && segments[0].startsWith("@");
  const isChannelIdRoot = segments.length === 2 && ["channel", "c", "user"].includes(segments[0]);
  if (!isChannelRoot && !isChannelIdRoot) return url;

  const lastSegment = segments.at(-1) ?? "";
  if (CHANNEL_TABS.includes(lastSegment)) return url;

  parsed.pathname = `${parsed.pathname.replace(/\/+$/, "")}/videos`;
  return parsed.toString();
}

// One JSON object per line, tab would collide with titles that contain one.
// --flat-playlist skips resolving each video's own page, so listing a
// channel's uploads never pulls anything beyond the playlist metadata,
// nowhere close to a full-video download.
export function buildChannelListArgs(opts: YtDlpListOptions): string[] {
  const args = ["-m", "yt_dlp"];
  args.push("--ignore-config", "--no-config-locations", "--no-plugin-dirs");
  args.push("--flat-playlist", "--no-warnings");
  if (opts.limit) args.push("--playlist-end", String(opts.limit));
  if (opts.cookiesFromBrowser) args.push("--cookies-from-browser", opts.cookiesFromBrowser);
  args.push("--print", "%(.{id,title,duration,upload_date,url})j");
  args.push("--", opts.channelUrl);
  return args;
}

export interface YtDlpVideoInfoOptions {
  videoUrl: string;
  cookiesFromBrowser?: string;
}

// Dumps one video's full metadata (including subtitle/automatic_captions
// track URLs) as JSON, with --skip-download so this never fetches the
// video itself, only the page and timed-text track list.
export function buildVideoInfoArgs(opts: YtDlpVideoInfoOptions): string[] {
  const args = ["-m", "yt_dlp"];
  args.push("--ignore-config", "--no-config-locations", "--no-plugin-dirs");
  args.push("--skip-download", "--no-warnings");
  // A url pointing at a playlist or a channel's "radio" mix would otherwise
  // dump the first entry's info instead of erroring, silently mining the
  // wrong video's captions.
  args.push("--no-playlist");
  if (opts.cookiesFromBrowser) args.push("--cookies-from-browser", opts.cookiesFromBrowser);
  args.push("--dump-json", "--", opts.videoUrl);
  return args;
}

// Pulls the video id out of the handful of URL shapes yt-dlp/YouTube use, so
// the id yt-dlp actually resolved can be checked against what was asked for.
export function extractYouTubeVideoId(url: string): string | null {
  try {
    const parsed = new URL(url);
    const vParam = parsed.searchParams.get("v");
    if (vParam) return vParam;
    const pathMatch = parsed.pathname.match(/\/(?:shorts|embed|live)\/([^/?]+)/);
    if (pathMatch) return pathMatch[1];
    if (parsed.hostname === "youtu.be") {
      const id = parsed.pathname.slice(1).split("/")[0];
      return id || null;
    }
    return null;
  } catch {
    return null;
  }
}

/** Turns a yt-dlp failure into one line naming what to do about it. */
export function ytDlpHint(stderr: string): string | null {
  const s = stderr.toLowerCase();
  if (
    s.includes("sign in to confirm") ||
    s.includes("confirm you're not a bot") ||
    s.includes("cookies")
  ) {
    return "The site asked for a signed-in session. Set PODCLI_YTDLP_BROWSER to the browser you are logged into (chrome, firefox, safari, edge, brave, chromium, opera, vivaldi, whale).";
  }
  if (s.includes("members-only") || s.includes("members only")) {
    return "This is members-only. Set PODCLI_YTDLP_BROWSER to a browser signed in to an account with access.";
  }
  if (s.includes("private video")) {
    return "This video is private. If it is yours, set PODCLI_YTDLP_BROWSER to a browser signed in to that account.";
  }
  if (s.includes("video unavailable") || s.includes("not available in your")) {
    return "The video is unavailable from here, which is usually a region or age restriction. A signed-in browser profile via PODCLI_YTDLP_BROWSER often clears it.";
  }
  return null;
}
