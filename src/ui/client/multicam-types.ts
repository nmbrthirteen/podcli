// Shared types and small URL helpers for the multicam edit page and its
// sibling components. Field names mirror the Python session payload exactly
// (see backend/services/multicam.py: payload()).

export interface McPerson {
  id: string;
  name: string;
  role?: "host" | "guest";
}

export type McRole = "camera" | "mic" | "ignore";
export type McSyncStatus = "reference" | "ok" | "review" | "rough" | "failed" | "manual" | "assumed";

export interface McSourceSync {
  status?: McSyncStatus;
  method?: "manual";
  anchors?: { timeline: number; source: number }[];
  score?: number;
  checkpoints?: number;
  residual_ms?: number;
  residual_all_ms?: number;
  drift_ppm?: number;
  message?: string;
  reasons?: string[];
}

export interface McSource {
  id: string;
  path: string;
  name: string;
  kind: "video" | "audio";
  duration: number;
  has_audio: boolean;
  audio_channels: number;
  audio_stream_count?: number;
  audio_stream_index?: number;
  width?: number;
  height?: number;
  fps?: number;
  fps_warning?: string;
  role: McRole;
  person: string;
  channel_people: string[];
  input_lut?: string;
  guessed: boolean;
  offset: number | null;
  speed: number;
  timeline_start: number | null;
  timeline_end: number | null;
  sync: McSourceSync;
  parent: string;
  crop: number[];
  members: string[];
}

export interface McCut {
  start: number;
  end: number;
  source_id: string;
}

export type McStyle = "auto" | "studio" | "remote";

export interface McCutSettings {
  style: McStyle;
  min_shot: number;
  max_shot: number;
  wide_insert: number;
  backchannel: number;
  hold_guest: boolean;
  host_solo: boolean;
  guest_min: number;
  guest_delay: number;
}

export interface McStats {
  shots?: number;
  duration?: number;
  average_shot?: number;
  share?: Record<string, number>;
}

export interface McValidation {
  frames_expected: number;
  frames_actual: number;
  duration: number;
  lufs: number | null;
  true_peak: number | null;
  warnings: string[];
}

export interface McOutputs {
  video?: string;
  stems?: string[];
  duration?: number;
  validation?: McValidation;
  premiere?: string;
  fcpxml?: string;
  color_handoff?: string;
}

export interface McSession {
  session_id: string;
  name: string;
  folder: string;
  people: McPerson[];
  sources: McSource[];
  reference_id: string;
  range_start: number | null;
  range_end: number | null;
  look: string;
  looks: string[];
  cut_settings: McCutSettings;
  resolved_style: "studio" | "remote";
  auto_style: "studio" | "remote";
  removals: { start: number; end: number; reason?: string }[];
  cloud?: { id?: string; url?: string };
  cuts: McCut[];
  stats: McStats;
  speaker_map: Record<string, string>;
  has_person_mics: boolean;
  timeline_duration: number;
  outputs: McOutputs;
  active_job?: { id: string; action: McJobKind };
}

export interface McSessionSummary {
  session_id: string;
  name: string;
  sources: number;
  cameras: number;
  synced: boolean;
  shots: number;
  video: string | null;
}

export interface McPreviews {
  cameras: Record<string, string>;
  /** Camera id to look name to still path. */
  looks: Record<string, Record<string, string>>;
}

export interface McPreviewsResp extends McSession {
  previews: McPreviews;
}

export type McJobKind = "sync" | "plan" | "render" | "preview" | "cloud" | "pull";

export const mcImageUrl = (path: string) => `/api/multicam/image?path=${encodeURIComponent(path)}`;
export const mcFileUrl = (path: string) => `/api/multicam/file?path=${encodeURIComponent(path)}`;
export const mcStreamUrl = (path: string) => `/api/stream-source?path=${encodeURIComponent(path)}`;

export const personName = (people: McPerson[], id: string): string =>
  people.find((p) => p.id === id)?.name || id;

/** Who a file shows or records, in the words the UI uses everywhere. */
export function whoLabel(people: McPerson[], source: McSource): string {
  if (source.role === "camera") return source.person === "wide" ? "Everyone (wide)" : personName(people, source.person);
  if (source.channel_people.length) return source.channel_people.map((id) => personName(people, id) || "Nobody").join(" / ");
  return source.person ? personName(people, source.person) : "Shared room mic";
}

export type McEdit = (body: Record<string, unknown>) => void;

// One color per camera in the order the cameras are listed, so the shot strip
// and the legend agree on which color means which camera.
const CAMERA_COLORS = ["var(--accent)", "var(--green)", "var(--blue)", "var(--amber)", "var(--red)", "var(--text2)"];

export function cameraColorMap(cameraIds: string[]): Record<string, string> {
  const map: Record<string, string> = {};
  cameraIds.forEach((id, i) => {
    map[id] = CAMERA_COLORS[i % CAMERA_COLORS.length];
  });
  return map;
}
