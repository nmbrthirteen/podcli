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

// PODCLI_VERSION is only set when the Go launcher spawns this process (see
// cli/internal/engine/engine.go nodeEnv). A source checkout running `npm run
// ui` directly never has it, so its presence tells the two install types apart.
export function isLauncherInstall(): boolean {
  return !!process.env.PODCLI_VERSION?.trim();
}

/** The right command to start the Web UI for whichever install type is running. */
export function studioStartCommand(): string {
  return isLauncherInstall() ? "podcli studio" : "npm run ui";
}
