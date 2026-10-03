import { spawn } from "child_process";
import { paths, pythonEnv } from "../config/paths.js";
import { ClipsHistory } from "../services/clips-history.js";
import {
  buildChannelListArgs,
  buildVideoInfoArgs,
  extractYouTubeVideoId,
  isCookieBrowser,
  normalizeChannelUrl,
} from "../utils/ytdlp-args.js";
import { selectBestCaptionTrack, parseVtt, parseJson3, cuesToWords, type CaptionTrackRef } from "../utils/captions.js";
import type { WordTimestamp } from "../models/index.js";

const history = new ClipsHistory();

export const mineChannelToolDef = {
  name: "mine_channel",
  description:
    "Mine a YouTube channel's back catalog for clip-worthy moments without downloading any video. " +
    "action='list' lists a channel's uploads (title, duration, upload date), flagging ones already mined " +
    "into clip_history so you don't re-suggest from them. action='mine' fetches one video's existing " +
    "captions (never the video itself), preferring the original-language track over an auto-translated one, " +
    "and converts them to podcli's word-level transcript format. Feed the result straight into the " +
    "suggest_clips flow the same way import_transcript's output is used. Never downloads a full video; " +
    "that only happens if you separately choose to render a clip from one.",
};

export interface MineChannelInput {
  action: "list" | "mine";
  channel_url?: string;
  video_url?: string;
  limit?: number;
  cookies_from_browser?: string;
}

interface RunResult {
  stdout: string;
  stderr: string;
  code: number;
}

async function runYtDlp(args: string[]): Promise<RunResult> {
  return new Promise((resolve, reject) => {
    const proc = spawn(paths.pythonPath, args, { env: pythonEnv() });
    let stdout = "";
    let stderr = "";
    proc.stdout.on("data", (d) => (stdout += d.toString()));
    proc.stderr.on("data", (d) => (stderr += d.toString()));
    proc.on("error", reject);
    proc.on("close", (code) => resolve({ stdout, stderr, code: code ?? 1 }));
  });
}

async function fetchText(url: string): Promise<string> {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`HTTP ${res.status} fetching caption track`);
  return res.text();
}

export interface ChannelUpload {
  video_id: string;
  title: string;
  duration?: number;
  upload_date?: string;
  url: string;
  already_mined: boolean;
}

export interface MinedVideoResult {
  video_id: string;
  title: string;
  duration?: number;
  url: string;
  already_mined: boolean;
  track: { lang: string; kind: "manual" | "auto" } | null;
  transcript: {
    words: WordTimestamp[];
    segments: Array<{ text: string; start: number; end: number }>;
    duration?: number;
    language: string;
    text: string;
  } | null;
  message: string;
}

// Injectable I/O so the orchestration (parsing, track selection, dedup) is
// testable against fixture data with zero network or subprocess calls.
export interface MineChannelDeps {
  runYtDlp: (args: string[]) => Promise<RunResult>;
  fetchText: (url: string) => Promise<string>;
  minedVideoIds: () => Promise<Set<string>>;
}

export async function listChannelUploads(
  deps: MineChannelDeps,
  input: { channel_url: string; limit?: number; cookies_from_browser?: string },
): Promise<ChannelUpload[]> {
  const args = buildChannelListArgs({
    channelUrl: normalizeChannelUrl(input.channel_url),
    limit: input.limit,
    cookiesFromBrowser: isCookieBrowser(input.cookies_from_browser) ? input.cookies_from_browser : undefined,
  });
  const { stdout, stderr, code } = await deps.runYtDlp(args);
  if (code !== 0) {
    throw new Error(stderr.trim() || "yt-dlp failed to list the channel's uploads");
  }
  const minedIds = await deps.minedVideoIds();
  return stdout
    .trim()
    .split("\n")
    .filter(Boolean)
    .map((line) => JSON.parse(line) as { id?: string; title: string; duration?: number; upload_date?: string; url?: string })
    // A tab or sub-playlist entry (e.g. "Shorts", "Live") has no video id of
    // its own: only real uploads do.
    .filter((u): u is { id: string; title: string; duration?: number; upload_date?: string; url?: string } => Boolean(u.id))
    .map((u) => ({
      video_id: u.id,
      title: u.title,
      duration: u.duration,
      upload_date: u.upload_date,
      url: u.url ?? `https://www.youtube.com/watch?v=${u.id}`,
      already_mined: minedIds.has(u.id),
    }));
}

