/**
 * Format seconds as an SRT timestamp (HH:MM:SS,mmm).
 *
 * Rounds to whole milliseconds once, up front, then derives h/m/sec/ms by
 * integer division. Rounding the seconds and milliseconds components
 * separately lets a value like 1.9996 round seconds down to 1 and ms up to
 * 1000, emitting the invalid "01,1000" instead of carrying into "02,000".
 */
export function formatSrtTime(s: number): string {
  let totalMs = Math.round(s * 1000);
  const h = Math.floor(totalMs / 3_600_000);
  totalMs -= h * 3_600_000;
  const m = Math.floor(totalMs / 60_000);
  totalMs -= m * 60_000;
  const sec = Math.floor(totalMs / 1000);
  const ms = totalMs - sec * 1000;
  return `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}:${String(sec).padStart(2, "0")},${String(ms).padStart(3, "0")}`;
}

/** Format seconds as a WEBVTT timestamp (HH:MM:SS.mmm). */
export function formatVttTime(s: number): string {
  return formatSrtTime(s).replace(",", ".");
}
