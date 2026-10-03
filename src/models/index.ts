import type { ClipHook } from "../utils/clip-hook.js";
import type { ThumbnailLayout, ThumbnailPerson } from "../utils/thumbnail-layout.js";

export type { ClipHook, HookMode } from "../utils/clip-hook.js";
export type { ThumbnailLayout, ThumbnailPerson } from "../utils/thumbnail-layout.js";

// === Task Communication Models ===

export interface TaskRequest {
  task_id: string;
  task_type: "transcribe" | "resolve_transcribe_engine" | "compare_engines" | "parse_transcript" | "create_clip" | "batch_clips" | "analyze_energy" | "detect_highlights" | "manage_reel" | "pack_transcript" | "detect_encoder" | "presets" | "ping" | "suggest_clips" | "find_moment" | "generate_content" | "generate_custom" | "corrections" | "manage_integrations" | "run_integration_tool" | "manage_config" | "manage_env" | "ai_cli_status" | "ai_provider_status" | "analyze_silence" | "render_silence_removed" | "manage_multicam";
  params: Record<string, unknown>;
}

export interface TaskResult<T = Record<string, unknown>> {
  task_id: string;
  status: "success" | "error";
  data?: T;
  error?: string;
}

export interface ProgressEvent {
  task_id: string;
  stage: string;
  percent: number;
  message: string;
  clip_result?: BatchClipsResult["results"][number];
  partial?: Record<string, unknown>;
}

// === Transcript Models ===

export interface WordTimestamp {
  word: string;
  start: number;
  end: number;
  confidence: number;
  speaker?: string | null;
}

export interface TranscriptSegment {
  id: number;
  start: number;
  end: number;
  text: string;
  speaker?: string | null;
}

export interface SpeakerInfo {
  total_time: number;
  segments: number;
  label: string;
}

export interface SpeakerSummary {
  num_speakers: number;
  speakers: Record<string, SpeakerInfo>;
}

export interface SpeakerSegment {
  speaker: string;
  start: number;
  end: number;
}

export interface TranscriptResult {
  transcript: string;
  segments: TranscriptSegment[];
  words: WordTimestamp[];
  duration: number;
  language: string;
  speakers: SpeakerSummary;
  speaker_segments: SpeakerSegment[];
  engine?: string;
  // Present only for a sample-mode transcription (start_seconds/duration_seconds):
  // complete is always false, and timestamps are relative to the sample window,
  // offset from the source by sample_offset_seconds.
  complete?: boolean;
  sample_offset_seconds?: number;
  // Whether diarization was actually tried for this result (set by every
  // branch of backend/services/transcription.py). False for engines that
  // never diarize (whispercpp, omnilingual) regardless of what was
  // requested, see needsDiarizationRetry in services/transcript-cache.ts.
  diarization_attempted?: boolean;
}

// === Clip Models ===

export type CaptionStyle = "branded" | "hormozi" | "karaoke" | "subtle";
export type CropStrategy = "center" | "face" | "speaker";
export type Format = "vertical" | "horizontal" | "square";

export interface ClipRequest {
  video_path: string;
  start_second: number;
  end_second: number;
  caption_style: CaptionStyle;
  crop_strategy: CropStrategy;
  title?: string;
  transcript_words: WordTimestamp[];
}

export interface ClipResult {
  output_path: string;
  duration: number;
  file_size_mb: number;
  format?: Format;
  caption_overlay_path?: string;
  cropped_source_path?: string;
  /** Sidecar subtitles, retimed to the exported file's own playback clock. */
  srt_path?: string;
  vtt_path?: string;
  /** Same audio/loudness/intro/outro, without burned captions. */
  clean_output_path?: string;
  /** The hook as rendered, edges widened to whole words. */
  hook?: ClipHook;
}

