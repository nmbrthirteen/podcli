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
            reasoning: {
              type: "string",
              description: "Why this moment is viral-worthy",
            },
            preview_text: {
              type: "string",
              description: "Brief text preview of what's said",
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

export async function handleSuggestClips(
  input: Record<string, unknown>
): Promise<string> {
  const suggestions = input.suggestions as Array<{
    title: string;
    start_second: number;
    end_second: number;
    reasoning: string;
    preview_text?: string;
    suggested_caption_style?: string;
  }>;

  // Validate and enrich suggestions
  const enriched = suggestions.map((s, i) => ({
    clip_number: i + 1,
    clip_id: randomUUID(),
    title: s.title,
    start_second: s.start_second,
    end_second: s.end_second,
    duration: Math.round((s.end_second - s.start_second) * 10) / 10,
    reasoning: s.reasoning,
    preview_text: s.preview_text || "",
    suggested_caption_style: s.suggested_caption_style || "hormozi",
    timestamp_display: `${formatTime(s.start_second)} → ${formatTime(s.end_second)}`,
  }));

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
