/**
 * suggest_clips tool — helps Claude structure its clip suggestions.
 *
 * This tool doesn't do heavy processing. Claude analyzes the transcript
 * in conversation, then calls this tool to store/format the suggestions.
 */

export const suggestClipsToolDef = {
  name: "suggest_clips",
  description:
    "Store and structure clip suggestions from transcript analysis. " +
    "Use after analyzing a transcript to format suggested viral moments " +
    "with timestamps, titles, and reasoning. Claude should analyze the " +
    "transcript first, then call this tool to present suggestions to the user.",
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
    clip_id: `clip_${i + 1}`,
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
