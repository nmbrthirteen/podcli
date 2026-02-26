// === Task Communication Models ===

export interface TaskRequest {
  task_id: string;
  task_type: "transcribe" | "parse_transcript" | "create_clip" | "batch_clips" | "analyze_energy" | "detect_encoder" | "presets" | "ping";
  params: Record<string, unknown>;
}

export interface TaskResult {
  task_id: string;
  status: "success" | "error";
  data?: Record<string, unknown>;
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

export type CaptionStyle = "hormozi" | "karaoke" | "subtle";
export type CropStrategy = "center" | "face";

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
