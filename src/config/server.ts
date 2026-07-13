export function resolveWebServerPort(env: NodeJS.ProcessEnv = process.env): number {
  const raw = env.PODCLI_PORT || env.PORT;
  const parsed = raw ? parseInt(raw, 10) : NaN;
  return Number.isInteger(parsed) && parsed > 0 && parsed <= 65535 ? parsed : 3847;
}

export const webServerPort = resolveWebServerPort();
export const webServerUrl = `http://localhost:${webServerPort}`;
