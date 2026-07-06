import { readFileSync } from "fs";
import { dirname, resolve } from "path";
import { fileURLToPath } from "url";

export function podcliVersion(): string {
  const envVersion = process.env.PODCLI_VERSION?.trim();
  if (envVersion) return envVersion;

  try {
    const pkg = JSON.parse(readFileSync(resolve(dirname(fileURLToPath(import.meta.url)), "..", "package.json"), "utf-8"));
    if (typeof pkg.version === "string" && pkg.version.trim()) return pkg.version;
  } catch {
    // Local dev fallback; release builds are stamped through PODCLI_VERSION.
  }

  return "0.0.0-dev";
}
