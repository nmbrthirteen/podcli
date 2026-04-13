/**
 * suggest_clips tool — helps Claude structure its clip suggestions.
 *
 * This tool doesn't do heavy processing. Claude analyzes the transcript
 * in conversation, then calls this tool to store/format the suggestions.
 */

import { randomUUID } from "crypto";

export const suggestClipsToolDef = {
  name: "suggest_clips",
  description:
    "STEP 2 — Submit your clip suggestions after analyzing the transcript.\n\n" +
    "Before calling this: read the transcript via get_ui_state(include_transcript: true) " +
    "and identify the best viral moments.\n\n" +
    "What it does: Stores your suggestions, assigns clip numbers (#1, #2, etc.), " +
    "and pushes them to the Web UI for the user to review.\n\n" +
    "After this: the user reviews in the UI. Then export with " +
    "batch_create_clips(export_selected: true) or create_clip(clip_number: N).",
  inputSchema: {
    type: "object" as const,
    properties: {
      suggestions: {
        type: "array",
        description: "Array of suggested clip moments",
        items: {
          type: "object",
          properties: {
            title: {
              type: "string",
              description: "Short catchy title for the clip",
            },
            start_second: {
              type: "number",
              description: "Start timestamp in seconds",
            },
            end_second: {
              type: "number",
              description: "End timestamp in seconds",
            },
            segments: {
              type: "array",
              description:
                "Multi-cut keep-ranges within the clip. Use to cut out filler/tangents " +
                "in the middle. If omitted, the full start→end range is used.",
              items: {
                type: "object",
                properties: {
                  start: { type: "number" },
                  end: { type: "number" },
                },
                required: ["start", "end"],
              },
            },
            reasoning: {
              type: "string",
              description: "Why this moment is viral-worthy",
            },
            preview_text: {
              type: "string",
              description: "Brief text preview of what's said",
            },
            content_type: {
              type: "string",
              description:
                "Content classification: guest_story, technical_insight, market_landscape, business_strategy, hot_take",
            },
            score: {
              type: "number",
              description: "Virality score (0-20). Sum of standalone + hook + relevance + quotability (each 1-5).",
            },
            suggested_caption_style: {
              type: "string",
              enum: ["hormozi", "karaoke", "subtle", "branded"],
              description: "Recommended caption style for this clip",
            },
          },
          required: ["title", "start_second", "end_second", "reasoning"],
        },
      },
    },
    required: ["suggestions"],
  },
};

export interface RawSuggestion {
  title: string;
  start_second: number;
  end_second: number;
  segments?: Array<{ start: number; end: number }>;
  reasoning: string;
  preview_text?: string;
  content_type?: string;
  score?: number;
  suggested_caption_style?: string;
}

export interface SuggestClipsInput {
  suggestions: RawSuggestion[];
}

export async function handleSuggestClips(input: SuggestClipsInput): Promise<string> {
  const suggestions = input.suggestions;

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