export async function mineVideoCaptions(
  deps: MineChannelDeps,
  input: { video_url: string; cookies_from_browser?: string },
): Promise<MinedVideoResult> {
  const args = buildVideoInfoArgs({
    videoUrl: input.video_url,
    cookiesFromBrowser: isCookieBrowser(input.cookies_from_browser) ? input.cookies_from_browser : undefined,
  });
  const { stdout, stderr, code } = await deps.runYtDlp(args);
  if (code !== 0) {
    throw new Error(stderr.trim() || "yt-dlp failed to fetch video info");
  }
  // --dump-json prints exactly one JSON object; take the last non-empty line
  // defensively in case a warning slipped through on an earlier line.
  const jsonLine = stdout.trim().split("\n").filter(Boolean).at(-1);
  if (!jsonLine) throw new Error("yt-dlp returned no video info");
  const info = JSON.parse(jsonLine) as {
    id: string;
    title: string;
    duration?: number;
    subtitles?: Record<string, CaptionTrackRef[]>;
    automatic_captions?: Record<string, CaptionTrackRef[]>;
  };

  // --no-playlist stops a playlist/mix url from resolving to its first entry,
  // but a url that already names one video (e.g. a stale redirect) can still
  // resolve to a different id than the one asked for. Catch that here rather
  // than silently returning the wrong video's captions.
  const requestedId = extractYouTubeVideoId(input.video_url);
  if (requestedId && requestedId !== info.id) {
    throw new Error(
      `yt-dlp resolved ${input.video_url} to video ${info.id}, not the requested ${requestedId}`,
    );
  }

  const minedIds = await deps.minedVideoIds();
  const alreadyMined = minedIds.has(info.id);

  const track = selectBestCaptionTrack(info.subtitles, info.automatic_captions);
  if (!track) {
    return {
      video_id: info.id,
      title: info.title,
      duration: info.duration,
      url: input.video_url,
      already_mined: alreadyMined,
      track: null,
      transcript: null,
      message: "No captions available for this video, nothing to mine.",
    };
  }

  const captionText = await deps.fetchText(track.url);
  const { words, segments } =
    track.ext === "json3"
      ? parseJson3(captionText)
      : (() => {
          const cues = parseVtt(captionText);
          return { words: cuesToWords(cues), segments: cues.map((c) => ({ text: c.text, start: c.start, end: c.end })) };
        })();
  const language = track.lang.replace(/-orig$/, "");

  return {
    video_id: info.id,
    title: info.title,
    duration: info.duration,
    url: input.video_url,
    already_mined: alreadyMined,
    track: { lang: track.lang, kind: track.kind },
    transcript: {
      words,
      segments,
      duration: info.duration,
      language,
      text: words.map((w) => w.word).join(" "),
    },
    message: alreadyMined
      ? "This video already has clips in history. Review before re-mining to avoid duplicates."
      : `Mined ${words.length} words from the ${track.kind} ${track.lang} track. ` +
        "Use this transcript the same way import_transcript's output is used, then suggest_clips.",
  };
}

const defaultDeps: MineChannelDeps = {
  runYtDlp,
  fetchText,
  minedVideoIds: () => history.minedYouTubeVideoIds(),
};

export async function handleMineChannel(input: MineChannelInput): Promise<string> {
  if (input.action === "list") {
    if (!input.channel_url) throw new Error("channel_url is required for action=list");
    const uploads = await listChannelUploads(defaultDeps, {
      channel_url: input.channel_url,
      limit: input.limit,
      cookies_from_browser: input.cookies_from_browser,
    });
    return JSON.stringify({ uploads, count: uploads.length });
  }

  if (!input.video_url) throw new Error("video_url is required for action=mine");
  const result = await mineVideoCaptions(defaultDeps, {
    video_url: input.video_url,
    cookies_from_browser: input.cookies_from_browser,
  });
  return JSON.stringify(result);
}
