import type { BatchClipsResult } from "../models/index.js";

type ClipResultRow = BatchClipsResult["results"][number];

export type ClipBounds = { start_second: number; end_second: number };

// Python numbers its result rows by position in the clips array it was handed,
// which for an agent-driven export is not the studio's clip order. Stamp each row
// with the bounds of the clip that was submitted for it so the client can match a
// result to its own clip instead of trusting the index. The rendered start_second
// can't stand in for that: the renderer trims weak openings and reports the
// trimmed value.
export function tagSubmittedClip(
  row: ClipResultRow,
  clipSpecs?: ClipBounds[],
): ClipResultRow {
  const spec = row.clip_index !== undefined ? clipSpecs?.[row.clip_index] : undefined;

  if (!spec) return row;

  return {
    ...row,
    source_start_second: spec.start_second,
    source_end_second: spec.end_second,
  };
}

export function tagSubmittedClips(
  data: BatchClipsResult | undefined,
  clipSpecs?: ClipBounds[],
): BatchClipsResult | undefined {
  if (!data?.results) return data;

  return {
    ...data,
    results: data.results.map((row) => tagSubmittedClip(row, clipSpecs)),
  };
}

// Clips render in parallel, so each worker reports its own share of the batch and
// the percentages arrive out of order. Only ever move the bar forward.
export function advanceProgress(job: { progress: number }, percent: number): number {
  if (percent > job.progress) job.progress = percent;

  return job.progress;
}

export interface BatchOutcome {
  status: "done" | "error";
  message: string;
  /** Set when no clip rendered: the first clip's error, for the job's error field. */
  error?: string;
}

// Remotion failures carry long stack traces; the status line only needs the gist.
// The full text stays in result.results[].error.
const MAX_ERROR_CHARS = 300;

function shortError(text: string): string {
  const oneLine = text.replace(/\s+/g, " ").trim();

  return oneLine.length > MAX_ERROR_CHARS ? `${oneLine.slice(0, MAX_ERROR_CHARS - 1)}…` : oneLine;
}

function clipLabel(row: ClipResultRow, position: number): string {
  return `clip ${(row.clip_index ?? position) + 1}`;
}

// The Python batch task succeeds as a task even when every clip in it failed, so
// the job's final state has to come from the per-clip rows: a batch where nothing
// rendered is a failed job, and a partial one says which clips are missing.
export function summarizeBatchOutcome(
  data: BatchClipsResult | undefined,
  successMessage: string,
): BatchOutcome {
  const results = data?.results ?? [];

  if (!data || results.length === 0) {
    const error = "The batch render returned no clip results.";

    return { status: "error", message: `Error: ${error}`, error };
  }

  const total = data.total_clips ?? results.length;

  const failures = results
    .map((row, position) => ({ row, position }))
    .filter(({ row }) => row.status !== "success");

  const rendered = results.length - failures.length;

  if (failures.length === 0) return { status: "done", message: successMessage };

  const failureList = failures
    .map(({ row, position }) => `${clipLabel(row, position)}: ${shortError(row.error || "unknown error")}`)
    .join("; ");

  if (rendered === 0) {
    const first = failures[0];
    const error = `All ${total} clips failed. First error (${clipLabel(first.row, first.position)}): ${shortError(first.row.error || "unknown error")}`;

    return { status: "error", message: `Error: ${error}`, error };
  }

  return { status: "done", message: `${rendered} of ${total} clips rendered. Failed: ${failureList}` };
}
