/**
 * suggest_clips tool — helps Claude structure its clip suggestions.
 *
 * This tool doesn't do heavy processing. Claude analyzes the transcript
 * in conversation, then calls this tool to store/format the suggestions.
 */

import { randomUUID } from "crypto";
import { z } from "zod";
import {
  validateSuggestionContext,
  validateSuggestionRange,
} from "../utils/clip-validation.js";

const suggestionSchema = z.object({
  title: z.string().describe("Short catchy title for the clip"),
  start_second: z
    .number()
    .describe(
      "Start timestamp in seconds. If the moment is an answer, move this back " +
        "to include the question that prompted it.",
    ),
  end_second: z.number().describe("End timestamp in seconds"),
  segments: z
    .array(z.object({ start: z.number(), end: z.number() }))
    .optional()
    .describe(
      "Multi-cut keep-ranges within the clip. Use to cut out filler/tangents " +
        "in the middle. Omit for a single continuous clip.",
    ),
  payoff: z
    .string()
    .describe(
      "What the viewer walks away with. One sentence, second person, e.g. " +
        '"You learn why raising a seed round early cost them control of pricing." ' +
        "Not a description of the clip and not a restatement of the title.",
    ),
  standalone: z
    .string()
    .describe(
      "What a viewer who never heard this episode must already know to follow " +
        'the clip. Write "nothing" when the clip carries its own setup.',
    ),
  context_line: z
    .string()
    .optional()
    .describe(
      "The question or setup that makes the clip land, rewritten to one line " +
        "for on-screen text. Required whenever standalone is not \"nothing\", " +
        "or the clip opens on a word pointing back before the cut.",
    ),
  reasoning: z
    .string()
    .describe("Why this earns 30 seconds of a stranger's attention"),
  preview_text: z
    .string()
    .describe(
      "The first sentence or two the viewer actually hears, verbatim from " +
        "start_second. This is what the standalone check reads, so it has to be " +
        "the real opening line, not a paraphrase.",
    ),
  content_type: z
    .string()
    .optional()
    .describe(
      "Content classification: guest_story, technical_insight, market_landscape, business_strategy, hot_take",
    ),
  score: z
    .number()
    .optional()
    .describe(
      "Virality score (0-20). Sum of standalone + hook + relevance + quotability (each 1-5).",
    ),
  suggested_caption_style: z
    .enum(["hormozi", "karaoke", "subtle", "branded"])
    .optional()
    .describe("Recommended caption style for this clip"),
});

/** Single source of truth for the tool's arguments; server.ts registers this. */
export const suggestClipsInputShape = {
  suggestions: z
    .array(suggestionSchema)
    .describe("Array of suggested clip moments"),
};

export const suggestClipsToolDef = {
  name: "suggest_clips",
  description:
    "STEP 2 — Submit your clip suggestions after analyzing the transcript.\n\n" +
    "Before calling this: read the transcript via get_ui_state(include_transcript: true) " +
    "and identify the best viral moments.\n\n" +
    "Every suggestion must carry its own context. A clip that opens on an answer " +
    "whose question stayed behind the cut is rejected. Either widen start_second " +
    "to include the question, or supply context_line.\n\n" +
    "What it does: Stores your suggestions, assigns clip numbers (#1, #2, etc.), " +
    "and pushes them to the Web UI for the user to review.\n\n" +
    "After this: the user reviews in the UI. Then export with " +
    "batch_create_clips(export_selected: true) or create_clip(clip_number: N).",
};

export type RawSuggestion = z.infer<typeof suggestionSchema>;

export interface SuggestClipsInput {
  suggestions: RawSuggestion[];
}

export async function handleSuggestClips(input: SuggestClipsInput): Promise<string> {
  const suggestions = input.suggestions;

  const problems: string[] = [];
  for (let i = 0; i < suggestions.length; i++) {
    const s = suggestions[i];
    const error =
      validateSuggestionRange(s.start_second, s.end_second) ||
      validateSuggestionContext(s);
    if (error) problems.push(`Suggestion ${i + 1} ("${s.title}"): ${error}`);
  }
  if (problems.length > 0) {
    throw new Error(
      `${problems.length} of ${suggestions.length} suggestions need work. ` +
        `Fix them and call suggest_clips again with the full list.\n\n${problems.join("\n")}`,
    );
  }

  // Validate and enrich suggestions
  const enriched = suggestions.map((s, i) => {
    // Compute duration from segments if available, otherwise from start/end
    const segments = s.segments?.filter((seg) => seg.end > seg.start) || [];
    const keptDuration = segments.length > 0
      ? segments.reduce((sum, seg) => sum + (seg.end - seg.start), 0)
      : s.end_second - s.start_second;

    return {
      clip_number: i + 1,
      clip_id: randomUUID(),
      title: s.title,
      start_second: s.start_second,
      end_second: s.end_second,
      segments: segments.length > 0 ? segments : [{ start: s.start_second, end: s.end_second }],
      duration: Math.round(keptDuration * 10) / 10,
      payoff: s.payoff,
      standalone: s.standalone,
      context_line: s.context_line || "",
      reasoning: s.reasoning,
      preview_text: s.preview_text || "",
      content_type: s.content_type || "unknown",
      score: s.score || 0,
      suggested_caption_style: s.suggested_caption_style || "hormozi",
      timestamp_display: `${formatTime(s.start_second)} → ${formatTime(s.end_second)}`,
    };
  });

  return JSON.stringify({
    clip_count: enriched.length,
    total_content_seconds: enriched.reduce((sum, c) => sum + c.duration, 0),
    clips: enriched,
  });
}

function formatTime(seconds: number): string {
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${m}:${s.toString().padStart(2, "0")}`;
}
