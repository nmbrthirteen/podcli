// Shared types and small URL helpers for the multicam edit page and its
// sibling components. Field names mirror the Python session payload exactly
// (see backend/services/multicam.py: payload()).

export interface McPerson {
  id: string;
  name: string;
}

export type McRole = "camera" | "mic" | "ignore";
export type McSyncStatus = "reference" | "ok" | "rough" | "failed" | "manual";

export interface McSourceSync {
  status?: McSyncStatus;
  score?: number;
  checkpoints?: number;
  residual_ms?: number;
  drift_ppm?: number;
  message?: string;
}

export interface McSource {
  id: string;
  path: string;
  name: string;
  kind: "video" | "audio";
  duration: number;
  has_audio: boolean;
  audio_channels: number;
  width?: number;
  height?: number;
  fps?: number;
  role: McRole;
  person: string;
  channel_people: string[];
  guessed: boolean;
  offset: number | null;
  speed: number;
  timeline_start: number | null;
  timeline_end: number | null;
  sync: McSourceSync;
}

export interface McCut {
  start: number;
  end: number;
  source_id: string;
}

export interface McCutSettings {
  min_shot: number;
  max_shot: number;
  wide_insert: number;
}

export interface McStats {
  shots?: number;
  duration?: number;
  average_shot?: number;
  share?: Record<string, number>;
}

export interface McOutputs {
  video?: string;
  stems?: string[];
  duration?: number;
  premiere?: string;
  fcpxml?: string;
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
  looks: Record<string, string>;
}

export interface McPreviewsResp extends McSession {
  previews: McPreviews;
}

export type McJobKind = "sync" | "plan" | "render";

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

// Stable color per camera, assigned in first-appearance order across the cuts
// so the shot strip and legend always agree on which color means which camera.
const CAMERA_COLORS = ["var(--accent)", "var(--green)", "var(--blue)", "var(--amber)", "var(--red)", "var(--text2)"];

export function cameraColorMap(cameraIds: string[]): Record<string, string> {
  const map: Record<string, string> = {};
  cameraIds.forEach((id, i) => {
    map[id] = CAMERA_COLORS[i % CAMERA_COLORS.length];
  });
  return map;
}
