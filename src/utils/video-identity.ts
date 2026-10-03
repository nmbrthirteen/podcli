import { statSync } from "fs";

/**
 * Identifies a specific video file on disk, not just its path, so a
 * transcript can be tied to the exact bytes it was generated from. A path
 * can be reused for a different recording (re-record, re-encode, swap in a
 * trimmed version); size and mtime catch that even though the path matches.
 */
export interface VideoIdentity {
  path: string;
  size: number;
  mtimeMs: number;
}

/** Stat a video file into a VideoIdentity, or null if it can't be read. */
export function computeVideoIdentity(path: string): VideoIdentity | null {
  try {
    const stat = statSync(path);
    return { path, size: stat.size, mtimeMs: stat.mtimeMs };
  } catch {
    return null;
  }
}

/** True when two identities refer to the same file snapshot. */
export function identitiesMatch(
  a: VideoIdentity | null | undefined,
  b: VideoIdentity | null | undefined,
): boolean {
  if (!a || !b) return false;
  return a.path === b.path && a.size === b.size && a.mtimeMs === b.mtimeMs;
}

/**
 * Checks whether a session's recorded transcript still belongs to the video
 * currently loaded at videoPath. Returns an error string naming the mismatch
 * when it doesn't, or null when it's safe to proceed (including when there's
 * no recorded identity to check, e.g. older sessions before this check existed).
 */
export function transcriptVideoMismatch(
  transcriptVideoIdentity: VideoIdentity | null | undefined,
  videoPath: string,
): string | null {
  if (!transcriptVideoIdentity) return null;
  const current = computeVideoIdentity(videoPath);
  if (!current) return null;
  if (identitiesMatch(transcriptVideoIdentity, current)) return null;
  return (
    `Session transcript belongs to a different video than the one currently set ` +
    `(${transcriptVideoIdentity.path}). Re-transcribe or re-import a transcript for ` +
    `${videoPath} before rendering.`
  );
}
