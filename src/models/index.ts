// === Task Communication Models ===

export interface TaskRequest {
  task_id: string;
  task_type: "transcribe" | "parse_transcript" | "create_clip" | "batch_clips" | "analyze_energy" | "pack_transcript" | "detect_encoder" | "presets" | "ping" | "suggest_clips" | "generate_content" | "corrections";
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
}

// === Clip Models ===

export type CaptionStyle = "branded" | "hormozi" | "karaoke" | "subtle";
export type CropStrategy = "center" | "face" | "speaker";

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
}

export interface SuggestedClip {
  clip_id: string;
  title: string;
  start_second: number;
  end_second: number;
  duration: number;
  reasoning: string;
  preview_text: string;
  segments?: Array<{ start: number; end: number }>;
  suggested_caption_style?: string;
  timestamp_display?: string;
  content_type?: string;
  score?: number;
}

export interface UIState {
  videoPath?: string;
  filePath?: string;
  transcript?: TranscriptResult | null;
  rawTranscriptText?: string;
  suggestions?: SuggestedClip[];
  deselectedIndices?: number[];
  settings?: {
    captionStyle?: string;
    cropStrategy?: string;
    logoPath?: string;
    outroPath?: string;
  };
  phase?: string;
  lastUpdated?: number;
}

export interface CreateClipInput {
  clip_number?: number;
  video_path?: string;
  start_second?: number;
  end_second?: number;
  title?: string;
  caption_style?: string;
  crop_strategy?: string;
  logo_path?: string;
  outro_path?: string;
  transcript_words?: WordTimestamp[];
  clean_fillers?: boolean;
  allow_ass_fallback?: boolean;
}

export interface BatchClipSpec {
  start_second: number;
  end_second: number;
  title?: string;
  caption_style?: string;
  crop_strategy?: string;
  logo_path?: string | null;
  allow_ass_fallback?: boolean;
  keep_segments?: Array<{ start: number; end: number }>;
}

export interface BatchClipsInput {
  video_path?: string;
  transcript_words?: WordTimestamp[];
  clip_numbers?: number[];
  clips?: BatchClipSpec[];
  export_selected?: boolean;
  clean_fillers?: boolean;
  allow_ass_fallback?: boolean;
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
    status: "success" | "error";
    output_path?: string;
    start_second?: number;
    end_second?: number;
    caption_style?: string;
    crop_strategy?: string;
    title?: string;
    file_size_mb?: number;
    duration?: number;
    error?: string;
  }>;
}

// === Asset Models ===

export interface Asset {
  name: string;
  type: "logo" | "video" | "image" | "other";
  path: string;
  addedAt: string;
}

export interface AssetRegistry {
  assets: Asset[];
}

// === Clip History Models ===

export interface ClipHistoryEntry {
  id: string;
  source_video: string;
  start_second: number;
  end_second: number;
  caption_style: string;
  crop_strategy: string;
  logo_path?: string;
  title: string;
  output_path: string;
  file_size_mb: number;
  duration: number;
  created_at: string;
}

// === Knowledge Base Models ===

export interface KnowledgeFile {
  filename: string;
  content: string;
  updatedAt: string;
}
