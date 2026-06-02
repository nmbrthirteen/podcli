import { join, dirname, resolve, isAbsolute } from "path";
import { fileURLToPath } from "url";
import { existsSync, readFileSync } from "fs";

const __dirname = dirname(fileURLToPath(import.meta.url));

const projectRoot = resolve(__dirname, "..", "..");
const homeMarker = join(projectRoot, ".podcli-home");
const dataDir = resolve(process.env.PODCLI_DATA || join(projectRoot, "data"));

function resolveHome(): string {
  if (process.env.PODCLI_HOME) {
    return resolve(process.env.PODCLI_HOME);
  }
  if (existsSync(homeMarker)) {
    try {
      const marker = readFileSync(homeMarker, "utf-8").trim();
      if (marker) {
        return isAbsolute(marker) ? resolve(marker) : resolve(projectRoot, marker);
      }
    } catch {
      // Unreadable marker — fall through to the default home.
    }
  }
  return resolve(projectRoot, ".podcli");
}

const home = resolveHome();

function detectPython(): string {
  if (process.env.PYTHON_PATH) return process.env.PYTHON_PATH;
  const venvPython = join(projectRoot, "venv", "bin", "python3");
  if (existsSync(venvPython)) return venvPython;
  return "python3";
}

export const paths = {
  home,
  projectRoot,
  homeMarker,
  dataDir,
  cache: join(dataDir, "cache"),
  transcripts: join(dataDir, "cache", "transcripts"),
  packed: join(home, "packed"),
  working: join(dataDir, "working"),
  output: join(dataDir, "output"),
  logs: join(dataDir, "logs"),
  assets: join(home, "assets"),
  assetsRegistry: join(home, "assets", "registry.json"),
  history: join(home, "history"),
  clipsHistory: join(home, "history", "clips.json"),
  knowledge: join(home, "knowledge"),
  uiState: join(home, "ui-state.json"),
  corrections: join(home, "corrections.json"),
  thumbnailConfig: join(home, "thumbnail-config.json"),
  integrations: join(home, "integrations.json"),
  pythonBackend: join(projectRoot, "backend", "main.py"),
  pythonPath: detectPython(),
  ffmpegPath: process.env.FFMPEG_PATH || "ffmpeg",
  ffprobePath: process.env.FFPROBE_PATH || "ffprobe",
};
