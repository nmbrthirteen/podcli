import { homedir } from "os";
import { join, dirname } from "path";
import { fileURLToPath } from "url";

const __dirname = dirname(fileURLToPath(import.meta.url));

const home =
  process.env.PODCLI_HOME || join(homedir(), ".podcli");

export const paths = {
  home,
  cache: join(home, "cache"),
  transcripts: join(home, "cache", "transcripts"),
  working: join(home, "working"),
  output: join(home, "output"),
  logs: join(home, "logs"),
  pythonBackend: join(__dirname, "..", "..", "backend", "main.py"),
  pythonPath: process.env.PYTHON_PATH || "python3",
  ffmpegPath: process.env.FFMPEG_PATH || "ffmpeg",
  ffprobePath: process.env.FFPROBE_PATH || "ffprobe",
};
