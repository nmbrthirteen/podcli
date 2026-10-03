/**
 * Thumbnail layouts: one face, or two people side by side from the clip.
 * backend/services/thumbnail_pair.py picks the people; this builds the
 * `thumbnail-render` call for a clip and reads back what it reported.
 *
 * No imports, so the studio client can bundle it as is.
 */

export type ThumbnailLayout = "single" | "pair";

/** One person on a pair-layout thumbnail and where their face came from. */
export interface ThumbnailPerson {
  side: "left" | "right";
  role: "guest" | "host" | null;
  from: "seats" | "multicam" | "image";
  source_time: number | null; // second in the source video the face was taken at
  camera?: string; // multicam only: the person's own camera file
  camera_time?: number; // multicam only: the second in that file
}

/** The layout a render ended up with, and why when it fell back to one face. */
export interface ThumbnailLayoutReport {
  layout: ThumbnailLayout;
  note?: string;
  people?: ThumbnailPerson[];
}

export interface ClipThumbnailRender {
  output: string;
  frame?: string | null;
  frameInfo?: unknown;
  line1?: string;
  line2?: string;
  layout?: unknown;
  swap?: boolean;
  clip: { source_video: string; start_second: number; end_second: number };
}

/** Whether a render needs a chosen frame. The pair layout takes both people from the clip. */
export function needsFrame(layout: unknown): boolean {
  return layout !== "pair";
}

/** Arguments for `podcli thumbnail-render` on one clip, ahead of the grounding flags and the title. */
export function thumbnailRenderArgs(r: ClipThumbnailRender): string[] {
  const pair = r.layout === "pair";
  const args = ["thumbnail-render", "--output", r.output];
  if (r.frame) args.push("--frame", r.frame);
  if (r.layout === "pair" || r.layout === "single") args.push("--layout", r.layout);
  if (pair) {
    args.push(
      "--video", r.clip.source_video,
      "--start", String(r.clip.start_second),
      "--end", String(r.clip.end_second),
    );
    if (r.swap) args.push("--swap");
  }
  if (r.line1) args.push(`--line1=${r.line1}`);
  if (r.line2) args.push(`--line2=${r.line2}`);
  // Face metadata describes the chosen frame, which a pair render does not draw.
  if (r.frameInfo && !pair) args.push("--frame-info", JSON.stringify(r.frameInfo));
  return args;
}

/** The PNG path and layout report from the last JSON line `thumbnail-render` printed. */
export function parseThumbnailRender(stdout: string): { path: string; report: ThumbnailLayoutReport } {
  const line = stdout.trim().split("\n").reverse().find((l) => l.trim().startsWith("{"));
  try {
    const parsed = JSON.parse(line || "{}");
    return {
      path: typeof parsed.path === "string" ? parsed.path : "",
      report: {
        layout: parsed.layout === "pair" ? "pair" : "single",
        ...(typeof parsed.note === "string" && { note: parsed.note }),
        ...(Array.isArray(parsed.people) && { people: parsed.people as ThumbnailPerson[] }),
      },
    };
  } catch {
    return { path: "", report: { layout: "single" } };
  }
}
