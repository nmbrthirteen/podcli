import { join, dirname } from "path";
import { fileURLToPath } from "url";
import { existsSync } from "fs";

const __dirname = dirname(fileURLToPath(import.meta.url));

// Project root (next to package.json)
const projectRoot = join(__dirname, "..", "..");

// Visible data/ directory for outputs and user-facing data
const dataDir = process.env.PODCLI_DATA || join(projectRoot, "data");

// Internal .podcli directory for caches, state, and config
const home = process.env.PODCLI_HOME || join(projectRoot, ".podcli");

// Auto-detect venv python (same logic as the bash wrapper)
function detectPython(): string {
  if (process.env.PYTHON_PATH) return process.env.PYTHON_PATH;
  const venvPython = join(projectRoot, "venv", "bin", "python3");
  if (existsSync(venvPython)) return venvPython;
  return "python3";
}

export const paths = {
  home,
  projectRoot,
  cache: join(dataDir, "cache"),
  transcripts: join(dataDir, "cache", "transcripts"),
  working: join(dataDir, "working"),
  output: join(dataDir, "output"),
  logs: join(dataDir, "logs"),
  assets: join(home, "assets"),
  assetsRegistry: join(home, "assets", "registry.json"),
  history: join(home, "history"),
  clipsHistory: join(home, "history", "clips.json"),
  knowledge: join(home, "knowledge"),
  uiState: join(home, "ui-state.json"),
  pythonBackend: join(__dirname, "..", "..", "backend", "main.py"),
  pythonPath: detectPython(),
  ffmpegPath: process.env.FFMPEG_PATH || "ffmpeg",
  ffprobePath: process.env.FFPROBE_PATH || "ffprobe",
};
