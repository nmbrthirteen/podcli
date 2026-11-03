import path from "path";

/** Resolve `filename` under `base`, or null when it escapes `base`. */
export function safePath(base: string, filename: string): string | null {
  const root = path.resolve(base);
  const resolved = path.resolve(base, filename);

  // Compare on a path boundary so "/data/output-evil" isn't accepted as inside "/data/output".
  if (resolved !== root && !resolved.startsWith(root + path.sep)) return null;

  return resolved;
}