export interface SuggestedClip {
  clip_id: string;
  title: string;
  start_second: number;
  end_second: number;
  duration: number;
  payoff?: string;
  standalone?: string;
  context_line?: string;
  reasoning: string;
  preview_text: string;
  segments?: Array<{ start: number; end: number }>;
  /** Spoken passage from inside the clip, played before it. */
  hook?: ClipHook;
  suggested_caption_style?: string;
  timestamp_display?: string;
  content_type?: string;
  score?: number;
  rank?: number;
  /** Fingerprint of render-relevant fields, stamped when the clip is selected for export. */
  selectionHash?: string;
  /** True when the clip was edited after selectionHash was stamped, the selection may be stale. */
  changedSinceSelection?: boolean;
}

export interface UIState {
  videoPath?: string;
  filePath?: string;
  activeExportJobId?: string | null;
  transcript?: TranscriptResult | null;
  /** Identity (path + size + mtime) of the video the transcript was generated from. */
  transcriptVideoIdentity?: { path: string; size: number; mtimeMs: number } | null;
  rawTranscriptText?: string;
  silenceOriginal?: { videoPath: string; transcript: TranscriptResult } | null;
  silencePlan?: Record<string, unknown> | null;
  /** True when videoPath didn't exist at last check (e.g. an external drive
   * is unmounted). The session is kept, not wiped, while this is set. */
  videoMissing?: boolean;
  suggestions?: SuggestedClip[];
  deselectedIndices?: number[];
  settings?: {
    captionStyle?: string;
    cropStrategy?: string;
    format?: Format;
    logoPath?: string;
    outroPath?: string;
    introPath?: string;
    cleanFillers?: boolean;
    captionPosition?: string;
    captionFontScale?: number;
    logoPosition?: string;
    onboardingDismissed?: boolean;
    silenceThreshold?: number;
    silenceMinPause?: number;
    silencePadding?: number;
  };
  phase?: string;
  lastUpdated?: number;
}

/**
 * Decisions the user has already answered for one episode, so /auto and
 * /produce-shorts ask each question once and reuse the answer on every later
 * run against the same video, including after a Web UI restart, since this
 * is keyed by video identity and stored independently of ui-state.json.
 */
export interface EpisodeDecisions {
  videoPath: string;
  fileSize: number;
  clipCount?: number;
  clipDurationRange?: { min?: number; max?: number };
  captionStyle?: string;
  captionsEnabled?: boolean;
  language?: string;
  thumbnailsWanted?: boolean;
  deliveryTarget?: string;
  notes?: string;
  updatedAt: number;
}

/** One decision from EpisodeDecisions that has no answer yet. */
export interface OpenQuestion {
  field: keyof EpisodeDecisions;
  question: string;
}

/** Who is speaking, shown as a lower third for the first seconds of a clip. */
export interface NameCard {
  title: string;
  subtitle?: string;
  seconds?: number;
  accent?: string;
}

/**
 * How a part of a clip arrives and leaves.
 *
 * Omitted, each caption style uses the motion it has always had.
 */
export interface Motion {
  enter?: "none" | "fade" | "rise" | "pop";
  exit?: "none" | "fade" | "sink";
  /** Frames, at the render's fps. */
  duration?: number;
  feel?: "snap" | "soft" | "linear";
}

export interface ClipMotion {
  captions?: Motion;
  nameCard?: Motion;
}

export interface CreateClipInput {
  clip_number?: number;
  video_path?: string;
  start_second?: number;
  end_second?: number;
  title?: string;
  caption_style?: string;
  crop_strategy?: string;
  format?: Format;
  logo_path?: string;
  outro_path?: string;
  intro_path?: string;
  name_card?: NameCard;
  motion?: ClipMotion;
  transcript_words?: WordTimestamp[];
  clean_fillers?: boolean;
  allow_ass_fallback?: boolean;
  keep_caption_overlay?: boolean;
  write_clean_variant?: boolean;
  /** Overrides the suggestion's hook; null renders without one. */
  hook?: ClipHook | null;
}

export interface BatchClipSpec {
  start_second: number;
  end_second: number;
  title?: string;
  caption_style?: string;
  crop_strategy?: string;
  format?: Format;
  logo_path?: string | null;
  intro_path?: string | null;
  allow_ass_fallback?: boolean;
  keep_caption_overlay?: boolean;
  write_clean_variant?: boolean;
  keep_segments?: Array<{ start: number; end: number }>;
  hook?: ClipHook | null;
}

