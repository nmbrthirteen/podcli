import { describe, it, expect } from "vitest";
import { listChannelUploads, mineVideoCaptions, type MineChannelDeps } from "./mine-channel.handler.js";

// Fixture data standing in for yt-dlp's real output shape, so these tests
// never touch the network or spawn a real subprocess.

const FLAT_PLAYLIST_STDOUT = [
  JSON.stringify({ id: "vid1", title: "Episode 1", duration: 3600, upload_date: "20240101", url: "https://youtu.be/vid1" }),
  JSON.stringify({ id: "vid2", title: "Episode 2", duration: 4200, upload_date: "20240108" }),
].join("\n");

const VIDEO_INFO_WITH_ORIGINAL_TRACK = JSON.stringify({
  id: "vid1",
  title: "Episode 1",
  duration: 3600,
  subtitles: {},
  automatic_captions: {
    ka: [{ ext: "vtt", url: "https://captions.example/ka-translated.vtt" }],
    "ka-orig": [{ ext: "vtt", url: "https://captions.example/ka-orig.vtt" }],
  },
});

const VIDEO_INFO_NO_CAPTIONS = JSON.stringify({ id: "vid3", title: "No captions here", duration: 100 });

const FIXTURE_VTT = [
  "WEBVTT",
  "",
  "00:00:00.000 --> 00:00:02.000",
  "Hello and welcome",
  "",
  "00:00:02.000 --> 00:00:04.000",
  "to the show",
  "",
].join("\n");

function depsReturning(opts: {
  stdout: string;
  code?: number;
  stderr?: string;
  minedIds?: string[];
  captionText?: string;
}): MineChannelDeps {
  return {
    runYtDlp: async () => ({ stdout: opts.stdout, stderr: opts.stderr ?? "", code: opts.code ?? 0 }),
    fetchText: async () => opts.captionText ?? "",
    minedVideoIds: async () => new Set(opts.minedIds ?? []),
  };
}

describe("listChannelUploads", () => {
  it("normalizes a bare channel root to the /videos tab before calling yt-dlp", async () => {
    const seenArgs: string[][] = [];
    const deps: MineChannelDeps = {
      runYtDlp: async (args) => {
        seenArgs.push(args);
        return { stdout: FLAT_PLAYLIST_STDOUT, stderr: "", code: 0 };
      },
      fetchText: async () => "",
      minedVideoIds: async () => new Set(),
    };
    await listChannelUploads(deps, { channel_url: "https://www.youtube.com/@deeptechdecodedai" });
    expect(seenArgs[0].at(-1)).toBe("https://www.youtube.com/@deeptechdecodedai/videos");
  });

  it("drops tab/sub-playlist entries that carry no video id of their own", async () => {
    const stdout = [
      JSON.stringify({ title: "Shorts", _type: "playlist" }),
      JSON.stringify({ id: "vid1", title: "Episode 1", duration: 3600 }),
    ].join("\n");
    const uploads = await listChannelUploads(depsReturning({ stdout }), {
      channel_url: "https://www.youtube.com/@example/videos",
    });
    expect(uploads).toHaveLength(1);
    expect(uploads[0].video_id).toBe("vid1");
  });

  it("parses one JSON object per line into channel uploads", async () => {
    const uploads = await listChannelUploads(depsReturning({ stdout: FLAT_PLAYLIST_STDOUT }), {
      channel_url: "https://www.youtube.com/@example/videos",
    });
    expect(uploads).toHaveLength(2);
    expect(uploads[0]).toMatchObject({ video_id: "vid1", title: "Episode 1", duration: 3600 });
  });

  it("falls back to a watch url when yt-dlp doesn't print one", async () => {
    const uploads = await listChannelUploads(depsReturning({ stdout: FLAT_PLAYLIST_STDOUT }), {
      channel_url: "https://www.youtube.com/@example/videos",
    });
    expect(uploads[1].url).toBe("https://www.youtube.com/watch?v=vid2");
  });

  it("flags uploads that already have clips in history", async () => {
    const uploads = await listChannelUploads(depsReturning({ stdout: FLAT_PLAYLIST_STDOUT, minedIds: ["vid1"] }), {
      channel_url: "https://www.youtube.com/@example/videos",
    });
    expect(uploads.find((u) => u.video_id === "vid1")?.already_mined).toBe(true);
    expect(uploads.find((u) => u.video_id === "vid2")?.already_mined).toBe(false);
  });

  it("throws with yt-dlp's stderr when the listing fails", async () => {
    await expect(
      listChannelUploads(depsReturning({ stdout: "", code: 1, stderr: "channel not found" }), {
        channel_url: "https://www.youtube.com/@nope",
      }),
    ).rejects.toThrow("channel not found");
  });
});

describe("mineVideoCaptions", () => {
  it("prefers the original-language track over the auto-translated one", async () => {
    const result = await mineVideoCaptions(
      depsReturning({ stdout: VIDEO_INFO_WITH_ORIGINAL_TRACK, captionText: FIXTURE_VTT }),
      { video_url: "https://youtu.be/vid1" },
    );
    expect(result.track).toEqual({ lang: "ka-orig", kind: "auto" });
    expect(result.transcript?.language).toBe("ka");
  });

  it("converts fetched caption cues into a word-level transcript", async () => {
    const result = await mineVideoCaptions(
      depsReturning({ stdout: VIDEO_INFO_WITH_ORIGINAL_TRACK, captionText: FIXTURE_VTT }),
      { video_url: "https://youtu.be/vid1" },
    );
    expect(result.transcript?.words.map((w) => w.word)).toEqual(["Hello", "and", "welcome", "to", "the", "show"]);
    expect(result.transcript?.text).toBe("Hello and welcome to the show");
  });

  it("reports no transcript when the video has no captions at all", async () => {
    const result = await mineVideoCaptions(depsReturning({ stdout: VIDEO_INFO_NO_CAPTIONS }), {
      video_url: "https://youtu.be/vid3",
    });
    expect(result.transcript).toBeNull();
    expect(result.track).toBeNull();
    expect(result.message).toMatch(/no captions/i);
  });

  it("flags a video that's already been mined into clip history", async () => {
    const result = await mineVideoCaptions(
      depsReturning({ stdout: VIDEO_INFO_WITH_ORIGINAL_TRACK, captionText: FIXTURE_VTT, minedIds: ["vid1"] }),
      { video_url: "https://youtu.be/vid1" },
    );
    expect(result.already_mined).toBe(true);
    expect(result.message).toMatch(/already/i);
  });

  it("throws with yt-dlp's stderr when fetching video info fails", async () => {
    await expect(
      mineVideoCaptions(depsReturning({ stdout: "", code: 1, stderr: "video unavailable" }), {
        video_url: "https://youtu.be/gone",
      }),
    ).rejects.toThrow("video unavailable");
  });

  it("throws when yt-dlp resolves the url to a different video than requested", async () => {
    // VIDEO_INFO_WITH_ORIGINAL_TRACK is for vid1; asking with a url naming vid2
    // (e.g. a playlist/mix redirect) must not be returned as if it were vid2's captions.
    await expect(
      mineVideoCaptions(depsReturning({ stdout: VIDEO_INFO_WITH_ORIGINAL_TRACK, captionText: FIXTURE_VTT }), {
        video_url: "https://www.youtube.com/watch?v=vid2",
      }),
    ).rejects.toThrow(/resolved .* to video vid1, not the requested vid2/);
  });
});
