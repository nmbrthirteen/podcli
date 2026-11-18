/**
 * Whether a render should cut pauses and filler words out of the clip body.
 * An explicit request always wins. The session default is ignored for topic
 * clips, so a Studio toggle left at its default does not put micro-cuts into a
 * long segment. Undefined lets the renderer apply the format's own default.
 */
export function resolveCleanFillers(
  explicit: boolean | undefined,
  sessionDefault: boolean | undefined,
  format?: string,
): boolean | undefined {
  if (explicit !== undefined) return explicit;

  if (format === "topic") return undefined;

  return sessionDefault ?? true;
}
