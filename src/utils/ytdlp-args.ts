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
  args.push(opts.url);
  return args;
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
