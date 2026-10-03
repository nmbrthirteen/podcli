import { PythonExecutor } from "../services/python-executor.js";

const executor = new PythonExecutor();

export interface CompareEnginesInput {
  file_path: string;
  engine_a: string;
  engine_b: string;
  start_seconds?: number;
  duration_seconds?: number;
  window_seconds?: number;
  model_size?: string;
  language?: string;
  output_dir?: string;
}

export const compareEnginesToolDef = {
  name: "compare_transcription_engines",
  description:
    "Transcribe the same sample window of a file with two engines and report where their output " +
    "disagrees, in 20s windows by default. This measures disagreement between the two engines' " +
    "output, not accuracy against a ground-truth transcript. Neither engine is assumed correct. " +
    "Writes comparison.json and a self-contained comparison.html (with a sample audio player and " +
    "per-window seek buttons) to output_dir.",
  inputSchema: {
    type: "object" as const,
    properties: {
      file_path: { type: "string", description: "Absolute path to the podcast file" },
      engine_a: {
        type: "string",
        enum: ["whisper-py", "whispercpp", "assemblyai", "omnilingual"],
        description: "First engine to compare",
      },
      engine_b: {
        type: "string",
        enum: ["whisper-py", "whispercpp", "assemblyai", "omnilingual"],
        description: "Second engine to compare",
      },
      start_seconds: {
        type: "number",
        description: "Sample start, seconds into the source. Default: 0.",
      },
      duration_seconds: {
        type: "number",
        description: "Sample length in seconds. Default: 120.",
      },
      window_seconds: {
        type: "number",
        description: "Report window size in seconds. Default: 20.",
      },
      model_size: {
        type: "string",
        enum: ["tiny", "base", "small", "medium", "large"],
        description: "Model size for engines that take one. Default: base.",
      },
      language: {
        type: "string",
        description: "ISO language code. Leave empty for auto-detect.",
      },
      output_dir: {
        type: "string",
        description: "Where to write comparison.json/.html. Defaults under the podcli output directory.",
      },
    },
    required: ["file_path", "engine_a", "engine_b"],
  },
};

export async function handleCompareEngines(input: CompareEnginesInput): Promise<string> {
  const result = await executor.execute<{
    overall_disagreement: number;
    both_empty_window_count: number;
    json_path?: string;
    html_path?: string;
  }>("compare_engines", {
    file_path: input.file_path,
    engine_a: input.engine_a,
    engine_b: input.engine_b,
    start_seconds: input.start_seconds,
    duration_seconds: input.duration_seconds,
    window_seconds: input.window_seconds,
    model_size: input.model_size,
    language: input.language,
    output_dir: input.output_dir,
  });

  if (!result.data) {
    throw new Error("Engine comparison returned no data");
  }

  return JSON.stringify({
    overall_disagreement: result.data.overall_disagreement,
    both_empty_window_count: result.data.both_empty_window_count,
    json_path: result.data.json_path,
    html_path: result.data.html_path,
    note: "disagreement between the two engines' output, not accuracy against a transcript",
  });
}
