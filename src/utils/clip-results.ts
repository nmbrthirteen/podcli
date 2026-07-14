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
  const spec =
    typeof row.clip_index === "number" ? clipSpecs?.[row.clip_index] : undefined;
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
  if (typeof percent === "number" && percent > job.progress) job.progress = percent;
  return job.progress;
}