export interface BatchClipsInput {
  video_path?: string;
  transcript_words?: WordTimestamp[];
  clip_numbers?: number[];
  clips?: BatchClipSpec[];
  export_selected?: boolean;
  format?: Format;
  clean_fillers?: boolean;
  allow_ass_fallback?: boolean;
  keep_caption_overlay?: boolean;
  write_clean_variant?: boolean;
  /**
   * When true, POST to the Web UI's /api/batch-clips and return a job_id
   * immediately so the caller can poll job_status and emit live progress.
   * Requires the Web UI to be running (npm run ui).
   */
  async_mode?: boolean;
}

export interface BatchClipsResult {
  total_clips: number;
  successful_clips: number;
  results: Array<{
    clip_index?: number;
    status: "success" | "error";
    output_path?: string;
    start_second?: number;
    end_second?: number;
    /** Bounds of the clip as submitted; the renderer may trim start_second. */
    source_start_second?: number;
    source_end_second?: number;
    caption_style?: string;
    crop_strategy?: string;
    format?: Format;
    title?: string;
    file_size_mb?: number;
    duration?: number;
    srt_path?: string;
    vtt_path?: string;
    clean_output_path?: string;
    error?: string;
  }>;
}

// === Asset Models ===

export type AssetType =
  | "logo"
  | "outro"
  | "intro"
  | "music"
  | "video"
  | "image"
  | "audio"
  | "other";

export interface Asset {
  name: string;
  type: AssetType;
  path: string;
  addedAt: string;
  default?: boolean;
}

export const ASSETS_SCHEMA_VERSION = 2;

export interface AssetRegistry {
  schemaVersion?: number;
  assets: Asset[];
}

// === Clip History Models ===

export interface ClipPerformanceMetrics {
  views?: number;
  retention?: number; // averageViewPercentage, 0-100
  ctr?: number; // impressionsClickThroughRate, 0-100
  impressions?: number;
  fetched_at?: string;
}

export interface ClipThumbnailConfig {
  text?: string;
  line1?: string; // explicit first line (overrides the AI split)
  line2?: string; // explicit second line
  image_path?: string; // user-supplied background image
  timestamp?: number; // absolute second in the source video for the frame
  preview_path?: string; // chosen thumbnail PNG
  variations?: string[]; // all generated thumbnail PNGs to pick from
  card_seconds?: number; // duration of the thumbnail card baked into the clip start
  layout?: ThumbnailLayout; // the layout the chosen thumbnail was drawn with
  people?: ThumbnailPerson[]; // pair layout only: who sits on each side
}

export interface ClipHistoryEntry {
  id: string;
  source_video: string;
  start_second: number;
  end_second: number;
  caption_style: string;
  crop_strategy: string;
  format?: Format;
  logo_path?: string;
  outro_path?: string;
  intro_path?: string;
  title: string;
  output_path: string;
  file_size_mb: number;
  duration: number;
  created_at: string;
  content_type?: string;
  transcript_slice?: string;
  // Carried from the suggestion at render time: the payoff, the question it
  // answers, and its verbatim opening line. Thumbnail generation grounds
  // headline copy in these instead of the title alone.
  payoff?: string;
  context_line?: string;
  preview_text?: string;
  logo_backup_path?: string;
  logo_position?: string;
  keep_segments?: Array<{ start: number; end: number }>;
  thumbnail_config?: ClipThumbnailConfig;
  youtube_video_id?: string;
  metrics?: ClipPerformanceMetrics;
  // AI-generated publishing metadata (titles/description/tags/hashtags), persisted
  // so it survives a page reload instead of vanishing after generation.
  generated_titles?: string[];
  description?: string;
  tags?: string;
  hashtags?: string;
  // Set for signed-in users once the clip is mirrored to the workspace. A false
  // cloud_synced marks a clip a later sweep should backfill; the local file
  // stays the source of truth either way.
  cloud_id?: string;
  cloud_synced?: boolean;
  cloud_video_uploaded?: boolean;
}

// === Knowledge Base Models ===

export interface KnowledgeFile {
  filename: string;
  content: string;
  updatedAt: string;
}
