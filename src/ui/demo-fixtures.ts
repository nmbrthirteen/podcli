// Demo mode: seed the studio with clean, on-brand sample clips so every page is
// screenshot-ready for the docs without rendering a real video. Enabled with
// PODCLI_DEMO=1. The clip identities and captions mirror the podcli.com showcase
// so the studio reads as the same product. See ClipsHistory.load()/save() and the
// web-server /api/image + /api/youtube/status demo branches for the wiring.

import { dirname, join, resolve } from "path";
import { existsSync } from "fs";
import { fileURLToPath } from "url";
import type { ClipHistoryEntry } from "../models/index.js";

// PNGs are shipped in src/ui/demo-assets and not copied into dist by tsc, so the
// compiled build falls back to the source dir (mirrors web-server's publicDir).
const here = dirname(fileURLToPath(import.meta.url));
const assetCandidates = [join(here, "demo-assets"), resolve(here, "..", "..", "src", "ui", "demo-assets")];
export const DEMO_ASSETS_DIR = assetCandidates.find(existsSync) ?? assetCandidates[0];

export function isDemoMode(): boolean {
  return process.env.PODCLI_DEMO === "1";
}

const preview = (id: string) => join(DEMO_ASSETS_DIR, `${id}.png`);

// Four "shows" mirror the landing showcase (Tech Daily / The Founder / Deep Dive /
// Long Story). Each source_video basename becomes one episode row in the Library.
const CLIPS: ReadonlyArray<Omit<ClipHistoryEntry, "output_path" | "thumbnail_config">> = [
  {
    id: "demo-tech-daily-1",
    source_video: "tech-daily-e148.mp4",
    start_second: 612.4, end_second: 640.9,
    caption_style: "branded", crop_strategy: "speaker", format: "vertical",
    title: "The one feature that doubled our retention",
    file_size_mb: 24.8, duration: 28.5, content_type: "technical_insight",
    created_at: "2026-07-05T14:20:00.000Z",
    youtube_video_id: "dq1Uq0tq1aA",
    metrics: { views: 42100, retention: 61.4, ctr: 7.2, impressions: 585000, fetched_at: "2026-07-06T09:00:00.000Z" },
  },
  {
    id: "demo-tech-daily-2",
    source_video: "tech-daily-e148.mp4",
    start_second: 1188.0, end_second: 1222.6,
    caption_style: "hormozi", crop_strategy: "speaker", format: "vertical",
    title: "Why we killed our best-selling product",
    file_size_mb: 30.1, duration: 34.6, content_type: "contrarian_take",
    created_at: "2026-07-05T14:22:00.000Z",
    youtube_video_id: "0Rt3zq9kf1o",
    metrics: { views: 18400, retention: 54.9, ctr: 6.1, impressions: 301000, fetched_at: "2026-07-06T09:00:00.000Z" },
  },
  {
    id: "demo-the-founder-1",
    source_video: "the-founder-e12.mp4",
    start_second: 402.1, end_second: 443.0,
    caption_style: "karaoke", crop_strategy: "speaker", format: "vertical",
    title: "How we scaled to 10k users with no ads",
    file_size_mb: 35.7, duration: 40.9, content_type: "story",
    created_at: "2026-07-04T18:05:00.000Z",
    youtube_video_id: "9bZkp7q19f0",
    metrics: { views: 96300, retention: 68.2, ctr: 9.1, impressions: 1058000, fetched_at: "2026-07-06T09:00:00.000Z" },
    generated_titles: [
      "How we scaled to 10k users with no ads",
      "10,000 users, $0 on ads. Here's how.",
      "The zero-budget growth loop that actually worked",
    ],
    description: "The founder breaks down the referral loop that took them from 0 to 10k users without spending a dollar on ads.",
    tags: "startup, growth, founder, saas",
    hashtags: "#startup #founder #growth",
  },
  {
    id: "demo-the-founder-2",
    source_video: "the-founder-e12.mp4",
    start_second: 2010.5, end_second: 2062.8,
    caption_style: "branded", crop_strategy: "face", format: "horizontal",
    title: "The hire that almost sank the company",
    file_size_mb: 58.3, duration: 52.3, content_type: "story",
    created_at: "2026-07-04T18:08:00.000Z",
    metrics: { views: 27700, retention: 58.6, ctr: 5.4, impressions: 512000, fetched_at: "2026-07-06T09:00:00.000Z" },
  },
  {
    id: "demo-deep-dive-1",
    source_video: "deep-dive-e07.mp4",
    start_second: 930.8, end_second: 954.7,
    caption_style: "subtle", crop_strategy: "speaker", format: "vertical",
    title: "That one call that changed everything",
    file_size_mb: 20.9, duration: 23.9, content_type: "emotional_peak",
    created_at: "2026-07-03T11:40:00.000Z",
    youtube_video_id: "kJQP7kiw5Fk",
    metrics: { views: 61200, retention: 64.7, ctr: 8.3, impressions: 738000, fetched_at: "2026-07-06T09:00:00.000Z" },
  },
  {
    id: "demo-deep-dive-2",
    source_video: "deep-dive-e07.mp4",
    start_second: 1502.2, end_second: 1532.5,
    caption_style: "branded", crop_strategy: "center", format: "square",
    title: "The research nobody wanted to fund",
    file_size_mb: 26.4, duration: 30.3, content_type: "technical_insight",
    created_at: "2026-07-03T11:44:00.000Z",
    metrics: { views: 14900, retention: 52.1, ctr: 5.9, impressions: 253000, fetched_at: "2026-07-06T09:00:00.000Z" },
  },
  {
    id: "demo-long-story-1",
    source_video: "long-story-e33.mp4",
    start_second: 744.0, end_second: 781.4,
    caption_style: "hormozi", crop_strategy: "speaker", format: "vertical",
    title: "It gets wild after the third year",
    file_size_mb: 32.6, duration: 37.4, content_type: "story",
    created_at: "2026-07-02T20:15:00.000Z",
    youtube_video_id: "L_jWHffIx5E",
    metrics: { views: 88700, retention: 66.9, ctr: 8.8, impressions: 964000, fetched_at: "2026-07-06T09:00:00.000Z" },
  },
  {
    id: "demo-long-story-2",
    source_video: "long-story-e33.mp4",
    start_second: 1890.7, end_second: 1912.9,
    caption_style: "karaoke", crop_strategy: "speaker", format: "vertical",
    title: "The moment we knew it would work",
    file_size_mb: 19.4, duration: 22.2, content_type: "emotional_peak",
    created_at: "2026-07-02T20:18:00.000Z",
    metrics: { views: 33500, retention: 62.3, ctr: 7.6, impressions: 441000, fetched_at: "2026-07-06T09:00:00.000Z" },
  },
];

export function demoClips(): ClipHistoryEntry[] {
  return CLIPS.map((c) => ({
    ...c,
    output_path: `demo/${c.id}.mp4`,
    thumbnail_config: { preview_path: preview(c.id), card_seconds: 1.5 },
  }));
}
