import { z } from "zod";
import type { ThumbnailLayoutReport } from "./thumbnail-layout.js";

const thumbnailPersonSchema = z.object({
  side: z.enum(["left", "right"]),
  role: z.enum(["guest", "host"]).nullable(),
  from: z.enum(["seats", "multicam", "image"]),
  source_time: z.number().nullable(),
  camera: z.string().optional(),
  camera_time: z.number().optional(),
});

const renderOutputSchema = z.looseObject({
  path: z.string().catch(""),
  layout: z.string().optional(),
  note: z.string().optional().catch(undefined),
  people: z.array(thumbnailPersonSchema).optional().catch(undefined),
});

export interface ThumbnailRenderOutput {
  path: string;
  report: ThumbnailLayoutReport;
}

/** The PNG path and layout report from the last JSON line `thumbnail-render` printed. */
export function parseThumbnailRender(stdout: string): ThumbnailRenderOutput {
  const line = stdout.trim().split("\n").reverse().find((l) => l.trim().startsWith("{"));

  try {
    const parsed = renderOutputSchema.parse(JSON.parse(line || "{}"));

    return {
      path: parsed.path,
      report: {
        layout: parsed.layout === "pair" ? "pair" : "single",
        ...(parsed.note !== undefined && { note: parsed.note }),
        ...(parsed.people && { people: parsed.people }),
      },
    };
  } catch {
    return { path: "", report: { layout: "single" } };
  }
}
