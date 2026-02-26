import { join, dirname } from "path";
import { fileURLToPath } from "url";

const __dirname = dirname(fileURLToPath(import.meta.url));

// Project-local .podcli directory (next to package.json)
const projectRoot = join(__dirname, "..", "..");
const home =
  process.env.PODCLI_HOME || join(projectRoot, ".podcli");

export const paths = {
  home,
  cache: join(home, "cache"),
  transcripts: join(home, "cache", "transcripts"),
  working: join(home, "working"),
  output: join(home, "output"),
  logs: join(home, "logs"),
  assets: join(home, "assets"),
  assetsRegistry: join(home, "assets", "registry.json"),
  history: join(home, "history"),
  clipsHistory: join(home, "history", "clips.json"),
  knowledge: join(home, "knowledge"),
  pythonBackend: join(__dirname, "..", "..", "backend", "main.py"),
  pythonPath: process.env.PYTHON_PATH || "python3",
  ffmpegPath: process.env.FFMPEG_PATH || "ffmpeg",
  ffprobePath: process.env.FFPROBE_PATH || "ffprobe",
};
