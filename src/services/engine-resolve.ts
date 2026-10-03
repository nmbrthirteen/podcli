import { PythonExecutor } from "./python-executor.js";

/**
 * Predicts which engine backend/services/transcription.py's transcribe_file
 * will actually use for an unset/auto request, without transcribing anything.
 *
 * The transcript cache is keyed by engine. Reading the cache with the raw
 * request (e.g. undefined, meaning "whisper-py unless this install can't run
 * it") instead of what transcribe_file resolves to always misses on a native
 * install, because the write afterward lands under "whispercpp", the key
 * the read never looked at.
 */
/**
 * Whether the resolved engine can ever produce speaker labels. whisper.cpp
 * and omnilingual skip diarization unconditionally (see
 * backend/services/transcription.py's whispercpp/omnilingual branches). A
 * cache entry from either engine will never gain speaker labels no matter
 * how many times it's re-transcribed, so treating a missing-labels cache as
 * a retriable miss for them would re-transcribe on every single call.
 */
export function engineCanDiarize(engine: string | undefined): boolean {
  return engine !== "whispercpp" && engine !== "omnilingual";
}

export async function resolveTranscribeEngine(
  executor: PythonExecutor,
  engine: string | undefined,
  modelSize: string
): Promise<string> {
  try {
    const result = await executor.execute<{ engine: string }>("resolve_transcribe_engine", {
      engine,
      model_size: modelSize,
    });
    return result.data?.engine ?? engine ?? "whisper-py";
  } catch {
    // Resolution is a best-effort cache-key optimization; a failure here
    // should fall through to a normal (possibly-missed) cache lookup rather
    // than block transcription.
    return engine ?? "whisper-py";
  }
}
